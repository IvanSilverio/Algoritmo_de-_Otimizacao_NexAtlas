"""Runner V1 de ponta a ponta contra o banco real (esquema published).

Conecta via variáveis de ambiente (NUNCA credenciais no código), monta o
subgrafo regional REA, roda o GWO, imprime o JSON da V1 e salva a plotagem.

Setup (uma vez):
    pip install psycopg2-binary numpy matplotlib
    export NEXATLAS_DB_HOST=jetstream.nexatlas.com
    export NEXATLAS_DB_PORT=5433
    export NEXATLAS_DB_NAME=jetstream
    export NEXATLAS_DB_USER=ivansilverio
    export NEXATLAS_DB_PASSWORD=sua_senha

Uso:
    # Coordenadas lidas automaticamente de published.adhps.geom:
    python run_v1.py SBBH SBMT
"""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    import psycopg2
except ImportError:
    sys.exit("Instale o driver: pip install psycopg2-binary")

from nexatlas_router.db import PostgisLoader
from nexatlas_router.gwo import GWOConfig
from nexatlas_router.v1 import plan_v1_route
from nexatlas_router.plot_route import plot_v1_route


def get_conn():
    # Defaults do esquema published (jetstream:5433); sobrescrevíveis por env.
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Rota V1 (VFR REA) com GWO — published")
    ap.add_argument("origin", help="ICAO de origem (ex: SBBH)")
    ap.add_argument("dest", help="ICAO de destino (ex: SBMT)")
    ap.add_argument("--chart-radius-nm", type=float, default=60.0)
    ap.add_argument("--link-radius-nm", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--iterations", type=int, default=200)
    ap.add_argument("--wolves", type=int, default=30)
    ap.add_argument("--max-hops", type=int, default=80)
    ap.add_argument("--plot", default=None, help="caminho do PNG de saída")
    args = ap.parse_args()

    conn = get_conn()
    # Loader resolve aeródromos direto de published.adhps.geom (sem resolver externo).
    loader = PostgisLoader(conn)

    graph, meta = loader.build_subgraph(
        args.origin, args.dest,
        chart_radius_nm=args.chart_radius_nm,
        link_radius_nm=args.link_radius_nm,
    )
    n_real = sum(1 for es in graph.adj.values() for e in es if not e.synthetic)
    print(f"Cartas: {meta['charts']}")
    print(f"Subgrafo: {graph.n} nós, {n_real} arestas de corredor REA")
    print(f"Sintéticas: {meta['synthetic_diagnostics']}\n")

    result = plan_v1_route(
        graph, meta["origin_id"], meta["dest_id"],
        GWOConfig(seed=args.seed, n_iterations=args.iterations,
                  n_wolves=args.wolves, max_hops=args.max_hops),
    )

    payload = result.to_dict()
    payload["meta"].pop("fitness_history", None)
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    plot_path = args.plot or f"rota_{args.origin}_{args.dest}.png"
    plot_v1_route(graph, result, plot_path,
                  title=f"Malha Aérea VFR — {args.origin} -> {args.dest}")
    print(f"\n[Sucesso] Plotagem salva em: {plot_path}")

    conn.close()


if __name__ == "__main__":
    main()