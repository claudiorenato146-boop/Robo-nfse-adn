"""
adn_client.py - Cliente HTTP mTLS da API do ADN (NFS-e Nacional).

Comportamento CONFIRMADO por execucao real em producao (20/08/2026, CNPJ da ON4,
3050 documentos baixados, paginacao encerrada pela propria API):

  - URL:  https://adn.nfse.gov.br/contribuintes/DFe/{NSU}?cnpjConsulta={CNPJ}&lote=true
  - O parametro e `cnpjConsulta` (conforme a Swagger oficial). Um prototipo
    anterior usava `cnpj`, baseado em log de 08/09/2025 - desatualizado.
    Configuravel por ADN_PARAM_CNPJ caso a API mude.
  - NSU vai como inteiro simples, sem zeros a esquerda.
  - Semantica INCLUSIVA: GET /DFe/N devolve documentos a partir do NSU N.
    Proximo ponteiro = maior NSU do lote + 1.
  - Lotes de ate ~50 documentos.
  - Fim da fila = HTTP 404 + StatusProcessamento NENHUM_DOCUMENTO_LOCALIZADO
    + codigo E2220. Nao e erro.
  - HTTP 429 (throttling) acontece: apareceu no NSU 51 sem intervalo entre
    chamadas. E TRANSITORIO - precisa aguardar e repetir o MESMO NSU.
    Classificar 429 como erro permanente faz a empresa ser pulada.
  - Autenticacao: mTLS puro. Nao existe token nem endpoint de login.
"""

from __future__ import annotations

import os
import time
import logging
import tempfile
from dataclasses import dataclass
from typing import Optional

import requests
from requests import Session, Response

logger = logging.getLogger(__name__)

# Status que valem nova tentativa (throttling e indisponibilidade temporaria).
STATUS_TRANSITORIOS = {429, 500, 502, 503, 504}


class ErroTransitorio(Exception):
    """Timeout, 5xx, 429, queda de rede - vale retentar."""


class ErroPermanente(Exception):
    """Erro de negocio - nao retentar."""


class ErroAutenticacao(ErroPermanente):
    """Falha de handshake TLS, certificado vencido ou senha errada."""


class FilaVazia(Exception):
    """HTTP 404 com E2220 - fim da fila do CNPJ. Nao e erro."""


@dataclass
class RespostaADN:
    status_processamento: str
    lote_dfe: list
    alertas: list
    erros: list
    tipo_ambiente: str
    versao_aplicativo: Optional[str]
    data_hora_processamento: str


