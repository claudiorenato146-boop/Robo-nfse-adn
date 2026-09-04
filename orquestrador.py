"""
orquestrador.py — Loop principal do robo NFS-e ADN.

Por empresa:
  1. Resolve e valida o certificado (arquivo nomeado na planilha, pasta unica).
  2. Sincroniza a fila de NSU ate o fim (incremental: o ponteiro fica no banco).
  3. Classifica cada documento por competencia, papel (emitida/recebida) e
     situacao (cancelada ou nao).
  4. Entrega, para a competencia pedida:
       - 4 ZIPs nas raizes EMITIDAS e RECEBIDAS
       - 2 planilhas de 38 colunas na raiz RELATORIOS
  5. Devolve o resultado para a planilha mensal consolidada.

A pasta do cliente NUNCA e criada — e localizada pelo prefixo do codigo. Se
faltar, vira aviso na planilha e a empresa segue sem aquela entrega.
"""

from __future__ import annotations

import csv
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import banco as db
import nfse_dados
import planilhas
import storage
import xmlutil
from adn_client import (
    AdnClient, FilaVazia, ErroTransitorio, ErroPermanente, ErroAutenticacao,
    validade_certificado,
)
from config import Config

logger = logging.getLogger(__name__)

EXTENSOES_CERT = (".pfx", ".p12")


@dataclass
class Cliente:
    codigo_dominio: str
    apelido: str
    razao_social: str
    cnpj: str
    cnpj_raiz: str
    arquivo_certificado: str
    senha_certificado: str


@dataclass
class ResultadoEmpresa:
    cliente: Cliente
    status: str = "OK"          # OK | SEM_CERTIFICADO | ERRO | PARCIAL
    mensagem: str = ""
    baixados_agora: int = 0
    emitidas: int = 0
    recebidas: int = 0
    emitidas_canceladas: int = 0
    recebidas_canceladas: int = 0
    avisos: list = field(default_factory=list)
    cert_vence: object = None      # datetime da validade, para a planilha mensal

    @property
    def total(self) -> int:
        return (self.emitidas + self.recebidas
                + self.emitidas_canceladas + self.recebidas_canceladas)

    def observacao(self) -> str:
        # Emitidas e recebidas sao fluxos INDEPENDENTES. Nao ter nota num deles
        # (ou nos dois) e "sem movimento", nao erro.
        def sit(qtd):
            if self.status not in ("OK", "PARCIAL"):
                return "ERRO"
            return "OK" if qtd else "SEM MOV"

        emi = sit(self.emitidas + self.emitidas_canceladas)
        rec = sit(self.recebidas + self.recebidas_canceladas)
        txt = (f"Emitidos: {emi} ({self.emitidas} XML, {self.emitidas_canceladas} canceladas); "
               f"Recebidos: {rec} ({self.recebidas} XML, {self.recebidas_canceladas} canceladas)")
        if self.status != "OK":
            txt = f"[{self.status}] {self.mensagem[:90]} | " + txt
        if self.avisos:
            txt += " | AVISO: " + "; ".join(self.avisos)
        return txt


# --------------------------------------------------------------------------
# Carteira
# --------------------------------------------------------------------------

