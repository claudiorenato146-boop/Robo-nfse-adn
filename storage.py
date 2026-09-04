"""
storage.py — Staging dos XMLs e entrega nos tres destinos.

Estrutura de saida (tres raizes distintas):

    {RAIZ_EMITIDAS}/{COD-APELIDO}/{MMAAAA}/xml_nfse_emitidas_{CNPJ}.zip
                                          /xml_nfse_emitidas_canceladas_{CNPJ}.zip
    {RAIZ_RECEBIDAS}/{COD-APELIDO}/{MMAAAA}/xml_nfse_recebidas_{CNPJ}.zip
                                           /xml_nfse_recebidas_canceladas_{CNPJ}.zip
    {RAIZ_RELATORIOS}/{COD-APELIDO}/{MMAAAA}/nfses_emitidas_{CNPJ}.xlsx
                                            /nfses_recebidas_{CNPJ}.xlsx

REGRAS (definidas pelo Claudio):
  - A pasta do CLIENTE nunca e criada. Ela e LOCALIZADA pelo prefixo do codigo
    (`^134-`), porque o nome pode estar desatualizado em relacao ao apelido do
    Dominio. Se nao existir, o robo avisa e pula aquela raiz.
  - A pasta da COMPETENCIA e criada quando nao existe, e ignorada quando existe.
  - XMLs soltos na raiz do ZIP. Nenhuma subpasta.
"""

from __future__ import annotations

import io
import gzip
import base64
import logging
import os
import re
import zipfile
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

CATEGORIAS = ("emitidas", "recebidas", "emitidas_canceladas", "recebidas_canceladas")

# categoria -> (raiz onde grava, nome do arquivo)
DESTINO = {
    "emitidas":             ("EMITIDAS",  "xml_nfse_emitidas_{cnpj}.zip"),
    "emitidas_canceladas":  ("EMITIDAS",  "xml_nfse_emitidas_canceladas_{cnpj}.zip"),
    "recebidas":            ("RECEBIDAS", "xml_nfse_recebidas_{cnpj}.zip"),
    "recebidas_canceladas": ("RECEBIDAS", "xml_nfse_recebidas_canceladas_{cnpj}.zip"),
}


def descompactar_xml(arquivo_xml_base64: str) -> str:
    """Campo ArquivoXml da API = base64(gzip(XML utf-8))."""
    try:
        dados = base64.b64decode(arquivo_xml_base64)
        with gzip.GzipFile(fileobj=io.BytesIO(dados)) as gz:
            return gz.read().decode("utf-8")
    except Exception as exc:
        raise ValueError(f"Falha ao descompactar ArquivoXml: {exc}") from exc


def nome_arquivo_xml(chave_acesso: str, tipo_documento: str,
                     tipo_evento: Optional[str]) -> str:
    base = chave_acesso or "SEM_CHAVE"
    return f"{base}_{tipo_evento}.xml" if tipo_evento else f"{base}.xml"


def salvar_staging(staging_dir: str, cnpj: str, competencia: str,
                   nome_arquivo: str, conteudo_xml: str) -> str:
    destino = Path(staging_dir) / cnpj / competencia
    destino.mkdir(parents=True, exist_ok=True)
    caminho = destino / nome_arquivo
    caminho.write_text(conteudo_xml, encoding="utf-8")
    return str(caminho)


# --------------------------------------------------------------------------
# Localizacao da pasta do cliente
# --------------------------------------------------------------------------

def localizar_pasta_cliente(raiz: str, codigo: str) -> Optional[Path]:
    """
    Procura a pasta que comeca com o codigo da empresa, aceitando as variacoes
    de espaco que existem no acervo (`25-NOME`, `17 - NOME`, `639- NOME`).

    Devolve None se nao houver — e o robo NAO cria.
    """
    base = Path(raiz)
    if not base.is_dir():
        return None
    padrao = re.compile(rf"^{re.escape(str(codigo))}\s*-", re.IGNORECASE)
    candidatas = [p for p in base.iterdir() if p.is_dir() and padrao.match(p.name)]
    if not candidatas:
        return None
    if len(candidatas) > 1:
        nomes = ", ".join(p.name for p in candidatas)
        logger.warning("[SAIDA] Codigo %s tem %d pastas em %s (%s). Usando a primeira.",
                       codigo, len(candidatas), base.name, nomes)
    return sorted(candidatas, key=lambda p: p.name)[0]


