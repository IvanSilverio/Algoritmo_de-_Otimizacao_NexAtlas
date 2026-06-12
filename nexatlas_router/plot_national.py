"""Mapa NACIONAL da malha VFR — padrão visual do script original
(plotagem_special_routes.py), agora com a rota e os aeródromos destacados.

Diferença para plot_route.py:
  - plot_route.py  → zoom REGIONAL no subgrafo da rota (inspeção de perto).
  - plot_national.py → malha NACIONAL inteira (todos os ~1.033 waypoints e
    ~2.100 conexões do banco) sobre o contorno do Brasil, como o original.

Puxa a malha completa direto do banco (special_routes_*_v2). Opcionalmente
sobrepõe uma rota já calculada (saída do plan_v1_route) e marca os
aeródromos de origem/destino.

Uso (na sua máquina, com banco + internet para o GeoJSON):
    source .env.sh
    python3 -m nexatlas_router.plot_national            # só a malha nacional
    python3 -m nexatlas_router.plot_national SBMT SBJD  # malha + rota destacada
"""
from __future__ import annotations

import os
import sys
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection

# Paleta — idêntica ao script original
OCEAN = "#0f172a"
LAND = "#1e293b"
LAND_EDGE = "#334155"
NODE = "#06b6d4"
ROUTE = "#34d399"
ORIGIN_MK = "#fbbf24"
DEST_MK = "#f472b6"
ADHP_MK = "#f59e0b"     # aeródromos em geral (se sobrepostos)

GEOJSON_URL = ("https://raw.githubusercontent.com/johan/world.geo.json/"
               "master/countries.geo.json")

# SQL: puxa TODA a malha VFR (sem filtro de carta) com peso geodésico no banco
SQL_FULL_MESH = """
    SELECT ST_X(o.geom) AS lon_o, ST_Y(o.geom) AS lat_o,
           ST_X(d.geom) AS lon_d, ST_Y(d.geom) AS lat_d,
           ST_DistanceSphere(o.geom, d.geom) AS dist_m
    FROM special_routes_connections_v2 c
    JOIN special_routes_waypoints_v2 o ON o.id = c.source_id
    JOIN special_routes_waypoints_v2 d ON d.id = c.target_id;
"""


def _fetch_full_mesh(conn) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(SQL_FULL_MESH)
        return cur.fetchall()


def _plot_brazil(ax) -> None:
    import geopandas as gpd
    world = gpd.read_file(GEOJSON_URL)
    brazil = world[world["id"] == "BRA"]
    brazil.plot(ax=ax, color=LAND, edgecolor=LAND_EDGE, linewidth=1.5)


def plot_national(conn,
                  route_result=None,
                  output_path: str = "malha_nacional.png",
                  title: str = "Malha Aérea VFR — Brasil") -> str:
    """Plota a malha VFR nacional. Se route_result for passado, destaca a rota."""
    rows = _fetch_full_mesh(conn)

    fig, ax = plt.subplots(figsize=(12, 12), dpi=300)
    ax.set_facecolor(OCEAN)
    fig.patch.set_facecolor(OCEAN)
    _plot_brazil(ax)

    # --- nós (todos os waypoints, origem+destino de cada aresta) ----------
    pts_x = [r[0] for r in rows] + [r[2] for r in rows]
    pts_y = [r[1] for r in rows] + [r[3] for r in rows]
    ax.scatter(pts_x, pts_y, color=NODE, s=4, zorder=3, alpha=0.7,
               label="Waypoints (Nós)")

    # --- arestas (mapa de calor por distância, colormap plasma) -----------
    segments = [[(r[0], r[1]), (r[2], r[3])] for r in rows]
    distances = [r[4] for r in rows]
    norm = mcolors.Normalize(vmin=min(distances), vmax=max(distances))
    lc = LineCollection(segments, cmap=plt.cm.plasma, norm=norm,
                        linewidths=0.8, alpha=0.9, zorder=2)
    lc.set_array(distances)
    ax.add_collection(lc)

    cbar = fig.colorbar(lc, ax=ax, fraction=0.036, pad=0.04)
    cbar.set_label("Distância da Aresta (Metros)", color="white", fontsize=10)
    cbar.ax.yaxis.set_tick_params(color="white")
    plt.setp(plt.getp(cbar.ax.axes, "yticklabels"), color="white")

    # --- rota destacada + aeródromos (opcional) ---------------------------
    if route_result is not None:
        rlon = [p["lon"] for p in route_result.points]
        rlat = [p["lat"] for p in route_result.points]
        ax.plot(rlon, rlat, color=ROUTE, linewidth=2.5, zorder=4,
                solid_capstyle="round",
                label=f"Rota ({route_result.total_distance_nm:.1f} NM)")
        # aeródromos: origem (triângulo) e destino (estrela), grandes
        ax.scatter([rlon[0]], [rlat[0]], marker="^", s=260, color=ORIGIN_MK,
                   zorder=6, edgecolors="white", linewidths=1.5,
                   label=f"Origem ({route_result.points[0]['name'][:22]})")
        ax.scatter([rlon[-1]], [rlat[-1]], marker="*", s=420, color=DEST_MK,
                   zorder=6, edgecolors="white", linewidths=1.5,
                   label=f"Destino ({route_result.points[-1]['name'][:22]})")

    ax.set_title(title, color="white", fontsize=16, pad=15)
    ax.set_xlabel("Longitude", color="white")
    ax.set_ylabel("Latitude", color="white")
    ax.tick_params(colors="white")
    ax.set_xlim([-75, -34])
    ax.set_ylim([-34, 6])
    ax.legend(loc="upper right", facecolor=LAND, edgecolor="white",
              labelcolor="white")
    plt.tight_layout()
    plt.savefig(output_path, facecolor=fig.get_facecolor(),
                edgecolor="none", dpi=300)
    plt.close(fig)
    return output_path