def carregar_clientes(caminho_csv: str,
                      codigos: Optional[list[str]] = None) -> list[Cliente]:
    caminho = Path(caminho_csv)
    if not caminho.exists():
        raise FileNotFoundError(f"Cadastro de clientes nao encontrado: {caminho}")

    obrigatorias = {"codigo_dominio", "cnpj", "arquivo_certificado", "senha_certificado"}
    todos: list[Cliente] = []

    # Quando a coluna `senha_certificado` vem vazia, a senha sai do ambiente.
    # Assim o CSV pode ficar sem nenhuma senha dentro: e um cadastro, nao um
    # cofre. Preencher a coluna continua funcionando (uma senha por empresa),
    # mas o caminho recomendado e deixar a coluna vazia e definir
    # SENHA_CERTIFICADO_PADRAO no .env, que nao vai para o repositorio.
    senha_do_ambiente = (os.getenv("SENHA_CERTIFICADO_PADRAO") or "").strip()

    with open(caminho, newline="", encoding="utf-8-sig") as f:
        amostra = f.read(4096)
        f.seek(0)
        sep = ";" if amostra.count(";") >= amostra.count(",") else ","
        leitor = csv.DictReader(f, delimiter=sep)
        faltando = obrigatorias - set(leitor.fieldnames or [])
        if faltando:
            raise ValueError(
                f"clientes.csv sem a(s) coluna(s): {', '.join(sorted(faltando))}. "
                f"Encontradas: {', '.join(leitor.fieldnames or [])}"
            )
        for linha in leitor:
            if (linha.get("ativo") or "sim").strip().lower() not in ("sim", "s", "true", "1"):
                continue
            cod = (linha["codigo_dominio"] or "").strip()
            if not cod:
                continue
            todos.append(Cliente(
                codigo_dominio=cod,
                apelido=(linha.get("apelido") or "").strip(),
                razao_social=(linha.get("razao_social") or "").strip(),
                cnpj="".join(c for c in (linha["cnpj"] or "") if c.isdigit()),
                cnpj_raiz=(linha.get("cnpj_raiz") or "").strip(),
                arquivo_certificado=(linha["arquivo_certificado"] or "").strip(),
                senha_certificado=((linha["senha_certificado"] or "").strip()
                                   or senha_do_ambiente),
            ))

    if not codigos:
        logger.info("[CARTEIRA] %d empresa(s).", len(todos))
        return todos

    por_codigo = {c.codigo_dominio: c for c in todos}
    sel, faltantes = [], []
    for cod in codigos:
        cod = cod.strip()
        if not cod:
            continue
        (sel.append(por_codigo[cod]) if cod in por_codigo else faltantes.append(cod))
    if faltantes:
        raise ValueError(f"Codigo(s) nao encontrado(s): {', '.join(faltantes)}")
    logger.info("[CARTEIRA] %d selecionada(s): %s", len(sel),
                ", ".join(c.codigo_dominio for c in sel))
    return sel


def resolver_certificado(certificados_dir: str, cliente: Cliente) -> str:
    """
    Caminho do .pfx/.p12 da empresa, dentro da pasta unica de certificados.
    Aceita o nome com ou sem extensao, e as duas extensoes de certificado.
    """
    pasta = Path(certificados_dir)
    if not pasta.is_dir():
        raise FileNotFoundError(f"Pasta de certificados nao existe: {pasta}")

    nome = cliente.arquivo_certificado
    if not nome or nome.strip().upper().startswith(("NAO TEM", "NÃO TEM")):
        raise FileNotFoundError("Certificado nao informado na planilha")

    candidatos = [pasta / nome]
    if not Path(nome).suffix:
        candidatos += [pasta / (nome + ext) for ext in EXTENSOES_CERT]

    for c in candidatos:
        if c.is_file():
            return str(c)

    # ultimo recurso: procura sem diferenciar maiuscula/minuscula
    alvo = nome.lower()
    for p in pasta.iterdir():
        if p.is_file() and (p.name.lower() == alvo or p.stem.lower() == alvo):
            return str(p)

    raise FileNotFoundError(f"Certificado nao encontrado: {pasta / nome}")


# --------------------------------------------------------------------------
# Sincronizacao
# --------------------------------------------------------------------------

