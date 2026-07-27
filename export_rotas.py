#!/usr/bin/env python3
"""Exporta a rota lateral V1 + as rotas ALTERNATIVAS para um JSON.

Diferente do terminal (que só imprime os NOMES das alternativas), aqui cada
alternativa sai com as COORDENADAS completas de todos os seus pontos, além dos
corredores REA e das distâncias. As partes descritivas (texto/justificativa)
são omitidas — o foco é coordenada, ponto, trecho e corredor.

Uso (depois de `source .env.sh`):
    python export_rotas.py SBMT SBBH
    python export_rotas.py SBMT SBBH SBPA SBFL          # vários pares de uma vez
    python export_rotas.py SBMT SBBH -o minha_rota.json

Saída (JSON):
    {
      "gerado_em": "<ISO-8601 UTC>",
      "parametros": {"chart_radius_nm": 60.0, "link_radius_nm": 30.0},
      "rotas": [
        {
          "origem": "SBMT", "destino": "SBBH",
          "distancia_direta_nm": 265.9,
          "distancia_rota_nm": 283.8,
          "corredores": [{"name": "...", "is_mandatory": true}, ...],
          "trechos":   [{"from": "...", "to": "...", "corridor": "...",
                         "is_mandatory": false}, ...],
          "pontos":    [{"seq": 1, "id": "...", "name": "...", "kind": "...",
                         "lat": -23.5, "lon": -46.6, "chart": null}, ...],
          "alternativas": [
            {"distancia_rota_nm": 284.6, "overhead_nm": 18.8,
             "corredores": [...], "n_pontos": 15, "pontos": [ ... ]},
            ...
          ]
        }
      ]
    }

Coordenadas em graus decimais (lat/lon nomeados; sem arredondamento). As
distâncias saem arredondadas a 0.1 NM, como no terminal.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

try:
    import psycopg2  # noqa: F401
except ImportError:
    sys.exit("Instale o driver: pip install psycopg2-binary")

from nexatlas_router.db import PostgisLoader
from nexatlas_router.gwo import GWOConfig
from nexatlas_router.v1 import plan_v1_route

CHART_RADIUS_NM = 60.0
LINK_RADIUS_NM = 30.0


def _connect():
    """Conexão ao banco published (jetstream), credenciais por ambiente."""
    if not os.environ.get("NEXATLAS_DB_PASSWORD"):
        sys.exit("Variável NEXATLAS_DB_PASSWORD ausente. Execute: source .env.sh")
    import psycopg2
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


def _pontos(points: list[dict]) -> list[dict]:
    """Reempacota os pontos com `seq` (1..n) e ordem de campos estável."""
    out = []
    for i, p in enumerate(points, 1):
        out.append({
            "seq": i,
            "id": p["id"],
            "name": p["name"],
            "kind": p["kind"],
            "lat": p["lat"],
            "lon": p["lon"],
            "chart": p.get("chart"),
        })
    return out


def _rota_json(origin: str, dest: str, result) -> dict:
    """Serializa a rota principal + alternativas, sem texto descritivo."""
    alternativas = []
    for a in result.meta.get("alternatives", []):
        alternativas.append({
            "distancia_rota_nm": a["total_distance_nm"],
            "overhead_nm": a["overhead_nm"],
            "corredores": a["corridors_used"],       # [{name, is_mandatory}]
            "n_pontos": a["n_points"],
            "pontos": _pontos(a["points"]),
        })
    return {
        "origem": origin,
        "destino": dest,
        "distancia_direta_nm": round(result.direct_distance_nm, 1),
        "distancia_rota_nm": round(result.total_distance_nm, 1),
        "corredores": result.corridors_used,         # [{name, is_mandatory}]
        "trechos": result.legs,                      # [{from, to, corridor, is_mandatory}]
        "pontos": _pontos(result.points),
        "alternativas": alternativas,
    }


def exportar(pares: list[tuple[str, str]], out_path: str) -> None:
    conn = _connect()
    loader = PostgisLoader(conn)
    # config apenas para paridade com a CLI; plan_v1_route usa Dijkstra (exato).
    gwo_cfg = GWOConfig(seed=42, n_iterations=200, n_wolves=30, max_hops=80)

    rotas = []
    for origin, dest in pares:
        try:
            graph, meta = loader.build_subgraph(
                origin, dest,
                chart_radius_nm=CHART_RADIUS_NM, link_radius_nm=LINK_RADIUS_NM)
            result = plan_v1_route(graph, meta["origin_id"], meta["dest_id"], gwo_cfg)
            rotas.append(_rota_json(origin, dest, result))
            print(f"  ✓ {origin} → {dest}: {len(result.points)} pontos, "
                  f"{result.meta.get('n_alternatives', 0)} alternativa(s)")
        except Exception as e:                       # aeródromo inexistente, sem rota, etc.
            print(f"  ✗ {origin} → {dest}: {e}", file=sys.stderr)
            rotas.append({"origem": origin, "destino": dest, "erro": str(e)})
    conn.close()

    doc = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "parametros": {"chart_radius_nm": CHART_RADIUS_NM, "link_radius_nm": LINK_RADIUS_NM},
        "rotas": rotas,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"\n  JSON salvo: {os.path.abspath(out_path)}  ({len(rotas)} rota(s))")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Exporta rota V1 + alternativas (coordenadas, pontos, corredores) para JSON.")
    ap.add_argument("icaos", nargs="+",
                    help="Pares ICAO: ORIGEM DESTINO [ORIGEM DESTINO ...]")
    ap.add_argument("-o", "--out", default=None, help="Arquivo JSON de saída")
    args = ap.parse_args()

    if len(args.icaos) % 2 != 0:
        ap.error("Informe os ICAOs em pares (origem destino).")
    pares = [(args.icaos[i].upper(), args.icaos[i + 1].upper())
             for i in range(0, len(args.icaos), 2)]
    out = args.out or (f"rotas_{pares[0][0]}_{pares[0][1]}.json"
                       if len(pares) == 1 else "rotas_export.json")
    exportar(pares, out)


if __name__ == "__main__":
    main()