def plot_route_with_context(conn, route_result,
                            output_path: str = "rota_contexto.png",
                            title: str = "Rota VFR",
                            margin_deg: float = 0.35) -> str:
    """Zoom na REGIÃO da rota, mantendo o contorno do Brasil ao fundo.

    Junta o contexto geográfico (Brasil desenhado) com a visibilidade da
    rota (zoom na caixa que contém origem, destino e waypoints da rota).
    Mostra só as arestas da malha que caem dentro da janela, para não
    poluir, e destaca a rota e os aeródromos.
    """
    rows = _fetch_full_mesh(conn)

    # caixa da rota (com folga)
    rlon = [p["lon"] for p in route_result.points]
    rlat = [p["lat"] for p in route_result.points]
    xmin, xmax = min(rlon) - margin_deg, max(rlon) + margin_deg
    ymin, ymax = min(rlat) - margin_deg, max(rlat) + margin_deg

    fig, ax = plt.subplots(figsize=(12, 12), dpi=300)
    ax.set_facecolor(OCEAN)
    fig.patch.set_facecolor(OCEAN)
    _plot_brazil(ax)  # Brasil ao fundo dá o contexto geográfico

    # malha local: só arestas com ao menos uma ponta dentro da janela
    def inside(lon, lat):
        return xmin <= lon <= xmax and ymin <= lat <= ymax
    local = [r for r in rows if inside(r[0], r[1]) or inside(r[2], r[3])]

    if local:
        pts_x = [r[0] for r in local] + [r[2] for r in local]
        pts_y = [r[1] for r in local] + [r[3] for r in local]
        ax.scatter(pts_x, pts_y, color=NODE, s=14, zorder=3, alpha=0.75,
                   label="Waypoints (Nós)")
        segments = [[(r[0], r[1]), (r[2], r[3])] for r in local]
        distances = [r[4] for r in local]
        norm = mcolors.Normalize(vmin=min(distances), vmax=max(distances))
        lc = LineCollection(segments, cmap=plt.cm.plasma, norm=norm,
                            linewidths=1.0, alpha=0.9, zorder=2)
        lc.set_array(distances)
        ax.add_collection(lc)
        cbar = fig.colorbar(lc, ax=ax, fraction=0.036, pad=0.04)
        cbar.set_label("Distância da Aresta (Metros)", color="white", fontsize=10)
        cbar.ax.yaxis.set_tick_params(color="white")
        plt.setp(plt.getp(cbar.ax.axes, "yticklabels"), color="white")

    # rota destacada
    ax.plot(rlon, rlat, color=ROUTE, linewidth=3.0, zorder=4,
            solid_capstyle="round",
            label=f"Rota ({route_result.total_distance_nm:.1f} NM)")
    # waypoints intermediários da rota, com nome
    for p in route_result.points[1:-1]:
        ax.scatter([p["lon"]], [p["lat"]], color=ROUTE, s=55, zorder=5,
                   edgecolors=OCEAN, linewidths=1.2)
        ax.annotate(p["name"], (p["lon"], p["lat"]),
                    textcoords="offset points", xytext=(7, 7),
                    color="white", fontsize=9, zorder=7)
    # aeródromos
    ax.scatter([rlon[0]], [rlat[0]], marker="^", s=320, color=ORIGIN_MK,
               zorder=6, edgecolors="white", linewidths=1.6,
               label=f"Origem ({route_result.points[0]['name'][:24]})")
    ax.scatter([rlon[-1]], [rlat[-1]], marker="*", s=520, color=DEST_MK,
               zorder=6, edgecolors="white", linewidths=1.6,
               label=f"Destino ({route_result.points[-1]['name'][:24]})")
    ax.annotate(route_result.points[0]["name"], (rlon[0], rlat[0]),
                textcoords="offset points", xytext=(8, -16),
                color=ORIGIN_MK, fontsize=10, fontweight="bold", zorder=7)
    ax.annotate(route_result.points[-1]["name"], (rlon[-1], rlat[-1]),
                textcoords="offset points", xytext=(8, 10),
                color=DEST_MK, fontsize=10, fontweight="bold", zorder=7)

    ax.set_title(title, color="white", fontsize=16, pad=15)
    ax.set_xlabel("Longitude", color="white")
    ax.set_ylabel("Latitude", color="white")
    ax.tick_params(colors="white")
    ax.set_xlim([xmin, xmax])
    ax.set_ylim([ymin, ymax])
    ax.legend(loc="upper right", facecolor=LAND, edgecolor="white",
              labelcolor="white")
    plt.tight_layout()
    plt.savefig(output_path, facecolor=fig.get_facecolor(),
                edgecolor="none", dpi=300)
    plt.close(fig)
    return output_path


