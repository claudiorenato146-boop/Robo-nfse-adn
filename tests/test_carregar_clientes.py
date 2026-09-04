"""Testes da leitura do cadastro de clientes.

Cobrem, alem do basico, a mudanca feita na auditoria de 04/09/2026: a senha do
certificado pode vir do ambiente (SENHA_CERTIFICADO_PADRAO) quando a coluna
`senha_certificado` esta vazia. Isso permite que o CSV seja um cadastro sem
nenhum segredo dentro.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import orquestrador  # noqa: E402

CABECALHO = ("codigo_dominio;apelido;razao_social;cnpj;cnpj_raiz;pasta_saida;"
             "pasta_emitidos;pasta_recebidos;pasta_relatorios;"
             "arquivo_certificado;senha_certificado;situacao;ativo")


def escrever_csv(tmp_path: Path, *linhas: str) -> str:
    caminho = tmp_path / "clientes.csv"
    caminho.write_text("\n".join((CABECALHO, *linhas)) + "\n", encoding="utf-8-sig")
    return str(caminho)


def linha(codigo="11", cnpj="99900001000150", senha="", ativo="sim") -> str:
    return (f"{codigo};EXEMPLO;EMPRESA EXEMPLO LTDA;{cnpj};{cnpj[:8]};"
            f"p;p;p;;EXEMPLO.pfx;{senha};Ativa;{ativo}")


def test_le_uma_empresa(tmp_path):
    clientes = orquestrador.carregar_clientes(escrever_csv(tmp_path, linha()))
    assert len(clientes) == 1
    assert clientes[0].codigo_dominio == "11"
    assert clientes[0].cnpj == "99900001000150"


def test_cnpj_com_mascara_vira_so_digitos(tmp_path):
    csv = escrever_csv(tmp_path, linha(cnpj="99.900.001/0001-50"))
    assert orquestrador.carregar_clientes(csv)[0].cnpj == "999000010001"[:12] + "50"


def test_empresa_inativa_e_ignorada(tmp_path):
    csv = escrever_csv(tmp_path, linha(codigo="11"), linha(codigo="22", ativo="nao"))
    assert [c.codigo_dominio for c in orquestrador.carregar_clientes(csv)] == ["11"]


def test_arquivo_inexistente_falha_com_mensagem_clara(tmp_path):
    with pytest.raises(FileNotFoundError, match="Cadastro de clientes"):
        orquestrador.carregar_clientes(str(tmp_path / "nao_existe.csv"))


def test_coluna_obrigatoria_faltando_falha_dizendo_qual(tmp_path):
    caminho = tmp_path / "clientes.csv"
    caminho.write_text("codigo_dominio;cnpj;arquivo_certificado\n11;99900001000150;a.pfx\n",
                       encoding="utf-8-sig")
    with pytest.raises(ValueError, match="senha_certificado"):
        orquestrador.carregar_clientes(str(caminho))


def test_selecao_por_codigo_respeita_a_ordem_pedida(tmp_path):
    csv = escrever_csv(tmp_path, linha(codigo="11"), linha(codigo="22", cnpj="99900002000102"),
                       linha(codigo="33", cnpj="99900003000149"))
    sel = orquestrador.carregar_clientes(csv, ["33", "11"])
    assert [c.codigo_dominio for c in sel] == ["33", "11"]


def test_codigo_inexistente_nao_passa_despercebido(tmp_path):
    csv = escrever_csv(tmp_path, linha(codigo="11"))
    with pytest.raises(ValueError):
        orquestrador.carregar_clientes(csv, ["11", "999"])


# ------------------------------------------------------- senha do certificado

def test_senha_vem_do_ambiente_quando_a_coluna_esta_vazia(tmp_path, monkeypatch):
    monkeypatch.setenv("SENHA_CERTIFICADO_PADRAO", "senha-do-ambiente")
    csv = escrever_csv(tmp_path, linha(senha=""))
    assert orquestrador.carregar_clientes(csv)[0].senha_certificado == "senha-do-ambiente"


def test_a_coluna_tem_prioridade_sobre_o_ambiente(tmp_path, monkeypatch):
    """Uma empresa pode ter senha propria sem que as outras percam a do .env."""
    monkeypatch.setenv("SENHA_CERTIFICADO_PADRAO", "senha-do-ambiente")
    csv = escrever_csv(tmp_path, linha(senha="senha-da-empresa"))
    assert orquestrador.carregar_clientes(csv)[0].senha_certificado == "senha-da-empresa"


def test_sem_coluna_e_sem_ambiente_a_senha_fica_vazia(tmp_path, monkeypatch):
    """Vazio, e nao um valor chutado: o erro aparece ao abrir o .pfx."""
    monkeypatch.delenv("SENHA_CERTIFICADO_PADRAO", raising=False)
    csv = escrever_csv(tmp_path, linha(senha=""))
    assert orquestrador.carregar_clientes(csv)[0].senha_certificado == ""


def test_o_csv_de_exemplo_do_repositorio_carrega(monkeypatch):
    """O exemplo versionado tem de continuar valido — e sem senha dentro."""
    monkeypatch.delenv("SENHA_CERTIFICADO_PADRAO", raising=False)
    exemplo = Path(__file__).resolve().parents[1] / "data" / "clientes.exemplo.csv"
    clientes = orquestrador.carregar_clientes(str(exemplo))
    assert len(clientes) == 6
    assert all(c.senha_certificado == "" for c in clientes)
