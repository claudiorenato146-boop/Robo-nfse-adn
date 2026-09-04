"""
xmlutil.py — Leitura do XML da NFS-e Nacional (SefinNacional).

Todo o XML do ADN vem com namespace http://www.sped.fazenda.gov.br/nfse.
O ElementTree do Python NAO suporta local-name() no XPath — tentar usar isso
levanta SyntaxError. Por isso a busca aqui e feita percorrendo a arvore e
comparando o nome da tag sem o namespace.

Regras de negocio implementadas:
  - Competencia = dCompet (competencia fiscal declarada), NAO dhEmi.
    Sao diferentes em ~14,5% das notas reais (nota de competencia 31/12
    emitida em 02/01). Cair para dhEmi/dhEvento so quando dCompet nao existe.
  - Papel = comparacao do CNPJ/CPF da empresa contra prest (prestador) e
    toma (tomador). Emitida = a empresa prestou o servico.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Optional

# Eventos que efetivamente cancelam a NFS-e.
EVENTOS_CANCELAMENTO = {
    "CANCELAMENTO",
    "CANCELAMENTO_POR_SUBSTITUICAO",
    "CANCELAMENTO_DEFERIDO_ANALISE_FISCAL",
    "CANCELAMENTO_POR_OFICIO",
}

PAPEL_EMITIDA = "EMITIDA"
PAPEL_RECEBIDA = "RECEBIDA"
PAPEL_INTERMEDIARIA = "INTERMEDIARIA"
PAPEL_INDEFINIDO = "INDEFINIDO"


def _local(tag: str) -> str:
    """Remove o namespace: '{uri}dCompet' -> 'dCompet'."""
    return tag.rsplit("}", 1)[-1]


def parse(xml: str | bytes) -> Optional[ET.Element]:
    """Parseia o XML. Devolve None se nao for XML valido."""
    try:
        if isinstance(xml, str):
            xml = xml.encode("utf-8")
        return ET.fromstring(xml)
    except ET.ParseError:
        return None


def achar(root: ET.Element, nome: str) -> Optional[ET.Element]:
    """Primeiro elemento cujo nome local seja `nome`. Ignora namespace."""
    if _local(root.tag) == nome:
        return root
    for el in root.iter():
        if _local(el.tag) == nome:
            return el
    return None


def texto(root: ET.Element, nome: str) -> Optional[str]:
    """Texto do primeiro elemento com esse nome local, ou None."""
    el = achar(root, nome)
    if el is not None and el.text and el.text.strip():
        return el.text.strip()
    return None


def _mmyyyy(valor: str) -> Optional[str]:
    """'2026-08-01T10:00:00-03:00' ou '2026-08-01' -> '082026'."""
    m = re.match(r"^(\d{4})-(\d{2})", valor.strip())
    if not m:
        return None
    return f"{m.group(2)}{m.group(1)}"


def competencia_do_xml(xml: str | bytes,
                       data_hora_geracao: Optional[str] = None) -> Optional[str]:
    """
    Competencia MMYYYY do documento.

    Ordem: dCompet (a competencia fiscal de verdade) -> dhEmi -> dhEvento ->
    dhProc -> DataHoraGeracao do envelope da API.

    Devolve None quando nao da para determinar. NUNCA cai para o mes atual:
    um documento sem data conhecida precisa aparecer como problema, nao ser
    arquivado silenciosamente no mes em que o robo rodou.
    """
    root = parse(xml)
    if root is not None:
        for tag in ("dCompet", "dhEmi", "dhEvento", "dhProc"):
            valor = texto(root, tag)
            if valor:
                comp = _mmyyyy(valor)
                if comp:
                    return comp

    if data_hora_geracao:
        return _mmyyyy(data_hora_geracao)

    return None


def _documentos_da_parte(root: ET.Element, parte: str) -> set[str]:
    """CNPJ e CPF declarados dentro de <prest>, <toma> ou <interm>."""
    el = achar(root, parte)
    if el is None:
        return set()
    docs = set()
    for filho in el.iter():
        nome = _local(filho.tag)
        if nome in ("CNPJ", "CPF") and filho.text and filho.text.strip():
            docs.add(re.sub(r"\D", "", filho.text))
    return docs


def papel_do_xml(xml: str | bytes, cnpj_empresa: str) -> str:
    """
    Determina se a empresa figura como prestadora (EMITIDA) ou tomadora
    (RECEBIDA) no documento.

    Para eventos, que nao trazem prest/toma, devolve INDEFINIDO — o papel do
    evento deve ser herdado da NFS-e que ele referencia.
    """
    root = parse(xml)
    if root is None:
        return PAPEL_INDEFINIDO

    alvo = re.sub(r"\D", "", cnpj_empresa or "")
    if not alvo:
        return PAPEL_INDEFINIDO

    if alvo in _documentos_da_parte(root, "prest"):
        return PAPEL_EMITIDA
    if alvo in _documentos_da_parte(root, "toma"):
        return PAPEL_RECEBIDA
    if alvo in _documentos_da_parte(root, "interm"):
        return PAPEL_INTERMEDIARIA

    # Fallback: emitente no cabecalho da NFS-e (infNFSe/emit).
    emit = achar(root, "emit")
    if emit is not None:
        for filho in emit.iter():
            if _local(filho.tag) in ("CNPJ", "CPF") and filho.text:
                if re.sub(r"\D", "", filho.text) == alvo:
                    return PAPEL_EMITIDA

    return PAPEL_INDEFINIDO


def numero_nfse(xml: str | bytes) -> Optional[str]:
    root = parse(xml)
    return texto(root, "nNFSe") if root is not None else None


def valor_servico(xml: str | bytes) -> Optional[str]:
    """vServ do DPS; cai para vLiq da NFS-e."""
    root = parse(xml)
    if root is None:
        return None
    return texto(root, "vServ") or texto(root, "vLiq")