# ------------------------------------------------------------------- CLI
def _main() -> None:
    import psycopg2
    from .db import PostgisLoader
    from .gwo import GWOConfig
    from .v1 import plan_v1_route
    from .resolver import CsvResolver

    conn = psycopg2.connect(
        host=os.environ["NEXATLAS_DB_HOST"],
        port=os.environ.get("NEXATLAS_DB_PORT", "5432"),
        dbname=os.environ["NEXATLAS_DB_NAME"],
        user=os.environ["NEXATLAS_DB_USER"],
        password=os.environ["NEXATLAS_DB_PASSWORD"],
    )

    route = None
    title = "Malha Aérea VFR — Brasil"
    if len(sys.argv) >= 3:
        origin_icao, dest_icao = sys.argv[1], sys.argv[2]
        csv_path = os.path.join(os.path.dirname(__file__), "..", "data",
                                "aerodromos_br_ourairports.csv")
        resolver = CsvResolver(csv_path)
        loader = PostgisLoader(conn, schema="v2")
        graph, meta = loader.build_subgraph(origin_icao, dest_icao,
                                            resolver=resolver)
        route = plan_v1_route(graph, meta["origin_id"], meta["dest_id"],
                              GWOConfig(seed=42, n_iterations=200, max_hops=40))
        title = f"Malha Aérea VFR — Brasil ({origin_icao} → {dest_icao})"

    out = "malha_nacional.png" if route is None else \
          f"malha_nacional_{sys.argv[1]}_{sys.argv[2]}.png"
    path = plot_national(conn, route_result=route, output_path=out, title=title)
    print(f"[Sucesso] Mapa nacional salvo em: {path}")

    # Quando há rota, gera também o mapa com ZOOM na região + Brasil ao fundo
    if route is not None:
        out2 = f"rota_contexto_{sys.argv[1]}_{sys.argv[2]}.png"
        path2 = plot_route_with_context(
            conn, route_result=route, output_path=out2,
            title=f"Rota VFR {sys.argv[1]} → {sys.argv[2]} (zoom + Brasil ao fundo)")
        print(f"[Sucesso] Mapa da rota (zoom) salvo em: {path2}")

    conn.close()


if __name__ == "__main__":
    _main()