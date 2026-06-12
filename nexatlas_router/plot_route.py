"""Plotagem da rota V1 — mesmo padrão visual do script nacional
(plotagem_special_routes.py): fundo #0f172a, território #1e293b, waypoints
ciano, arestas coloridas por distância (colormap plasma).

Acréscimos sobre o padrão:
  * Rota encontrada destacada em verde-primavera com linha grossa.
  * Origem (triângulo) e destino (estrela) marcados.
  * Zoom automático na caixa do subgrafo (a V1 é regional; o mapa nacional
    não mostra nada útil de uma rota de 30 NM). O contorno do Brasil é
    opcional e só carrega se geopandas estiver disponível.

Uso integrado (com banco):
    from nexatlas_router.plot_route import plot_v1_route
    plot_v1_route(graph, result, "rota_v1.png")

Uso standalone de demonstração (sem banco):
    python -m nexatlas_router.plot_route
"""
from __future__ import annotations

from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D

from .graphmodel import RouteGraph
from .v1 import V1RouteResult

# Paleta — idêntica ao script nacional
OCEAN = "#0f172a"
LAND = "#1e293b"
LAND_EDGE = "#334155"
NODE = "#06b6d4"
ROUTE = "#34d399"        # destaque da rota (verde-primavera)
ORIGIN_MK = "#fbbf24"    # âmbar
DEST_MK = "#f472b6"      # rosa


def _try_plot_brazil(ax) -> None:
    """Contorno do Brasil (opcional; requer geopandas + internet)."""
    try:
        import geopandas as gpd
        url = ("https://raw.githubusercontent.com/johan/world.geo.json/"
               "master/countries.geo.json")
        world = gpd.read_file(url)
        world[world["id"] == "BRA"].plot(
            ax=ax, color=LAND, edgecolor=LAND_EDGE, linewidth=1.5)
    except Exception:
        pass  # sem basemap: o subgrafo continua legível no fundo escuro


def plot_v1_route(graph: RouteGraph, result: V1RouteResult,
                  output_path: str = "rota_v1.png",
                  title: Optional[str] = None,
                  margin_deg: float = 0.12) -> str:
    fig, ax = plt.subplots(figsize=(12, 12), dpi=300)
    ax.set_facecolor(OCEAN)
    fig.patch.set_facecolor(OCEAN)
    _try_plot_brazil(ax)

    # ---- malha do subgrafo (arestas reais, colormap plasma) --------------
    segments, distances = [], []
    for edges in graph.adj.values():
        for e in edges:
            if e.synthetic:
                continue
            a, b = graph.nodes[e.source].pos, graph.nodes[e.target].pos
            segments.append([(a.lon, a.lat), (b.lon, b.lat)])
            distances.append(e.weight_m)

    if segments:
        norm = mcolors.Normalize(vmin=min(distances), vmax=max(distances))
        lc = LineCollection(segments, cmap=plt.cm.plasma, norm=norm,
                            linewidths=0.8, alpha=0.9, zorder=2)
        lc.set_array(distances)
        ax.add_collection(lc)
        cbar = fig.colorbar(lc, ax=ax, fraction=0.036, pad=0.04)
        cbar.set_label("Distância da Aresta (Metros)", color="white", fontsize=10)
        cbar.ax.yaxis.set_tick_params(color="white")
        plt.setp(plt.getp(cbar.ax.axes, "yticklabels"), color="white")

    # ---- waypoints --------------------------------------------------------
    wlon = [n.pos.lon for n in graph.nodes.values() if n.kind == "waypoint"]
    wlat = [n.pos.lat for n in graph.nodes.values() if n.kind == "waypoint"]
    ax.scatter(wlon, wlat, color=NODE, s=10, zorder=3, alpha=0.7,
               label="Waypoints (Nós)")

    # ---- rota encontrada (destaque) ---------------------------------------
    rlon = [p["lon"] for p in result.points]
    rlat = [p["lat"] for p in result.points]
    ax.plot(rlon, rlat, color=ROUTE, linewidth=3.0, zorder=4,
            solid_capstyle="round",
            label=f"Rota V1 ({result.total_distance_nm:.1f} NM)")
    ax.scatter(rlon[1:-1], rlat[1:-1], color=ROUTE, s=42, zorder=5,
               edgecolors=OCEAN, linewidths=1.2)

    # origem / destino
    ax.scatter([rlon[0]], [rlat[0]], marker="^", s=220, color=ORIGIN_MK,
               zorder=6, edgecolors=OCEAN, linewidths=1.5,
               label=f"Origem ({result.points[0]['name']})")
    ax.scatter([rlon[-1]], [rlat[-1]], marker="*", s=340, color=DEST_MK,
               zorder=6, edgecolors=OCEAN, linewidths=1.5,
               label=f"Destino ({result.points[-1]['name']})")

    # nomes dos pontos da rota (fonia)
    for p in result.points[1:-1]:
        ax.annotate(p["name"], (p["lon"], p["lat"]),
                    textcoords="offset points", xytext=(6, 6),
                    color="white", fontsize=7, alpha=0.85, zorder=7)

    # ---- zoom regional ----------------------------------------------------
    all_lon = wlon + rlon
    all_lat = wlat + rlat
    ax.set_xlim(min(all_lon) - margin_deg, max(all_lon) + margin_deg)
    ax.set_ylim(min(all_lat) - margin_deg, max(all_lat) + margin_deg)

    # ---- aviso de dado fictício (se houver terminal FICTICIO_) -----------
    has_fake = any(p["name"].startswith("FICTICIO_") for p in result.points)
    if has_fake:
        ax.text(0.5, 0.5, "DADO DE DEMONSTRAÇÃO\nterminais fictícios",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=30, color="#ef4444", alpha=0.18,
                rotation=25, fontweight="bold", zorder=10)

    ax.set_title(title or "Malha Aérea VFR — Rota V1 (GWO)",
                 color="white", fontsize=16, pad=15)
    ax.set_xlabel("Longitude", color="white")
    ax.set_ylabel("Latitude", color="white")
    ax.tick_params(colors="white")
    ax.legend(loc="upper right", facecolor=LAND, edgecolor="white",
              labelcolor="white")
    plt.tight_layout()
    plt.savefig(output_path, facecolor=fig.get_facecolor(),
                edgecolor="none", dpi=300)
    plt.close(fig)
    return output_path


# ---------------------------------------------------------------- demo CLI
def _demo() -> None:
    from .demo import build_synthetic_chart
    from .gwo import GWOConfig
    from .v1 import plan_v1_route

    g, o, d = build_synthetic_chart()
    result = plan_v1_route(g, o, d,
                           GWOConfig(seed=0, n_iterations=200, max_hops=40))
    path = plot_v1_route(g, result, "rota_v1_demo.png",
                         title="Malha Aérea VFR — Rota V1 (demo sintético)")
    print(f"[Sucesso] Gráfico salvo em: {path}")
    print(f"Rota: {' -> '.join(p['name'] for p in result.points)}")


if __name__ == "__main__":
    _demo()