def _guardar_documento(conn, doc: dict, cliente: Cliente, config: Config) -> bool:
    chave = doc.get("ChaveAcesso") or ""
    tipo_doc = doc.get("TipoDocumento") or "NFSE"
    tipo_evento = doc.get("TipoEvento")
    b64 = doc.get("ArquivoXml")
    nsu = int(doc.get("NSU") or 0)

    if not b64 or db.documento_existe(conn, chave, tipo_doc, tipo_evento):
        return False

    xml = storage.descompactar_xml(b64)
    competencia = xmlutil.competencia_do_xml(xml, data_hora_geracao=doc.get("DataHoraGeracao"))
    if competencia is None:
        competencia = "SEM_COMPETENCIA"
        logger.warning("[DOC] NSU %d sem data identificavel", nsu)

    papel = xmlutil.papel_do_xml(xml, cliente.cnpj) if tipo_doc == "NFSE" else None

    caminho = storage.salvar_staging(
        config.staging_dir, cliente.cnpj, competencia,
        storage.nome_arquivo_xml(chave, tipo_doc, tipo_evento), xml,
    )
    db.registrar_documento(conn, cliente.cnpj, nsu, chave, tipo_doc, tipo_evento,
                           competencia, papel, caminho)
    return True


def sincronizar(cliente: Cliente, conn, config: Config, execucao_id: int,
                client: AdnClient) -> int:
    ambiente = config.ambiente_esperado
    nsu = max(0, db.obter_ultimo_nsu(conn, cliente.cnpj, ambiente))
    novos = 0

    while True:
        try:
            resposta = client.consultar_dfe(nsu=nsu, cnpj=cliente.cnpj)
        except FilaVazia:
            logger.info("[%s] Fila sincronizada (NSU %d).", cliente.codigo_dominio, nsu)
            break

        if resposta.status_processamento == "REJEICAO":
            detalhe = "; ".join(f"{e.get('Codigo')} {e.get('Descricao')}" for e in resposta.erros)
            db.registrar_erro(conn, execucao_id, cliente.cnpj, nsu, "REJEICAO", detalhe)
            raise ErroPermanente(f"REJEICAO da API: {detalhe}")

        lote = sorted(resposta.lote_dfe, key=lambda d: int(d.get("NSU") or 0))
        maior = nsu
        for doc in lote:
            if _guardar_documento(conn, doc, cliente, config):
                novos += 1
            maior = max(maior, int(doc.get("NSU") or 0))

        if maior + 1 <= nsu:
            logger.warning("[%s] NSU nao avancou (%d).", cliente.codigo_dominio, nsu)
            break

        db.salvar_nsu(conn, cliente.cnpj, ambiente, maior)
        nsu = maior + 1
        logger.info("[%s] Lote ate NSU %d | %d novo(s).", cliente.codigo_dominio, maior, novos)
        if config.intervalo_requisicoes > 0:
            time.sleep(config.intervalo_requisicoes)

    return novos


# --------------------------------------------------------------------------
# Classificacao
# --------------------------------------------------------------------------

def classificar(conn, cliente: Cliente, competencia: str) -> dict[str, list]:
    """
    Separa os documentos da competencia nas 4 categorias.
    Cada item e (caminho_do_xml, chave, e_evento).
    """
    canceladas = db.chaves_canceladas(conn, cliente.cnpj)
    papeis = db.papel_por_chave(conn, cliente.cnpj)
    grupos: dict[str, list] = {c: [] for c in storage.CATEGORIAS}

    for linha in db.documentos_da_competencia(conn, cliente.cnpj, competencia):
        caminho = linha["caminho_staging"]
        if not caminho or not Path(caminho).is_file():
            continue
        chave = linha["chave_acesso"]
        papel = linha["papel"] or papeis.get(chave) or xmlutil.PAPEL_EMITIDA
        base = "recebidas" if papel == xmlutil.PAPEL_RECEBIDA else "emitidas"
        categoria = f"{base}_canceladas" if chave in canceladas else base
        grupos[categoria].append(
            (Path(caminho), chave, linha["tipo_documento"] == "EVENTO")
        )
    return grupos


def _linhas_planilha(grupos: dict[str, list], base: str, contraparte: str) -> list[list]:
    """Linhas das notas (sem eventos) das categorias normal + cancelada."""
    linhas = []
    for cat, cancelada in ((base, False), (f"{base}_canceladas", True)):
        for caminho, chave, e_evento in grupos.get(cat, []):
            if e_evento:
                continue
            try:
                xml = caminho.read_text(encoding="utf-8")
            except OSError:
                continue
            linhas.append(nfse_dados.linha_planilha(xml, chave, contraparte, cancelada))
    linhas.sort(key=lambda l: (str(l[2]), str(l[1])))
    return linhas


