"""
main.py — Ponto de entrada do Robo NFS-e ADN.

Uso:
    python main.py                        # pergunta a competencia, roda todas
    python main.py -c 25,438,752          # so essas empresas (codigo Dominio)
    python main.py -m 082026              # competencia sem perguntar
    python main.py -c 25 -m 07/2026       # combinado
    python main.py --listar               # mostra a carteira e sai
    python main.py -m 082026 --sim        # sem confirmacao (agendador)
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import banco as db
import nfse_dados
import planilhas
import relatorio
from config import carregar_config
from orquestrador import ResultadoEmpresa, carregar_clientes, processar_empresa


def configurar_logging(logs_dir: str) -> None:
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    caminho = Path(logs_dir) / datetime.now().strftime("nfse_adn_%Y%m%d_%H%M%S.log")
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)-13s | %(message)s",
                            datefmt="%H:%M:%S")
    arq = RotatingFileHandler(caminho, maxBytes=10 * 1024 * 1024, backupCount=10,
                              encoding="utf-8")
    arq.setLevel(logging.DEBUG); arq.setFormatter(fmt)
    con = logging.StreamHandler(sys.stdout)
    con.setLevel(logging.INFO); con.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG); root.handlers.clear()
    root.addHandler(arq); root.addHandler(con)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def normalizar_competencia(valor: str) -> str:
    """'082026' | '08/2026' | '7/2026' | '2026-08' -> '082026' (MMAAAA)."""
    v = re.sub(r"\D", "", valor or "")
    if len(v) == 5 and 1 <= int(v[:1]) <= 9:
        v = "0" + v
    if len(v) != 6:
        raise ValueError(f"Competencia invalida: '{valor}'. Use MMAAAA, MM/AAAA ou AAAA-MM.")
    ano_ok = lambda x: 1900 <= int(x) <= 2199
    mes_ok = lambda x: 1 <= int(x) <= 12
    if ano_ok(v[:4]) and mes_ok(v[4:]):
        return v[4:] + v[:4]
    if mes_ok(v[:2]) and ano_ok(v[2:]):
        return v[:2] + v[2:]
    raise ValueError(f"Competencia invalida: '{valor}'. Use MMAAAA, MM/AAAA ou AAAA-MM.")


def perguntar_competencia() -> str:
    padrao = datetime.now().strftime("%m%Y")
    while True:
        resp = input(f"\nCompetencia a processar [MMAAAA] (Enter = {padrao}): ").strip()
        try:
            return normalizar_competencia(resp or padrao)
        except ValueError as exc:
            print(f"  {exc}")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Robo NFS-e ADN - baixa as NFS-e e entrega ZIPs e planilhas.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exemplos:\n  python main.py\n  python main.py -c 25,438 -m 082026\n")
    p.add_argument("-c", "--codigos", help="Codigos Dominio separados por virgula.")
    p.add_argument("-m", "--competencia", help="MMAAAA, MM/AAAA ou AAAA-MM.")
    p.add_argument("--somente-erros", action="store_true",
                   help="Roda so as empresas que NAO fecharam OK na ultima rodada "
                        "desta competencia (le o CSV mais recente de relatorios/).")
    p.add_argument("--listar", action="store_true", help="Lista a carteira e sai.")
    p.add_argument("--sim", action="store_true", help="Nao pedir confirmacao.")
    args = p.parse_args()

    try:
        config = carregar_config()
    except Exception as exc:
        print(f"ERRO DE CONFIGURACAO: {exc}")
        return 2

    configurar_logging(config.logs_dir)
    log = logging.getLogger("main")

    n = nfse_dados.carregar_municipios(config.municipios_csv)
    if n == 0:
        log.warning("Tabela de municipios nao encontrada (%s). As colunas de "
                    "nome de municipio vao sair em branco.", config.municipios_csv)
    else:
        log.info("Tabela de municipios: %d registros", n)

    # --somente-erros precisa da competencia para achar o CSV da ultima rodada,
    # entao ela e resolvida antes de montar a carteira.
    competencia = None
    if args.competencia or args.somente_erros:
        try:
            competencia = (normalizar_competencia(args.competencia)
                           if args.competencia else perguntar_competencia())
        except ValueError as exc:
            log.error("%s", exc)
            return 2

    codigos = [c for c in (args.codigos or "").split(",") if c.strip()] or None

    if args.somente_erros:
        codigos = relatorio.codigos_com_erro(config.relatorios_dir, competencia)
        if not codigos:
            print("")
            print(f"Nenhuma empresa com erro na ultima rodada de "
                  f"{competencia[:2]}/{competencia[2:]}. Nada a refazer.")
            print("")
            return 0

    try:
        clientes = carregar_clientes(config.clientes_csv, codigos)
    except (ValueError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 2

    if args.listar:
        print(f"\n{len(clientes)} empresa(s):\n")
        for c in clientes:
            print(f"  {c.codigo_dominio:>5}  {c.cnpj:<15} {c.apelido or c.razao_social}")
        return 0
    if not clientes:
        log.error("Nenhuma empresa selecionada.")
        return 2

    if competencia is None:
        try:
            competencia = perguntar_competencia()
        except ValueError as exc:
            log.error("%s", exc)
            return 2

    print()
    print(f"  Competencia : {competencia[:2]}/{competencia[2:]}")
    print(f"  Empresas    : {len(clientes)}"
          + ("   (so as que deram erro na ultima rodada)" if args.somente_erros else ""))
    print(f"  Ambiente    : {config.ambiente_esperado}  ({config.adn_url_base})")
    print(f"  Certificados: {config.certificados_dir}")
    print(f"  Emitidas    : {config.raizes['EMITIDAS']}")
    print(f"  Recebidas   : {config.raizes['RECEBIDAS']}")
    print(f"  Relatorios  : {config.raizes['RELATORIOS']}")
    print()
    if not args.sim and input("Confirma? [s/N]: ").strip().lower() not in ("s", "sim", "y"):
        print("Cancelado.")
        return 1

    conn = db.conectar(config.db_path)
    execucao_id = db.iniciar_execucao(conn)
    resultados: list[ResultadoEmpresa] = []

    try:
        for i, cliente in enumerate(clientes, 1):
            log.info("=" * 62)
            log.info("[%d/%d] %s | %s | CNPJ %s", i, len(clientes),
                     cliente.codigo_dominio, (cliente.apelido or cliente.razao_social)[:38],
                     cliente.cnpj)
            # O ADN derruba o handshake mTLS quando recebe conexoes seguidas
            # demais do mesmo IP. Uma pausa entre empresas evita a rajada de
            # RECORD_LAYER_FAILURE que derrubou 79% da carteira em 25/08/2026.
            if i > 1 and config.intervalo_empresas > 0:
                time.sleep(config.intervalo_empresas)
            try:
                resultados.append(processar_empresa(cliente, conn, config,
                                                    execucao_id, competencia))
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                log.exception("[%s] Falha inesperada: %s", cliente.codigo_dominio, exc)
                db.registrar_erro(conn, execucao_id, cliente.cnpj, None, "INESPERADO", str(exc))
                resultados.append(ResultadoEmpresa(cliente=cliente, status="ERRO",
                                                   mensagem=str(exc)))
    except KeyboardInterrupt:
        log.warning("Interrompido. Gerando relatorios do que ja rodou.")

    ok = sum(1 for r in resultados if r.status == "OK")
    erro = len(resultados) - ok
    db.finalizar_execucao(conn, execucao_id, ok, erro,
                          sum(r.baixados_agora for r in resultados))

    csv_local = relatorio.gerar(resultados, competencia, config.relatorios_dir)
    try:
        mensal = planilhas.atualizar_mensal(config.planilha_mensal, competencia, resultados)
    except Exception as exc:
        log.error("Falha ao atualizar a planilha mensal: %s", exc)
        mensal = f"FALHOU: {exc}"

    print(relatorio.resumo_console(resultados, competencia))
    print(f"\n  Planilha mensal : {mensal}")
    print(f"  Log da rodada   : {csv_local}\n")

    conn.close()
    return 0 if erro == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
