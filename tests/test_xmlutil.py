"""Testes da leitura do XML nacional da NFS-e.

Este modulo existe por causa de dois defeitos reais que ja custaram caro:

  1. `competencia_do_xml` chegou a devolver SEMPRE o mes corrente, porque o
     XPath usava `local-name()` (que o ElementTree nao suporta), a excecao era
     engolida e o codigo caia num fallback `datetime.now()`. Como a pasta de
     destino no Dominio e `<MMAAAA>`, todo documento ia para o mes errado.
  2. Mesmo com aquilo corrigido, ler `dhEmi` em vez de `dCompet` mandava as
     notas de virada de ano (dCompet 31/12, dhEmi 02/01) para o mes errado.

Os testes abaixo travam os dois comportamentos.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import xmlutil  # noqa: E402

NS = "http://www.sped.fazenda.gov.br/nfse"


def nfse(*, dcompet: str | None = None, dhemi: str | None = None,
         prest: str | None = None, toma: str | None = None,
         interm: str | None = None, emit: str | None = None) -> str:
    """Monta uma NFS-e minima, com namespace, como a API entrega."""
    partes = []
    if dcompet:
        partes.append(f"<dCompet>{dcompet}</dCompet>")
    if dhemi:
        partes.append(f"<dhEmi>{dhemi}</dhEmi>")
    if prest:
        partes.append(f"<prest><CNPJ>{prest}</CNPJ></prest>")
    if toma:
        partes.append(f"<toma><CNPJ>{toma}</CNPJ></toma>")
    if interm:
        partes.append(f"<interm><CNPJ>{interm}</CNPJ></interm>")
    if emit:
        partes.append(f"<emit><CNPJ>{emit}</CNPJ></emit>")
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<NFSe xmlns="{NS}"><infNFSe>{"".join(partes)}</infNFSe></NFSe>')


# ----------------------------------------------------------------- competencia

def test_dcompet_tem_prioridade_sobre_dhemi():
    """O caso da virada de ano: emitida em janeiro, competencia de dezembro."""
    xml = nfse(dcompet="2025-12-31", dhemi="2026-01-02T09:15:00-03:00")
    assert xmlutil.competencia_do_xml(xml) == "122025"


def test_usa_dhemi_quando_nao_ha_dcompet():
    xml = nfse(dhemi="2026-08-14T10:00:00-03:00")
    assert xmlutil.competencia_do_xml(xml) == "082026"


def test_cai_para_a_data_de_geracao_do_envelope():
    xml = nfse(prest="99900001000150")
    assert xmlutil.competencia_do_xml(xml, "2026-07-01T00:00:00Z") == "072026"


def test_sem_nenhuma_data_devolve_none_e_nao_o_mes_atual():
    """A regressao numero 1. None vira problema visivel; mes atual, nao."""
    assert xmlutil.competencia_do_xml(nfse(prest="99900001000150")) is None


def test_xml_invalido_devolve_none():
    assert xmlutil.competencia_do_xml("<nao> e xml valido") is None


@pytest.mark.parametrize("data,esperado", [
    ("2026-01-15", "012026"),
    ("2026-12-01T23:59:59-03:00", "122026"),
    ("2025-02-28", "022025"),
])
def test_formato_mmaaaa(data, esperado):
    assert xmlutil.competencia_do_xml(nfse(dcompet=data)) == esperado


def test_namespace_nao_atrapalha():
    """A leitura tem de ser agnostica ao namespace e ao prefixo."""
    xml = ('<?xml version="1.0"?>'
           f'<ns1:NFSe xmlns:ns1="{NS}"><ns1:infNFSe>'
           '<ns1:dCompet>2026-03-01</ns1:dCompet>'
           '</ns1:infNFSe></ns1:NFSe>')
    assert xmlutil.competencia_do_xml(xml) == "032026"


# ----------------------------------------------------------------------- papel

CNPJ = "99900001000150"
OUTRO = "99900002000102"


def test_empresa_como_prestadora_e_emitida():
    xml = nfse(dcompet="2026-08-01", prest=CNPJ, toma=OUTRO)
    assert xmlutil.papel_do_xml(xml, CNPJ) == xmlutil.PAPEL_EMITIDA


def test_empresa_como_tomadora_e_recebida():
    xml = nfse(dcompet="2026-08-01", prest=OUTRO, toma=CNPJ)
    assert xmlutil.papel_do_xml(xml, CNPJ) == xmlutil.PAPEL_RECEBIDA


def test_empresa_como_intermediaria():
    xml = nfse(dcompet="2026-08-01", prest=OUTRO, interm=CNPJ)
    assert xmlutil.papel_do_xml(xml, CNPJ) == xmlutil.PAPEL_INTERMEDIARIA


def test_cnpj_com_mascara_encontra_o_mesmo_documento():
    xml = nfse(dcompet="2026-08-01", prest=CNPJ)
    assert xmlutil.papel_do_xml(xml, "99.900.001/0001-50") == xmlutil.PAPEL_EMITIDA


def test_empresa_ausente_do_documento_fica_indefinida():
    xml = nfse(dcompet="2026-08-01", prest=OUTRO, toma="99900003000149")
    assert xmlutil.papel_do_xml(xml, CNPJ) == xmlutil.PAPEL_INDEFINIDO


def test_evento_sem_prest_nem_toma_fica_indefinido():
    """Eventos herdam o papel da NFS-e que referenciam, nao adivinham."""
    xml = ('<?xml version="1.0"?>'
           f'<evento xmlns="{NS}"><infEvento>'
           '<chNFSe>35260899900001000150000000000001</chNFSe>'
           '</infEvento></evento>')
    assert xmlutil.papel_do_xml(xml, CNPJ) == xmlutil.PAPEL_INDEFINIDO


def test_cnpj_vazio_nao_chuta_papel():
    xml = nfse(dcompet="2026-08-01", prest=CNPJ)
    assert xmlutil.papel_do_xml(xml, "") == xmlutil.PAPEL_INDEFINIDO


def test_fallback_pelo_emitente_do_cabecalho():
    xml = nfse(dcompet="2026-08-01", emit=CNPJ)
    assert xmlutil.papel_do_xml(xml, CNPJ) == xmlutil.PAPEL_EMITIDA
