"""
nfse_dados.py — Extrai do XML da NFS-e Nacional as 38 colunas da planilha de
conferencia, no MESMO layout que o robo antigo ja gera.

Os cabecalhos sao reproduzidos literalmente, inclusive com os erros de
digitacao do modelo original (`Cpd Municipio Interm`, `Num Cep Intern`,
`Vlr Sev.`). Mudar isso quebraria quem consome a planilha hoje.

A unica diferenca entre a planilha de emitidas e a de recebidas e o bloco da
contraparte: nas emitidas e o TOMADOR (`Toma`), nas recebidas e o EMITENTE
(`Emit`).
"""

from __future__ import annotations

import csv
import os
import re
from typing import Optional

import xmlutil

# Nome do municipio nao vem no XML (so o codigo IBGE). Tabela carregada de
# data/municipios.csv, baixada da API de localidades do IBGE.
_MUNICIPIOS: dict[str, tuple[str, str]] = {}


def carregar_municipios(caminho: str) -> int:
    global _MUNICIPIOS
    _MUNICIPIOS = {}
    if not os.path.exists(caminho):
        return 0
    with open(caminho, encoding="utf-8", newline="") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            _MUNICIPIOS[linha["codigo_ibge"].strip()] = (
                linha["municipio"].strip().upper(), linha["uf"].strip().upper()
            )
    return len(_MUNICIPIOS)


def municipio(codigo_ibge: Optional[str]) -> tuple[str, str]:
    """(NOME, UF) a partir do codigo IBGE. ('', '') quando desconhecido."""
    if not codigo_ibge:
        return "", ""
    return _MUNICIPIOS.get(str(codigo_ibge).strip(), ("", ""))


COLUNAS_EMITIDAS = [
    "Id Nfse", "Nº", "Data",
    "Im Toma", "Cpf Toma", "Cnpj Toma", "Nome Toma", "Des Endereco Toma",
    "Nro Endereco Toma", "Des Complemento Toma", "Des Bairro Toma",
    "Cod Municipio Toma", "Des Municipio Toma", "Cod Uf Toma", "Num Cep Toma",
    "Im Interm", "Cpf Interm", "Cnpj Interm", "Nome Interm",
    "Des Endereco Interm", "Nro Endereco Interm", "Des Complemento Interm",
    "Des Bairro Interm", "Cpd Municipio Interm", "Des Municipio Interm",
    "Cod Uf Interm", "Num Cep Intern",
    "Vlr Deduc Bc Issqn", "Tip Beneficio Issqn", "Retenção", "Vlr Sev.",
    "BC ISSQN", "Aliq", "ISSQN", "Des Servicos",
    "Cod Trib Serv Nac", "Des Trib Serv Nac", "Status Apuracao Nfse",
]

# Mesma lista, com o bloco da contraparte renomeado de Toma para Emit.
COLUNAS_RECEBIDAS = [c.replace(" Toma", " Emit") for c in COLUNAS_EMITIDAS]

_RETENCAO = {"1": "Não Retido", "2": "Retido pelo Tomador",
             "3": "Retido pelo Intermediário"}


def _numero(valor: str, padrao=""):
    """
    Converte para float para o Excel tratar como numero, nao como texto.
    Devolve `padrao` quando a tag nao existe — o modelo do robo antigo deixa
    BC ISSQN e Aliq em branco quando nao ha, mas grava 0 no ISSQN.
    """
    v = (valor or "").strip().replace(",", ".")
    if not v:
        return padrao
    try:
        return float(v)
    except ValueError:
        return valor


def _texto(el, nome: str) -> str:
    if el is None:
        return ""
    return xmlutil.texto(el, nome) or ""


def _bloco_parte(root, parte: str) -> list:
    """
    Os 12 campos de identificacao/endereco de `toma`, `emit` ou `interm`,
    na ordem em que aparecem na planilha.
    """
    el = xmlutil.achar(root, parte) if root is not None else None
    if el is None:
        return [""] * 12

    cmun = _texto(el, "cMun")
    nome_mun, uf = municipio(cmun)
    # endExt (exterior) traz a UF/pais de outro jeito; se vier UF explicita, usa.
    uf = _texto(el, "UF") or uf

    return [
        _texto(el, "IM"),
        _texto(el, "CPF"),
        _texto(el, "CNPJ"),
        _texto(el, "xNome"),
        _texto(el, "xLgr"),
        _texto(el, "nro"),
        _texto(el, "xCpl"),
        _texto(el, "xBairro"),
        cmun,
        nome_mun,
        uf,
        _texto(el, "CEP"),
    ]


def linha_planilha(xml: str | bytes,
                   chave_acesso: str,
                   contraparte: str,
                   cancelada: bool) -> list:
    """
    Devolve a linha de 38 colunas.

    `contraparte` e 'toma' (planilha de emitidas) ou 'emit' (recebidas):
    é sempre a OUTRA ponta da operação em relação à empresa do robô.
    """
    root = xmlutil.parse(xml)
    if root is None:
        return [chave_acesso] + [""] * 36 + ["XML INVALIDO"]

    # Data: o modelo usa a data de emissao (dhEmi), nao a competencia.
    data = _texto(root, "dhEmi")
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", data)
    data_fmt = f"{m.group(1)}-{m.group(2)}-{m.group(3)} 00:00:00" if m else data

    ret = _RETENCAO.get(_texto(root, "tpRetISSQN"), "")

    return [
        chave_acesso,
        _texto(root, "nNFSe"),
        data_fmt,
        *_bloco_parte(root, contraparte),
        *_bloco_parte(root, "interm"),
        _numero(_texto(root, "vDedRed")),
        _texto(root, "tpBM"),
        ret,
        _numero(_texto(root, "vServ")),
        _numero(_texto(root, "vBC")),
        _numero(_texto(root, "pAliqAplic")),
        _numero(_texto(root, "vISSQN"), padrao=0),
        _texto(root, "xDescServ"),
        _texto(root, "cTribNac"),
        _texto(root, "xTribNac"),
        "CANCELADA" if cancelada else "APURAVEL",
    ]
