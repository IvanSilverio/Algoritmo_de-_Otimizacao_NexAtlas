#!/usr/bin/env python3
"""Runner em lote dos casos de teste REA (esquema published).

Automatiza exatamente o que o `nexatlas_cli.py` faz por par origem/destino —
mesmo `PostgisLoader.build_subgraph`, mesmo `plan_v1_route`, mesmo
`plot_v1_combined` — só substituindo a entrada manual do REPL por uma leitura
em lote de `casos_teste_algoritmo_rotas_REA.json` (lista de grupos, cada um
com `casos: [{partida, destino, "tipo de corredor"}]`).

Setup (uma vez):
    source .env.sh   # credenciais do esquema published

Uso:
    python3 run_test_cases.py
    python3 run_test_cases.py --group "São Paulo"
    python3 run_test_cases.py --no-plot --output resultado_rapido.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

try:
    import psycopg2
except ImportError:
    sys.exit("Instale o driver: pip install psycopg2-binary")

from nexatlas_router.db import PostgisLoader
from nexatlas_router.v1 import plan_v1_route
from nexatlas_router.plot_route import plot_v1_combined
from nexatlas_router.portoes import PortaoError


def get_conn():
    # Mesma convenção de conexão do nexatlas_cli.py — defaults do esquema
    # published (jetstream:5433); credenciais nunca no código.
    required = ["NEXATLAS_DB_PASSWORD"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        sys.exit(f"Variáveis de ambiente ausentes: {', '.join(missing)}. "
                  f"Execute: source .env.sh")
    conn = psycopg2.connect(
        host=os.environ.get("NEXATLAS_DB_HOST", "jetstream.nexatlas.com"),
        port=os.environ.get("NEXATLAS_DB_PORT", "5433"),
        dbname=os.environ.get("NEXATLAS_DB_NAME", "jetstream"),
        user=os.environ.get("NEXATLAS_DB_USER", "ivansilverio"),
        password=os.environ["NEXATLAS_DB_PASSWORD"],
    )
    with conn.cursor() as cur:
        cur.execute("SET search_path TO published, public;")
    conn.commit()
    return conn


def load_cases(path: str, group_filter: str | None) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        grupos = json.load(f)
    flat: list[dict] = []
    for grupo in grupos:
        nome_grupo = grupo["grupo"]
        if group_filter and group_filter.lower() not in nome_grupo.lower():
            continue
        for caso in grupo["casos"]:
            flat.append({
                "grupo": nome_grupo,
                "partida": caso["partida"],
                "destino": caso["destino"],
                "tipo_corredor": caso.get("tipo de corredor"),
            })
    return flat


def run_case(loader: PostgisLoader, case: dict,
            idx: int, png_dir: str | None) -> dict:
    partida, destino = case["partida"], case["destino"]
    t0 = time.time()
    try:
        # Mesmos parâmetros que o CLI usa (nexatlas_cli.py: build_subgraph).
        graph, meta = loader.build_subgraph(
            partida, destino, chart_radius_nm=60.0, link_radius_nm=30.0)
        result = plan_v1_route(graph, meta["origin_id"], meta["dest_id"],
                               origin_gate_ids=meta.get("origin_gate_ids"),
                               dest_gate_ids=meta.get("dest_gate_ids"))

        plot_path = None
        if png_dir is not None:
            plot_path = os.path.join(png_dir, f"{idx:03d}_{partida}_{destino}.png")
            plot_v1_combined(graph, result, plot_path,
                             title=f"Malha Aérea VFR — {partida} → {destino}")

        row = result.to_dict()
        row.update({
            "grupo": case["grupo"],
            "partida": partida,
            "destino": destino,
            "tipo_corredor": case["tipo_corredor"],
            "status": "OK",
            "elapsed_s": round(time.time() - t0, 2),
            "plot_path": plot_path,
        })
        return row
    except Exception as e:
        # TAREFA_portoes.md item 6: falha por portão obrigatório inaplicável
        # (ponto não resolvido, ou forçado desconecta a rota) é uma falha
        # EXPLICADA, não um erro silencioso — status próprio no relatório.
        return {
            "grupo": case["grupo"],
            "partida": partida,
            "destino": destino,
            "tipo_corredor": case["tipo_corredor"],
            "status": "ERRO_PORTAO" if isinstance(e, PortaoError) else "ERRO",
            "erro": str(e),
            "elapsed_s": round(time.time() - t0, 2),
            "plot_path": None,
        }


def print_case_line(idx: int, total: int, row: dict) -> None:
    prefix = f"[{idx:03d}/{total}] {row['grupo']} · {row['partida']} → {row['destino']}"
    if row["status"] in ("ERRO", "ERRO_PORTAO"):
        print(f"{prefix}  {row['status']}  {row['erro']}")
        return
    direct_nm = row["direct_distance_nm"]
    total_nm = row["total_distance_nm"]
    overhead = total_nm - direct_nm
    sign = "+" if overhead >= 0 else ""
    src = row["meta"].get("route_source", "?")
    n_corr = len(row["corridors_used"])
    n_alt = row["meta"].get("n_alternatives", 0)
    fallback = "  [FALLBACK DIRETO]" if row["meta"].get("used_direct_fallback") else ""
    colisao = row["meta"].get("colisao_portao_coerencia")
    aviso = ""
    if colisao:
        lados = "/".join(l for l in ("entrada", "saida") if colisao.get(l))
        aviso = f"  [COLISÃO PORTÃO×COERÊNCIA: {lados}]"
    print(f"{prefix}  OK   {src:<14}  {total_nm:.1f} NM "
          f"(direta {direct_nm:.1f}, {sign}{overhead:.1f})  "
          f"corredores={n_corr}  alt={n_alt}{fallback}{aviso}")


def print_summary(rows: list[dict]) -> None:
    total = len(rows)
    ok = [r for r in rows if r["status"] == "OK"]
    erro = [r for r in rows if r["status"] == "ERRO"]
    erro_portao = [r for r in rows if r["status"] == "ERRO_PORTAO"]
    fallback = [r for r in ok if r["meta"].get("used_direct_fallback")]
    colisoes = [r for r in ok if r["meta"].get("colisao_portao_coerencia")]

    print()
    print("─" * 66)
    print(f"Total: {total}   OK: {len(ok)}   ERRO: {len(erro)}   "
          f"ERRO_PORTAO: {len(erro_portao)}   Fallback direto: {len(fallback)}")

    por_grupo: dict[str, dict[str, int]] = {}
    for r in rows:
        g = por_grupo.setdefault(r["grupo"], {"total": 0, "ok": 0, "erro": 0})
        g["total"] += 1
        g["ok" if r["status"] == "OK" else "erro"] += 1
    print()
    for grupo, stats in por_grupo.items():
        print(f"  {grupo}: {stats['ok']}/{stats['total']} OK "
              f"({stats['erro']} erro)")

    if erro:
        print()
        print("Casos com erro:")
        for r in erro:
            print(f"  {r['partida']} → {r['destino']}: {r['erro']}")

    if erro_portao:
        print()
        print("Casos com portão obrigatório INAPLICÁVEL (falha explicada — TAREFA_portoes.md):")
        for r in erro_portao:
            print(f"  {r['partida']} → {r['destino']}: {r['erro']}")

    print()
    print(f"Relatório de colisões portão×coerência (TAREFA_portoes.md): {len(colisoes)} caso(s)")
    if colisoes:
        print("(o portão prevalece — a rota é mantida; sinalizado pra revisão humana)")
        for r in colisoes:
            c = r["meta"]["colisao_portao_coerencia"]
            lados = "/".join(l for l in ("entrada", "saida") if c.get(l))
            print(f"  {r['partida']} → {r['destino']}: colisão na {lados} "
                  f"(rota: {' → '.join(p['name'] for p in r['points'])})")
    print("─" * 66)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Roda em lote os casos de casos_teste_algoritmo_rotas_REA.json")
    ap.add_argument("cases", nargs="?",
                    default="casos_teste_algoritmo_rotas_REA.json",
                    help="JSON de casos de teste (grupos + partida/destino)")
    ap.add_argument("--output", default="resultados_testes_REA.json",
                    help="caminho do relatório JSON de saída")
    ap.add_argument("--png-dir", default="resultados_testes_REA_pngs",
                    help="diretório dos PNGs gerados (ignorado com --no-plot)")
    ap.add_argument("--group", default=None,
                    help="filtra por substring do campo 'grupo' (case-insensitive)")
    ap.add_argument("--no-plot", action="store_true",
                    help="não gera PNG por caso (só calcula e reporta)")
    args = ap.parse_args()

    cases = load_cases(args.cases, args.group)
    if not cases:
        sys.exit(f"Nenhum caso encontrado em {args.cases} "
                  f"(filtro de grupo: {args.group!r}).")

    png_dir = None
    if not args.no_plot:
        png_dir = args.png_dir
        os.makedirs(png_dir, exist_ok=True)

    conn = get_conn()
    loader = PostgisLoader(conn)

    print(f"Rodando {len(cases)} casos "
          f"({'com' if png_dir else 'sem'} geração de PNG)...\n")

    rows: list[dict] = []
    for i, case in enumerate(cases, 1):
        row = run_case(loader, case, i, png_dir)
        print_case_line(i, len(cases), row)
        rows.append(row)

    conn.close()

    print_summary(rows)

    report = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "arquivo_casos": args.cases,
        "filtro_grupo": args.group,
        "total": len(rows),
        "ok": sum(1 for r in rows if r["status"] == "OK"),
        "erro": sum(1 for r in rows if r["status"] == "ERRO"),
        "erro_portao": sum(1 for r in rows if r["status"] == "ERRO_PORTAO"),
        "fallback_direto": sum(
            1 for r in rows
            if r["status"] == "OK" and r["meta"].get("used_direct_fallback")),
        "colisoes_portao_coerencia": [
            {"partida": r["partida"], "destino": r["destino"],
             "colisao": r["meta"]["colisao_portao_coerencia"]}
            for r in rows
            if r["status"] == "OK" and r["meta"].get("colisao_portao_coerencia")
        ],
        "casos": rows,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nRelatório salvo em: {os.path.abspath(args.output)}")
    if png_dir:
        print(f"PNGs salvos em: {os.path.abspath(png_dir)}/")


if __name__ == "__main__":
    main()
