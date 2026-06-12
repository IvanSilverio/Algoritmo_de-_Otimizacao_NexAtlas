"""Runner V1 de ponta a ponta contra o banco real.

Conecta via variáveis de ambiente (NUNCA credenciais no código), monta o
subgrafo regional, roda o GWO, imprime o JSON da V1 e salva a plotagem.

Setup (uma vez):
    pip install psycopg2-binary numpy matplotlib
    export NEXATLAS_DB_HOST=assistant.nexatlas.com
    export NEXATLAS_DB_PORT=5433
    export NEXATLAS_DB_NAME=jetstream_replica
    export NEXATLAS_DB_USER=seu_usuario
    export NEXATLAS_DB_PASSWORD=sua_senha

Uso:
    # Enquanto a fonte de coordenadas da adhps não estiver mapeada,
    # informe lon/lat manualmente (ordem do banco: LONGITUDE primeiro):
    python run_v1.py SBMT SBJD --origin-lonlat -46.6377 -23.5092 \
                               --dest-lonlat   -46.9436 -23.1817

    # Quando o banco interno tiver a coordenada oficial, troque o
    # CsvResolver pelo AdhpsGeomResolver em resolver.py.
    python run_v1.py SBMT SBJD
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
from nexatlas_router.resolver import CsvResolver

# Reservado para quando a coordenada oficial existir no banco (ver
# AdhpsGeomResolver em nexatlas_router/resolver.py).
AERODROME_COORD_SQL: str | None = None

# Fonte PROVISÓRIA de coordenadas: CSV público (OurAirports, domínio público).
# Substituir por AdhpsGeomResolver quando o banco interno tiver a geometria.
DEFAULT_CSV = os.path.join(os.path.dirname(__file__), "data",
                           "aerodromos_br_ourairports.csv")


def get_conn():
    return psycopg2.connect(
        host=os.environ["NEXATLAS_DB_HOST"],
        port=os.environ.get("NEXATLAS_DB_PORT", "5432"),
        dbname=os.environ["NEXATLAS_DB_NAME"],
        user=os.environ["NEXATLAS_DB_USER"],
        password=os.environ["NEXATLAS_DB_PASSWORD"],
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Rota V1 (VFR) com GWO")
    ap.add_argument("origin", help="ICAO de origem (ex: SBMT)")
    ap.add_argument("dest", help="ICAO de destino (ex: SBJD)")
    ap.add_argument("--origin-lonlat", nargs=2, type=float, metavar=("LON", "LAT"))
    ap.add_argument("--dest-lonlat", nargs=2, type=float, metavar=("LON", "LAT"))
    ap.add_argument("--chart-radius-nm", type=float, default=60.0)
    ap.add_argument("--link-radius-nm", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--iterations", type=int, default=200)
    ap.add_argument("--wolves", type=int, default=30)
    ap.add_argument("--plot", default=None, help="caminho do PNG de saída")
    ap.add_argument("--csv", default=DEFAULT_CSV,
                    help="CSV de coordenadas (default: OurAirports BR)")
    args = ap.parse_args()

    conn = get_conn()
    loader = PostgisLoader(conn, aerodrome_coord_sql=AERODROME_COORD_SQL,
                           schema="v2")

    # Resolver de coordenadas: CSV público se não houver lon/lat manual.
    resolver = None
    if not (args.origin_lonlat and args.dest_lonlat):
        resolver = CsvResolver(args.csv)
        print(f"Fonte de coordenadas: {os.path.basename(args.csv)} "
              f"(OurAirports — provisório, dado público)\n")

    graph, meta = loader.build_subgraph(
        args.origin, args.dest,
        origin_lonlat=tuple(args.origin_lonlat) if args.origin_lonlat else None,
        dest_lonlat=tuple(args.dest_lonlat) if args.dest_lonlat else None,
        resolver=resolver,
        chart_radius_nm=args.chart_radius_nm,
        link_radius_nm=args.link_radius_nm,
    )
    n_real = sum(1 for es in graph.adj.values() for e in es if not e.synthetic)
    print(f"Cartas: {meta['charts']}")
    print(f"Subgrafo: {graph.n} nós, {n_real} arestas de corredor\n")

    result = plan_v1_route(
        graph, meta["origin_id"], meta["dest_id"],
        GWOConfig(seed=args.seed, n_iterations=args.iterations,
                  n_wolves=args.wolves, max_hops=40),
    )

    payload = result.to_dict()
    payload["meta"].pop("fitness_history")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    plot_path = args.plot or f"rota_{args.origin}_{args.dest}.png"
    plot_v1_route(graph, result, plot_path,
                  title=f"Malha Aérea VFR — {args.origin} -> {args.dest}")
    print(f"\n[Sucesso] Plotagem salva em: {plot_path}")

    conn.close()


if __name__ == "__main__":
    main()
