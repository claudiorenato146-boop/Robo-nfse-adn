"""
relatorio.py — Log da rodada, em CSV, para diagnostico rapido.

Nao confundir com a planilha mensal consolidada (planilhas.py), que e a que vai
para a rede. Este aqui fica na pasta do robo e serve para ver o que aconteceu
numa execucao especifica, inclusive os avisos de pasta faltando.

CSV com ponto-e-virgula e UTF-8 com BOM: e o que o Excel em portugues abre ja
com as colunas separadas.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

CABECALHO = ["codigo", "apelido", "cnpj", "competencia", "status",
             "emitidas", "recebidas", "emitidas_canceladas", "recebidas_canceladas",
             "total", "baixados_nesta_rodada", "avisos", "mensagem"]


def gerar(resultados: Iterable, competencia: str, relatorios_dir: str) -> str:
    resultados = list(resultados)
    pasta = Path(relatorios_dir)
    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / f"rodada_{competencia}_{datetime.now():%Y%m%d_%H%M%S}.csv"

    tot = dict.fromkeys(("emitidas", "recebidas", "emitidas_canceladas",
                         "recebidas_canceladas", "total", "baixados"), 0)

    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(CABECALHO)
        for r in resultados:
            w.writerow([
                r.cliente.codigo_dominio,
                r.cliente.apelido or r.cliente.razao_social,
                r.cliente.cnpj, competencia, r.status,
                r.emitidas, r.recebidas, r.emitidas_canceladas, r.recebidas_canceladas,
                r.total, r.baixados_agora,
                "; ".join(r.avisos), r.mensagem[:300],
            ])
            for k, v in (("emitidas", r.emitidas), ("recebidas", r.recebidas),
                         ("emitidas_canceladas", r.emitidas_canceladas),
                         ("recebidas_canceladas", r.recebidas_canceladas),
                         ("total", r.total), ("baixados", r.baixados_agora)):
                tot[k] += v
        w.writerow([])
        w.writerow(["TOTAL", "", "", competencia, "",
                    tot["emitidas"], tot["recebidas"], tot["emitidas_canceladas"],
                    tot["recebidas_canceladas"], tot["total"], tot["baixados"], "", ""])

    logger.info("[RODADA] %s", caminho)
    return str(caminho)


def resumo_console(resultados: Iterable, competencia: str) -> str:
    resultados = list(resultados)
    ok = [r for r in resultados if r.status == "OK"]
    parciais = [r for r in resultados if r.status == "PARCIAL"]
    falhas = [r for r in resultados if r.status not in ("OK", "PARCIAL")]

    L = [
        "", "=" * 70,
        f"  RESUMO - competencia {competencia[:2]}/{competencia[2:]}",
        "=" * 70,
        f"  Empresas processadas : {len(resultados)}",
        f"  Sem problema         : {len(ok)}",
        f"  Com aviso (parcial)  : {len(parciais)}",
        f"  Com falha            : {len(falhas)}",
        "",
        f"  Emitidas             : {sum(r.emitidas for r in resultados)}",
        f"  Emitidas canceladas  : {sum(r.emitidas_canceladas for r in resultados)}",
        f"  Recebidas            : {sum(r.recebidas for r in resultados)}",
        f"  Recebidas canceladas : {sum(r.recebidas_canceladas for r in resultados)}",
        f"  TOTAL na competencia : {sum(r.total for r in resultados)}",
        f"  Baixados nesta rodada: {sum(r.baixados_agora for r in resultados)}",
    ]

    if falhas:
        L += ["", "  FALHAS:"]
        for r in falhas:
            L.append(f"    [{r.cliente.codigo_dominio:>4}] "
                     f"{(r.cliente.apelido or r.cliente.razao_social)[:30]:<30} "
                     f"{r.status}: {r.mensagem[:56]}")

    if parciais:
        L += ["", "  AVISOS (rodou, mas faltou pasta):"]
        for r in parciais[:30]:
            L.append(f"    [{r.cliente.codigo_dominio:>4}] "
                     f"{(r.cliente.apelido or r.cliente.razao_social)[:30]:<30} "
                     f"{'; '.join(r.avisos)[:56]}")
        if len(parciais) > 30:
            L.append(f"    ... e mais {len(parciais) - 30}")

    vazias = [r for r in resultados if r.status in ("OK", "PARCIAL") and r.total == 0]
    if vazias:
        L += ["", f"  SEM NOTA NESTA COMPETENCIA: {len(vazias)} empresa(s)"]

    L.append("=" * 70)
    return "\n".join(L)


def codigos_com_erro(relatorios_dir: str, competencia: str) -> list[str]:
    """
    Codigos das empresas que NAO fecharam OK na ultima rodada daquela
    competencia, lidos do CSV mais recente em `relatorios_dir`.

    Serve para o --somente-erros: reprocessar so quem falhou, sem gastar
    handshake com quem ja esta pronto (o ADN limita conexoes mTLS por IP).
    """
    pasta = Path(relatorios_dir)
    if not pasta.is_dir():
        return []
    arquivos = sorted(pasta.glob(f"rodada_{competencia}_*.csv"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    if not arquivos:
        return []

    codigos: list[str] = []
    with open(arquivos[0], newline="", encoding="utf-8-sig") as f:
        for linha in csv.DictReader(f, delimiter=";"):
            cod = (linha.get("codigo") or "").strip()
            status = (linha.get("status") or "").strip().upper()
            if not cod or cod == "TOTAL":
                continue
            if status and status != "OK":
                codigos.append(cod)

    logger.info("[REPESCAGEM] %s -> %d empresa(s) sem OK",
                arquivos[0].name, len(codigos))
    return codigos
