"""
config.py — Configuracao do robo NFS-e ADN, lida do .env.

Falha cedo e com mensagem clara quando falta variavel obrigatoria.
"""

import logging
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def _obrigatorio(nome: str) -> str:
    valor = os.getenv(nome)
    if not valor or not valor.strip():
        raise EnvironmentError(
            f"[CONFIG] Variavel obrigatoria nao definida: {nome}. "
            f"Confira o .env (modelo em .env.example)."
        )
    return valor.strip()


def _opcional(nome: str, padrao: str) -> str:
    return (os.getenv(nome) or padrao).strip()


@dataclass(frozen=True)
class Config:
    adn_url_base: str
    ambiente_esperado: str
    param_cnpj: str

    # tres raizes de saida + certificados
    raizes: dict = field(default_factory=dict)
    certificados_dir: str = ""
    planilha_mensal: str = ""

    staging_dir: str = ""
    logs_dir: str = ""
    relatorios_dir: str = ""
    db_path: str = ""
    clientes_csv: str = ""
    municipios_csv: str = ""

    timeout_connect: int = 30
    timeout_read: int = 120
    intervalo_requisicoes: float = 1.0
    intervalo_empresas: float = 3.0
    max_tentativas: int = 5


def carregar_config() -> Config:
    raizes = {
        "EMITIDAS": _obrigatorio("RAIZ_EMITIDAS"),
        "RECEBIDAS": _obrigatorio("RAIZ_RECEBIDAS"),
        "RELATORIOS": _obrigatorio("RAIZ_RELATORIOS"),
    }

    cfg = Config(
        adn_url_base=_obrigatorio("ADN_URL_BASE").rstrip("/"),
        ambiente_esperado=_opcional("AMBIENTE_ESPERADO", "PRODUCAO").upper(),
        param_cnpj=_opcional("ADN_PARAM_CNPJ", "cnpjConsulta"),

        raizes=raizes,
        certificados_dir=_obrigatorio("CERTIFICADOS_DIR"),
        planilha_mensal=_opcional("PLANILHA_MENSAL",
                                  os.path.join(raizes["RELATORIOS"],
                                               "PLANILHA RELATORIO MENSAL (API).xlsx")),

        staging_dir=_opcional("STAGING_DIR", "./staging"),
        logs_dir=_opcional("LOGS_DIR", "./logs"),
        relatorios_dir=_opcional("RELATORIOS_DIR", "./relatorios"),
        db_path=_opcional("DB_PATH", "./data/nfse_adn.db"),
        clientes_csv=_opcional("CLIENTES_CSV", "./data/clientes.csv"),
        municipios_csv=_opcional("MUNICIPIOS_CSV", "./data/municipios.csv"),

        timeout_connect=int(_opcional("TIMEOUT_CONNECT", "30")),
        timeout_read=int(_opcional("TIMEOUT_READ", "120")),
        intervalo_requisicoes=float(_opcional("INTERVALO_REQUISICOES", "1")),
        intervalo_empresas=float(_opcional("INTERVALO_EMPRESAS", "3")),
        max_tentativas=int(_opcional("MAX_TENTATIVAS", "5")),
    )

    logger.info("[CONFIG] Ambiente %s | %s", cfg.ambiente_esperado, cfg.adn_url_base)
    return cfg
