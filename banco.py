"""
banco.py - Camada de persistência SQLite do protótipo NFS-e ADN.

Responsabilidades:
  - Criar/migrar o schema na primeira execução.
  - Persistir e consultar o último NSU processado por CNPJ.
  - Registrar documentos baixados (deduplicação por chave_acesso).
  - Registrar execuções e erros.

Toda operação que muta estado usa transação explícita.
"""

import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
-- Controle incremental do NSU por CNPJ e ambiente
CREATE TABLE IF NOT EXISTS controle_nsu (
    cnpj_consulta   TEXT    NOT NULL,
    ambiente        TEXT    NOT NULL DEFAULT 'PRODUCAO',
    ultimo_nsu      INTEGER NOT NULL DEFAULT 0,
    data_consulta   TEXT,
    PRIMARY KEY (cnpj_consulta, ambiente)
);

-- Documentos baixados - chave_acesso é globalmente única por tipo
CREATE TABLE IF NOT EXISTS documentos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cnpj_dono       TEXT    NOT NULL,
    nsu             INTEGER NOT NULL,
    chave_acesso    TEXT    NOT NULL,
    tipo_documento  TEXT    NOT NULL,
    tipo_evento     TEXT,
    competencia     TEXT    NOT NULL,   -- formato MMYYYY
    papel           TEXT,               -- EMITIDA | RECEBIDA | INTERMEDIARIA | INDEFINIDO
    caminho_staging TEXT,
    status          TEXT    NOT NULL DEFAULT 'BAIXADO',
    baixado_em      TEXT    NOT NULL,
    UNIQUE (chave_acesso, tipo_documento, tipo_evento)
);

-- Execuções do robô
CREATE TABLE IF NOT EXISTS execucoes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio          TEXT    NOT NULL,
    fim             TEXT,
    status          TEXT    NOT NULL DEFAULT 'RODANDO',
    empresas_ok     INTEGER DEFAULT 0,
    empresas_erro   INTEGER DEFAULT 0,
    docs_baixados   INTEGER DEFAULT 0
);

-- Erros por empresa/NSU para reprocessamento
CREATE TABLE IF NOT EXISTS erros (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    execucao_id     INTEGER,
    empresa_cnpj    TEXT,
    nsu             INTEGER,
    tipo            TEXT,
    mensagem        TEXT,
    criado_em       TEXT    NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Conexão
# ---------------------------------------------------------------------------

def conectar(db_path: str) -> sqlite3.Connection:
    """Abre conexão SQLite com WAL (melhor concorrência) e aplica schema."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrar(conn)
    conn.commit()
    logger.info("[DB] Conectado: %s", db_path)
    return conn


# ---------------------------------------------------------------------------
# NSU
# ---------------------------------------------------------------------------

def _migrar(conn: sqlite3.Connection) -> None:
    """Migracoes idempotentes para bancos criados por versoes anteriores."""
    colunas = {r["name"] for r in conn.execute("PRAGMA table_info(documentos)")}
    if "papel" not in colunas:
        conn.execute("ALTER TABLE documentos ADD COLUMN papel TEXT")
        logger.info("[DB] Migracao: coluna 'papel' adicionada em documentos.")


def obter_ultimo_nsu(conn: sqlite3.Connection, cnpj: str, ambiente: str) -> int:
    """Retorna o último NSU processado. Retorna 0 se nunca consultado."""
    row = conn.execute(
        "SELECT ultimo_nsu FROM controle_nsu WHERE cnpj_consulta=? AND ambiente=?",
        (cnpj, ambiente)
    ).fetchone()
    return int(row["ultimo_nsu"]) if row else 0


def salvar_nsu(conn: sqlite3.Connection, cnpj: str, ambiente: str, nsu: int) -> None:
    """Persiste o NSU atomicamente (INSERT OR REPLACE)."""
    conn.execute("""
        INSERT INTO controle_nsu (cnpj_consulta, ambiente, ultimo_nsu, data_consulta)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(cnpj_consulta, ambiente)
        DO UPDATE SET ultimo_nsu=excluded.ultimo_nsu, data_consulta=excluded.data_consulta
    """, (cnpj, ambiente, nsu, datetime.now().isoformat()))
    conn.commit()
    logger.debug("[DB] NSU %d salvo para %s (%s)", nsu, cnpj, ambiente)


# ---------------------------------------------------------------------------
# Documentos
# ---------------------------------------------------------------------------

def documento_existe(conn: sqlite3.Connection,
                     chave_acesso: str,
                     tipo_documento: str,
                     tipo_evento: Optional[str]) -> bool:
    """Verifica se o documento já foi baixado (deduplicação)."""
    row = conn.execute("""
        SELECT 1 FROM documentos
        WHERE chave_acesso=? AND tipo_documento=? AND (tipo_evento=? OR (tipo_evento IS NULL AND ? IS NULL))
    """, (chave_acesso, tipo_documento, tipo_evento, tipo_evento)).fetchone()
    return row is not None


def registrar_documento(conn: sqlite3.Connection,
                        cnpj_dono: str,
                        nsu: int,
                        chave_acesso: str,
                        tipo_documento: str,
                        tipo_evento: Optional[str],
                        competencia: str,
                        papel: Optional[str],
                        caminho_staging: Optional[str]) -> None:
    """Insere um documento. Ignora silenciosamente se já existir."""
    try:
        conn.execute("""
            INSERT INTO documentos
                (cnpj_dono, nsu, chave_acesso, tipo_documento, tipo_evento,
                 competencia, papel, caminho_staging, baixado_em)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (cnpj_dono, nsu, chave_acesso, tipo_documento, tipo_evento,
              competencia, papel, caminho_staging, datetime.now().isoformat()))
        conn.commit()
    except sqlite3.IntegrityError:
        logger.debug("[DB] Documento já registrado (ignorado): %s", chave_acesso)


