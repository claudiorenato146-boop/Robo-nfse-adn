"""
conferir.py — Checagem previa, antes de rodar a carteira.

Confere duas coisas, sem tocar na API:

  1. CERTIFICADOS — o arquivo existe na pasta unica, a senha da planilha abre,
     esta dentro da validade e o CNPJ do certificado bate com a raiz do CNPJ
     da empresa.
  2. PASTAS — a pasta do cliente existe nas tres raizes. O robo NAO cria pasta
     de cliente, entao o que faltar aqui vira aviso na rodada.

Uso:
    python conferir.py                 # tudo
    python conferir.py -c 25,438       # so essas empresas
    python conferir.py --so-pastas     # pula os certificados (bem mais rapido)
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import storage
from config import carregar_config
from orquestrador import carregar_clientes, resolver_certificado

RAIZES = ("EMITIDAS", "RECEBIDAS", "RELATORIOS")


def cnpj_do_certificado(subject: str):
    m = re.search(r":(\d{14})", subject or "")
    return m.group(1) if m else None


def main() -> int:
    p = argparse.ArgumentParser(description="Confere certificados e pastas antes da rodada.")
    p.add_argument("-c", "--codigos", help="Codigos Dominio separados por virgula.")
    p.add_argument("--so-pastas", action="store_true", help="Nao abrir os certificados.")
    p.add_argument("--csv", help="Gravar o resultado neste CSV.")
    args = p.parse_args()

    try:
        config = carregar_config()
        codigos = [c for c in (args.codigos or "").split(",") if c.strip()] or None
        clientes = carregar_clientes(config.clientes_csv, codigos)
    except Exception as exc:
        print(f"ERRO: {exc}")
        return 2

    print(f"\nCarteira            : {len(clientes)} empresa(s)")
    print(f"Certificados        : {config.certificados_dir}")
    for r in RAIZES:
        print(f"{r:<20}: {config.raizes[r]}")
    print()

    if not args.so_pastas:
        from adn_client import validade_certificado

    linhas = []
    cert_ok = cert_erro = 0
    falta_pasta = {r: [] for r in RAIZES}
    agora = datetime.now(timezone.utc)

    print(f"  {'COD':>5} {'EMPRESA':<30} {'CERTIFICADO':<34} {'PASTAS'}")
    print("  " + "-" * 96)

    for c in sorted(clientes, key=lambda x: int(x.codigo_dominio)):
        nome = (c.apelido or c.razao_social)[:30]

        # --- certificado
        if args.so_pastas:
            sit_cert = "(nao conferido)"
        else:
            try:
                caminho = resolver_certificado(config.certificados_dir, c)
                _, vence, subject = validade_certificado(caminho, c.senha_certificado)
                dias = (vence - agora).days
                cc = cnpj_do_certificado(subject)
                if dias < 0:
                    sit_cert = f"VENCIDO em {vence:%d/%m/%Y}"
                elif cc and c.cnpj and cc[:8] != c.cnpj[:8]:
                    sit_cert = f"RAIZ DIVERGE (cert {cc[:8]})"
                else:
                    sit_cert = f"ok, vence {vence:%d/%m/%Y}"
                    if dias <= 30:
                        sit_cert += f" ({dias}d!)"
                cert_ok += 1 if sit_cert.startswith("ok") else 0
                cert_erro += 0 if sit_cert.startswith("ok") else 1
            except Exception as exc:
                sit_cert = f"FALHA: {str(exc)[:24]}"
                cert_erro += 1

        # --- pastas
        faltando = []
        for r in RAIZES:
            if storage.localizar_pasta_cliente(config.raizes[r], c.codigo_dominio) is None:
                faltando.append(r)
                falta_pasta[r].append(c)
        sit_pasta = "todas ok" if not faltando else "FALTA: " + ",".join(faltando)

        print(f"  {c.codigo_dominio:>5} {nome:<30} {sit_cert:<34} {sit_pasta}")
        linhas.append([c.codigo_dominio, nome, c.cnpj, c.arquivo_certificado,
                       sit_cert, sit_pasta])

    print("\n" + "=" * 98)
    if not args.so_pastas:
        print(f"  Certificados ok        : {cert_ok} de {len(clientes)}")
        print(f"  Certificados com erro  : {cert_erro}")
    for r in RAIZES:
        n = len(falta_pasta[r])
        print(f"  Sem pasta em {r:<12}: {n}" + ("  <-- criar antes de rodar" if n else ""))
    print("=" * 98 + "\n")

    if args.csv:
        with io.open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["codigo", "empresa", "cnpj", "arquivo_certificado",
                        "situacao_certificado", "situacao_pastas"])
            w.writerows(linhas)
        print(f"  gravado: {args.csv}\n")

    problemas = cert_erro + sum(len(v) for v in falta_pasta.values())
    return 0 if problemas == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