def pasta_competencia(pasta_cliente: Path, competencia: str) -> Path:
    """Cria a pasta MMAAAA se nao existir; se existir, apenas usa."""
    pasta = Path(pasta_cliente) / competencia
    if not pasta.exists():
        pasta.mkdir(parents=True)
        logger.info("[SAIDA] Pasta de competencia criada: %s", pasta)
    return pasta


# --------------------------------------------------------------------------
# ZIP
# --------------------------------------------------------------------------

def montar_zip(arquivos: Iterable[Path]) -> Optional[bytes]:
    """ZIP com os XMLs SOLTOS na raiz. None se nao houver arquivo."""
    existentes = [Path(a) for a in arquivos if Path(a).is_file()]
    if not existentes:
        return None
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for caminho in sorted(existentes, key=lambda p: p.name):
            zf.write(caminho, arcname=caminho.name)
    return buffer.getvalue()


def _mesmo_conteudo(caminho: Path, conteudo: bytes) -> bool:
    """
    Compara o que ja esta gravado com o que seria gravado.

    ZIP nao serve para comparacao byte a byte: cada montagem grava data/hora
    diferente no cabecalho, entao dois ZIPs com os MESMOS XMLs dao bytes
    diferentes. Por isso a comparacao e pelo conteudo de dentro: nome + CRC de
    cada arquivo. Para os demais tipos, compara os bytes mesmo.
    """
    if not caminho.is_file():
        return False
    try:
        if caminho.suffix.lower() == ".zip":
            def assinatura(fonte):
                with zipfile.ZipFile(fonte) as zf:
                    return sorted((i.filename, i.CRC) for i in zf.infolist())
            return assinatura(caminho) == assinatura(io.BytesIO(conteudo))
        return caminho.read_bytes() == conteudo
    except (OSError, zipfile.BadZipFile):
        return False


def gravar(caminho: Path, conteudo: bytes) -> str:
    """
    Grava de forma atomica (.tmp + replace).

    Se ja existir um arquivo com o MESMO nome e o MESMO conteudo, nao reescreve
    — devolve "identico". Arquivo que ja esta na pasta nao e erro nem motivo
    para regravar; so e substituido quando o conteudo mudou de verdade.
    Arquivos de outro nome (colocados a mao ou por outro processo) nunca sao
    tocados, porque o robo so escreve nos nomes que ele mesmo monta.
    """
    if _mesmo_conteudo(caminho, conteudo):
        return "identico"
    novo = not caminho.exists()
    tmp = Path(str(caminho) + ".tmp")
    tmp.write_bytes(conteudo)
    os.replace(tmp, caminho)
    return "novo" if novo else "atualizado"


def entregar_zips(raizes: dict[str, str], codigo: str, cnpj: str,
                  competencia: str,
                  arquivos_por_categoria: dict[str, list[Path]]) -> tuple[dict[str, int], list[str]]:
    """
    Grava os quatro ZIPs nas duas raizes de importacao.
    Devolve ({categoria: qtd}, [avisos]).
    """
    contagem = {c: 0 for c in CATEGORIAS}
    avisos: list[str] = []
    pastas: dict[str, Optional[Path]] = {}

    for nome_raiz in ("EMITIDAS", "RECEBIDAS"):
        pc = localizar_pasta_cliente(raizes[nome_raiz], codigo)
        pastas[nome_raiz] = pc
        if pc is None:
            avisos.append(f"sem pasta do cliente em {nome_raiz}")

    for categoria in CATEGORIAS:
        nome_raiz, modelo = DESTINO[categoria]
        pasta_cli = pastas[nome_raiz]
        arquivos = arquivos_por_categoria.get(categoria, [])
        conteudo = montar_zip(arquivos)

        if pasta_cli is None:
            if conteudo is not None:
                contagem[categoria] = len([a for a in arquivos if Path(a).is_file()])
            continue

        destino = pasta_competencia(pasta_cli, competencia) / modelo.format(cnpj=cnpj)

        if conteudo is None:
            if destino.exists():
                destino.unlink()
                logger.info("[SAIDA] ZIP obsoleto removido: %s", destino.name)
            continue

        situacao = gravar(destino, conteudo)
        contagem[categoria] = len([a for a in arquivos if Path(a).is_file()])
        if situacao == "identico":
            logger.info("[SAIDA] %s | %d XML | ja estava igual, mantido",
                        destino.name, contagem[categoria])
        else:
            logger.info("[SAIDA] %s | %d XML | %.1f KB | %s", destino.name,
                        contagem[categoria], len(conteudo) / 1024, situacao)

    return contagem, avisos