# ---------------------------------------------------------------------------
# Execuções e Erros
# ---------------------------------------------------------------------------

def iniciar_execucao(conn: sqlite3.Connection) -> int:
    """Registra o início de uma execução e retorna o ID."""
    cur = conn.execute(
        "INSERT INTO execucoes (inicio, status) VALUES (?, 'RODANDO')",
        (datetime.now().isoformat(),)
    )
    conn.commit()
    return cur.lastrowid


def finalizar_execucao(conn: sqlite3.Connection, execucao_id: int,
                       ok: int, erro: int, docs: int, status: str = "CONCLUIDO") -> None:
    conn.execute("""
        UPDATE execucoes SET fim=?, status=?, empresas_ok=?, empresas_erro=?, docs_baixados=?
        WHERE id=?
    """, (datetime.now().isoformat(), status, ok, erro, docs, execucao_id))
    conn.commit()


def registrar_erro(conn: sqlite3.Connection, execucao_id: int,
                   cnpj: str, nsu: Optional[int], tipo: str, mensagem: str) -> None:
    conn.execute("""
        INSERT INTO erros (execucao_id, empresa_cnpj, nsu, tipo, mensagem, criado_em)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (execucao_id, cnpj, nsu, tipo, mensagem, datetime.now().isoformat()))
    conn.commit()


# ---------------------------------------------------------------------------
# Consultas para montagem dos ZIPs
# ---------------------------------------------------------------------------

def documentos_da_competencia(conn: sqlite3.Connection,
                              cnpj: str,
                              competencia: str) -> list[sqlite3.Row]:
    """Todos os documentos ja baixados da empresa naquela competencia."""
    return list(conn.execute("""
        SELECT nsu, chave_acesso, tipo_documento, tipo_evento,
               competencia, papel, caminho_staging
        FROM documentos
        WHERE cnpj_dono=? AND competencia=?
        ORDER BY nsu
    """, (cnpj, competencia)))


def chaves_canceladas(conn: sqlite3.Connection, cnpj: str) -> set[str]:
    """
    Chaves com evento de cancelamento - em QUALQUER competencia.

    O cancelamento costuma vir depois da nota, as vezes no mes seguinte.
    Filtrar por competencia aqui faria uma nota cancelada em setembro
    continuar aparecendo como valida no ZIP de agosto.
    """
    tipos = ",".join("?" * len(_EVENTOS_CANCELAMENTO))
    linhas = conn.execute(f"""
        SELECT DISTINCT chave_acesso FROM documentos
        WHERE cnpj_dono=? AND tipo_documento='EVENTO' AND tipo_evento IN ({tipos})
    """, (cnpj, *_EVENTOS_CANCELAMENTO))
    return {r["chave_acesso"] for r in linhas}


def papel_por_chave(conn: sqlite3.Connection, cnpj: str) -> dict[str, str]:
    """
    Mapa chave_acesso -> papel, considerando apenas as NFS-e.
    Usado para o evento herdar o papel da nota que ele referencia.
    """
    linhas = conn.execute("""
        SELECT chave_acesso, papel FROM documentos
        WHERE cnpj_dono=? AND tipo_documento='NFSE' AND papel IS NOT NULL
    """, (cnpj,))
    return {r["chave_acesso"]: r["papel"] for r in linhas}


_EVENTOS_CANCELAMENTO = (
    "CANCELAMENTO",
    "CANCELAMENTO_POR_SUBSTITUICAO",
    "CANCELAMENTO_DEFERIDO_ANALISE_FISCAL",
    "CANCELAMENTO_POR_OFICIO",
)