def _pfx_para_pem_temp(caminho_pfx: str, senha: str) -> str:
    """
    Converte .pfx num PEM temporario unico (chave + certificado + cadeia),
    porque o `requests` nao le PKCS#12 diretamente.

    O arquivo contem a chave privada SEM senha. Fica no diretorio temporario
    do usuario e e apagado no fechamento do cliente.
    """
    from cryptography.hazmat.primitives.serialization import (
        Encoding, NoEncryption, PrivateFormat,
    )
    from cryptography.hazmat.primitives.serialization.pkcs12 import (
        load_key_and_certificates,
    )

    with open(caminho_pfx, "rb") as f:
        dados = f.read()

    chave, cert, cadeia = load_key_and_certificates(dados, senha.encode("utf-8"), None)
    if chave is None or cert is None:
        raise ValueError("PFX sem chave privada ou sem certificado.")

    fd, path_pem = tempfile.mkstemp(suffix=".pem", prefix="adn_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(chave.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
            f.write(cert.public_bytes(Encoding.PEM))
            for ca in (cadeia or []):
                f.write(ca.public_bytes(Encoding.PEM))
    except Exception:
        if os.path.exists(path_pem):
            os.remove(path_pem)
        raise

    return path_pem


def validade_certificado(caminho_pfx: str, senha: str):
    """(not_valid_before, not_valid_after, subject) - para checagem previa."""
    from cryptography.hazmat.primitives.serialization.pkcs12 import (
        load_key_and_certificates,
    )
    from datetime import timezone

    with open(caminho_pfx, "rb") as f:
        _, cert, _ = load_key_and_certificates(f.read(), senha.encode("utf-8"), None)

    # cryptography >= 42 depreciou not_valid_before/after em favor das versoes
    # _utc, que devolvem datetime com timezone. Padronizamos em UTC ciente.
    try:
        inicio, fim = cert.not_valid_before_utc, cert.not_valid_after_utc
    except AttributeError:
        inicio = cert.not_valid_before.replace(tzinfo=timezone.utc)
        fim = cert.not_valid_after.replace(tzinfo=timezone.utc)

    return inicio, fim, cert.subject.rfc4514_string()


class AdnClient:
    """
    Uso:
        with AdnClient(url, pfx, senha) as c:
            resposta = c.consultar_dfe(nsu=1, cnpj="12345678000195")
    """

    def __init__(self,
                 url_base: str,
                 caminho_pfx: str,
                 senha_pfx: str,
                 timeout: tuple[int, int] = (30, 60),
                 max_tentativas: int = 5,
                 intervalo_requisicoes: float = 1.0,
                 param_cnpj: str = "cnpjConsulta"):

        self._url_base = url_base.rstrip("/")
        self._timeout = timeout
        self._max_tentativas = max(1, max_tentativas)
        self._intervalo = intervalo_requisicoes
        self._param_cnpj = param_cnpj
        self._session: Optional[Session] = None

        logger.info("[ADN] Carregando certificado: %s", os.path.basename(caminho_pfx))
        try:
            self._path_pem = _pfx_para_pem_temp(caminho_pfx, senha_pfx)
        except Exception as exc:
            raise ErroAutenticacao(
                f"Falha ao abrir certificado '{caminho_pfx}': {exc}"
            ) from exc

    def __enter__(self) -> "AdnClient":
        session = Session()
        session.cert = self._path_pem
        session.verify = True
        session.headers.update({"Accept": "application/json"})
        self._session = session
        return self

    def __exit__(self, *_):
        self.fechar()

    def fechar(self):
        if self._session is not None:
            self._session.close()
            self._session = None
        if self._path_pem and os.path.exists(self._path_pem):
            try:
                os.remove(self._path_pem)
            except OSError:
                logger.warning("[ADN] Nao consegui remover o PEM temporario: %s",
                               self._path_pem)
        self._path_pem = None

    # ------------------------------------------------------------------

    def _espera_backoff(self, resp: Optional[Response], tentativa: int) -> float:
        """Respeita Retry-After quando numerico; senao backoff exponencial (teto 60s)."""
        if resp is not None:
            ra = resp.headers.get("Retry-After", "")
            if ra.strip().isdigit():
                return min(120.0, float(ra.strip()))
        return min(60.0, 2.0 ** tentativa)

    def _get(self, url: str, params: Optional[dict] = None) -> tuple[int, dict]:
        """
        GET com retry para erros transitorios (rede, 5xx e 429).
        Devolve (status_code, corpo_json). Levanta ErroTransitorio se esgotar.
        """
        ultima = "erro desconhecido"

        for tentativa in range(1, self._max_tentativas + 1):
            resp: Optional[Response] = None
            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)

                if resp.status_code not in STATUS_TRANSITORIOS:
                    try:
                        corpo = resp.json()
                    except ValueError:
                        corpo = {}
                    return resp.status_code, corpo

                ultima = f"HTTP {resp.status_code}"

            except requests.exceptions.SSLError as exc:
                # ATENCAO: SSLError aqui NAO significa certificado ruim.
                # Medido em producao (25/08/2026, 150 empresas): o ADN derruba o
                # handshake com RECORD_LAYER_FAILURE quando recebe muitas conexoes
                # mTLS seguidas do mesmo IP - 79% das empresas falharam assim, todas
                # com o certificado ja validado e no prazo, em rajadas instantaneas
                # logo apos uma empresa que baixou muito. E throttling de conexao,
                # o equivalente do HTTP 429 antes do HTTP. Tratar como transitorio.
                # O certificado ja foi aberto e conferido antes de chegar aqui.
                ultima = f"TLS derrubado pelo servidor: {str(exc)[:80]}"
            except requests.exceptions.Timeout as exc:
                ultima = f"Timeout: {exc}"
            except requests.exceptions.ConnectionError as exc:
                ultima = f"Conexao: {exc}"
            except requests.exceptions.RequestException as exc:
                ultima = f"Requisicao: {exc}"

            if tentativa < self._max_tentativas:
                espera = self._espera_backoff(resp, tentativa)
                logger.warning("[ADN] %s - tentativa %d/%d, aguardando %.0fs",
                               ultima, tentativa, self._max_tentativas, espera)
                time.sleep(espera)

        raise ErroTransitorio(
            f"Esgotadas {self._max_tentativas} tentativas: {ultima}"
        )

    # ------------------------------------------------------------------

    def consultar_dfe(self, nsu: int, cnpj: str) -> RespostaADN:
        """
        GET /DFe/{NSU}?cnpjConsulta={CNPJ}&lote=true

        Devolve os documentos a partir do NSU informado (inclusive).
        Levanta FilaVazia quando nao ha mais documentos.
        """
        url = f"{self._url_base}/DFe/{int(nsu)}"
        params = {self._param_cnpj: cnpj, "lote": "true"}

        status_code, corpo = self._get(url, params)

        status_proc = corpo.get("StatusProcessamento", "")
        codigos = [e.get("Codigo", "") for e in (corpo.get("Erros") or [])]

        # Fim da fila: a API declara explicitamente. Nao e erro.
        if status_proc == "NENHUM_DOCUMENTO_LOCALIZADO" or "E2220" in codigos:
            raise FilaVazia(f"CNPJ {cnpj} sem documentos a partir do NSU {nsu}")

        if status_code == 200:
            resposta = self._mapear(corpo)
            for alerta in resposta.alertas:
                logger.warning("[ADN-ALERTA] %s: %s",
                               alerta.get("Codigo"), alerta.get("Descricao"))
            # 200 com lote vazio tambem e fim de fila.
            if not resposta.lote_dfe:
                raise FilaVazia(f"CNPJ {cnpj}: lote vazio no NSU {nsu}")
            return resposta

        if status_code == 404:
            raise ErroPermanente(f"HTTP 404 inesperado (sem E2220): {corpo}")

        if 400 <= status_code < 500:
            raise ErroPermanente(f"HTTP {status_code}: {corpo}")

        raise ErroTransitorio(f"HTTP {status_code}: {corpo}")

    def consultar_eventos(self, chave_acesso: str) -> RespostaADN:
        """GET /NFSe/{ChaveAcesso}/Eventos"""
        url = f"{self._url_base}/NFSe/{chave_acesso}/Eventos"
        status_code, corpo = self._get(url)
        if status_code == 404:
            raise FilaVazia(f"Nenhum evento para a chave {chave_acesso}")
        if status_code == 200:
            return self._mapear(corpo)
        raise ErroPermanente(f"HTTP {status_code}: {corpo}")

    @staticmethod
    def _mapear(dados: dict) -> RespostaADN:
        return RespostaADN(
            status_processamento=dados.get("StatusProcessamento", ""),
            lote_dfe=dados.get("LoteDFe") or [],
            alertas=dados.get("Alertas") or [],
            erros=dados.get("Erros") or [],
            tipo_ambiente=dados.get("TipoAmbiente", ""),
            versao_aplicativo=dados.get("VersaoAplicativo"),
            data_hora_processamento=dados.get("DataHoraProcessamento", ""),
        )