# --------------------------------------------------------------------------
# Empresa
# --------------------------------------------------------------------------

def processar_empresa(cliente: Cliente, conn, config: Config, execucao_id: int,
                      competencia: str) -> ResultadoEmpresa:
    r = ResultadoEmpresa(cliente=cliente)

    # 1. Certificado
    try:
        caminho_pfx = resolver_certificado(config.certificados_dir, cliente)
        _, vence, _ = validade_certificado(caminho_pfx, cliente.senha_certificado)
        if vence < datetime.now(timezone.utc):
            raise ValueError(f"certificado vencido em {vence:%d/%m/%Y}")
        r.cert_vence = vence
        logger.info("[%s] Certificado ok, vence %s", cliente.codigo_dominio,
                    vence.strftime("%d/%m/%Y"))
    except Exception as exc:
        r.status, r.mensagem = "SEM_CERTIFICADO", str(exc)
        db.registrar_erro(conn, execucao_id, cliente.cnpj, None, "CERTIFICADO", str(exc))
        logger.error("[%s] %s", cliente.codigo_dominio, exc)
        return r

    # 2. Sincronizar
    try:
        with AdnClient(url_base=config.adn_url_base, caminho_pfx=caminho_pfx,
                       senha_pfx=cliente.senha_certificado,
                       timeout=(config.timeout_connect, config.timeout_read),
                       max_tentativas=config.max_tentativas,
                       intervalo_requisicoes=config.intervalo_requisicoes,
                       param_cnpj=config.param_cnpj) as client:
            r.baixados_agora = sincronizar(cliente, conn, config, execucao_id, client)
    except (ErroAutenticacao, ErroPermanente, ErroTransitorio) as exc:
        r.status, r.mensagem = "ERRO", str(exc)
        db.registrar_erro(conn, execucao_id, cliente.cnpj, None, "API", str(exc))
        logger.error("[%s] %s", cliente.codigo_dominio, exc)
        return r

    # 3. ZIPs
    grupos = classificar(conn, cliente, competencia)
    arquivos = {cat: [c for c, _, _ in itens] for cat, itens in grupos.items()}
    contagem, avisos = storage.entregar_zips(
        config.raizes, cliente.codigo_dominio, cliente.cnpj, competencia, arquivos)
    r.emitidas = contagem["emitidas"]
    r.recebidas = contagem["recebidas"]
    r.emitidas_canceladas = contagem["emitidas_canceladas"]
    r.recebidas_canceladas = contagem["recebidas_canceladas"]
    r.avisos.extend(avisos)

    # 4. Planilhas de notas
    pasta_rel = storage.localizar_pasta_cliente(config.raizes["RELATORIOS"],
                                                cliente.codigo_dominio)
    if pasta_rel is None:
        r.avisos.append("sem pasta do cliente em RELATORIOS")
    else:
        destino = storage.pasta_competencia(pasta_rel, competencia)
        try:
            planilhas.gravar_notas(
                str(destino / f"nfses_emitidas_{cliente.cnpj}.xlsx"),
                nfse_dados.COLUNAS_EMITIDAS,
                _linhas_planilha(grupos, "emitidas", "toma"), "NFS-e Emitidas")
            planilhas.gravar_notas(
                str(destino / f"nfses_recebidas_{cliente.cnpj}.xlsx"),
                nfse_dados.COLUNAS_RECEBIDAS,
                _linhas_planilha(grupos, "recebidas", "emit"), "NFS-e Recebidas")
        except Exception as exc:
            r.avisos.append(f"falha ao gravar planilha: {exc}")
            logger.exception("[%s] planilha: %s", cliente.codigo_dominio, exc)

    if r.avisos and r.status == "OK":
        r.status = "PARCIAL"
    return r
