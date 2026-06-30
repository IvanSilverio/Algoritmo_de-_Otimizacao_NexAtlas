"""Plotagem da rota V1 (esquema published).

Fundo escuro #0f172a, território #1e293b, waypoints REA em ciano. A rota
encontrada é destacada em verde, com origem (triângulo) e destino (estrela).

REGRA VISUAL (nova):
  * Arestas de corredor REA com is_mandatory=True  -> VERMELHO SÓLIDO (forte).
  * Arestas de corredor REA opcionais              -> AZUL/VERDE TRACEJADO.
  * Trechos sintéticos "DIRETO" não são desenhados como corredor.

NOTA: este módulo trabalha sobre o RouteGraph já carregado em memória — não
emite SQL. A migração de esquema (v2 -> published) está toda em db.py; aqui
apenas consumimos is_mandatory das arestas. (plot_national.py é quem mantém
as queries diretas e foi atualizado para published.special_routes_*.)
"""
from __future__ import annotations

from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from .graphmodel import RouteGraph
from .v1 import V1RouteResult

# Paleta
OCEAN = "#0f172a"
LAND = "#1e293b"
LAND_EDGE = "#334155"
NODE = "#06b6d4"
ROUTE = "#34d399"          # destaque da rota (verde-primavera)
ORIGIN_MK = "#fbbf24"      # âmbar
DEST_MK = "#f472b6"        # rosa
MANDATORY_EDGE = "#ef4444" # vermelho sólido — corredor obrigatório
OPTIONAL_EDGE = "#38bdf8"  # azul tracejado — corredor opcional


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
        pass


def plot_v1_route(graph: RouteGraph, result: V1RouteResult,
                  output_path: str = "rota_v1.png",
                  title: Optional[str] = None,
                  margin_deg: float = 0.12) -> str:
    fig, ax = plt.subplots(figsize=(12, 12), dpi=300)
    ax.set_facecolor(OCEAN)
    fig.patch.set_facecolor(OCEAN)
    _try_plot_brazil(ax)

    # ---- malha do subgrafo: cor por OBRIGATORIEDADE (regra visual nova) ----
    drew_mandatory = drew_optional = False
    for edges in graph.adj.values():
        for e in edges:
            if e.synthetic:
                continue  # trechos "DIRETO" não são corredor REA
            a, b = graph.nodes[e.source].pos, graph.nodes[e.target].pos
            if e.is_mandatory:
                # Obrigatório: vermelho sólido e grosso.
                ax.plot([a.lon, b.lon], [a.lat, b.lat],
                        color=MANDATORY_EDGE, linewidth=1.8, alpha=0.95,
                        zorder=2, solid_capstyle="round")
                drew_mandatory = True
            else:
                # Opcional: azul tracejado.
                ax.plot([a.lon, b.lon], [a.lat, b.lat],
                        color=OPTIONAL_EDGE, linewidth=1.0, alpha=0.8,
                        linestyle="--", zorder=2)
                drew_optional = True

    # ---- waypoints REA ----------------------------------------------------
    wlon = [n.pos.lon for n in graph.nodes.values() if n.kind == "waypoint"]
    wlat = [n.pos.lat for n in graph.nodes.values() if n.kind == "waypoint"]
    ax.scatter(wlon, wlat, color=NODE, s=10, zorder=3, alpha=0.7)

    # ---- rota encontrada (destaque) ---------------------------------------
    rlon = [p["lon"] for p in result.points]
    rlat = [p["lat"] for p in result.points]
    ax.plot(rlon, rlat, color=ROUTE, linewidth=3.0, zorder=4,
            solid_capstyle="round")
    ax.scatter(rlon[1:-1], rlat[1:-1], color=ROUTE, s=42, zorder=5,
               edgecolors=OCEAN, linewidths=1.2)

    # origem / destino
    ax.scatter([rlon[0]], [rlat[0]], marker="^", s=220, color=ORIGIN_MK,
               zorder=6, edgecolors=OCEAN, linewidths=1.5)
    ax.scatter([rlon[-1]], [rlat[-1]], marker="*", s=340, color=DEST_MK,
               zorder=6, edgecolors=OCEAN, linewidths=1.5)

    # nomes dos pontos intermediários
    for p in result.points[1:-1]:
        ax.annotate(p["name"], (p["lon"], p["lat"]),
                    textcoords="offset points", xytext=(6, 6),
                    color="white", fontsize=7, alpha=0.85, zorder=7)

    # ---- zoom regional ----------------------------------------------------
    all_lon = wlon + rlon
    all_lat = wlat + rlat
    ax.set_xlim(min(all_lon) - margin_deg, max(all_lon) + margin_deg)
    ax.set_ylim(min(all_lat) - margin_deg, max(all_lat) + margin_deg)

    # ---- legenda (construída manualmente p/ refletir a regra visual) ------
    handles = [
        Line2D([0], [0], color=ROUTE, lw=3,
               label=f"Rota V1 ({result.total_distance_nm:.1f} NM)"),
        Line2D([0], [0], marker="^", color="none", markerfacecolor=ORIGIN_MK,
               markersize=12, label=f"Origem ({result.points[0]['name']})"),
        Line2D([0], [0], marker="*", color="none", markerfacecolor=DEST_MK,
               markersize=16, label=f"Destino ({result.points[-1]['name']})"),
    ]
    if drew_mandatory:
        handles.append(Line2D([0], [0], color=MANDATORY_EDGE, lw=2,
                              label="Corredor obrigatório"))
    if drew_optional:
        handles.append(Line2D([0], [0], color=OPTIONAL_EDGE, lw=2, ls="--",
                              label="Corredor opcional"))
    ax.legend(handles=handles, loc="upper right", facecolor=LAND,
              edgecolor="white", labelcolor="white")

    ax.set_title(title or "Malha Aérea VFR — Rota V1",
                 color="white", fontsize=16, pad=15)
    ax.set_xlabel("Longitude", color="white")
    ax.set_ylabel("Latitude", color="white")
    ax.tick_params(colors="white")
    plt.tight_layout()
    plt.savefig(output_path, facecolor=fig.get_facecolor(),
                edgecolor="none", dpi=300)
    plt.close(fig)
    return output_path

# Paleta das rotas candidatas (distinta de rota/origem/destino/corredor)
ALT_COLORS = ["#c084fc", "#f97316", "#e879f9", "#fde047", "#5eead4"]


def plot_v1_alternatives(graph: RouteGraph, result: V1RouteResult,
                         output_path: str = "rota_v1_alternativas.png",
                         title: Optional[str] = None,
                         margin_deg: float = 0.12) -> Optional[str]:
    """Mapa dedicado às rotas CANDIDATAS (k-shortest): malha ao fundo atenuada,
    cada alternativa numa cor distinta (fina, semitransparente, com a distância
    na legenda) e a rota principal em verde por cima. Mantém o mapa principal
    limpo. Retorna None (sem gerar arquivo) se não houver alternativas.
    """
    alternatives = result.meta.get("alternatives", [])
    if not alternatives:
        return None

    fig, ax = plt.subplots(figsize=(12, 12), dpi=300)
    ax.set_facecolor(OCEAN)
    fig.patch.set_facecolor(OCEAN)
    _try_plot_brazil(ax)

    # malha REA ao fundo (atenuada)
    for edges in graph.adj.values():
        for e in edges:
            if e.synthetic:
                continue
            a, b = graph.nodes[e.source].pos, graph.nodes[e.target].pos
            if e.is_mandatory:
                ax.plot([a.lon, b.lon], [a.lat, b.lat], color=MANDATORY_EDGE,
                        linewidth=1.2, alpha=0.35, zorder=2)
            else:
                ax.plot([a.lon, b.lon], [a.lat, b.lat], color=OPTIONAL_EDGE,
                        linewidth=0.8, alpha=0.3, linestyle="--", zorder=2)

    wlon = [n.pos.lon for n in graph.nodes.values() if n.kind == "waypoint"]
    wlat = [n.pos.lat for n in graph.nodes.values() if n.kind == "waypoint"]
    ax.scatter(wlon, wlat, color=NODE, s=8, zorder=3, alpha=0.4)

    alt_handles = []
    for i, alt in enumerate(alternatives):
        color = ALT_COLORS[i % len(ALT_COLORS)]
        lon = [p["lon"] for p in alt["points"]]
        lat = [p["lat"] for p in alt["points"]]
        ax.plot(lon, lat, color=color, linewidth=1.8, alpha=0.85, zorder=4,
                solid_capstyle="round")
        ax.scatter(lon[1:-1], lat[1:-1], color=color, s=18, zorder=5,
                   edgecolors=OCEAN, linewidths=0.6, alpha=0.85)
        alt_handles.append(Line2D([0], [0], color=color, lw=2.2,
                           label=f"Alt {i + 1} — {alt['total_distance_nm']:.1f} NM"))

    # rota principal por cima de tudo
    rlon = [p["lon"] for p in result.points]
    rlat = [p["lat"] for p in result.points]
    ax.plot(rlon, rlat, color=ROUTE, linewidth=3.2, zorder=6,
            solid_capstyle="round")
    ax.scatter([rlon[0]], [rlat[0]], marker="^", s=220, color=ORIGIN_MK,
               zorder=7, edgecolors=OCEAN, linewidths=1.5)
    ax.scatter([rlon[-1]], [rlat[-1]], marker="*", s=340, color=DEST_MK,
               zorder=7, edgecolors=OCEAN, linewidths=1.5)

    all_lon = list(wlon) + rlon
    all_lat = list(wlat) + rlat
    for alt in alternatives:
        all_lon += [p["lon"] for p in alt["points"]]
        all_lat += [p["lat"] for p in alt["points"]]
    ax.set_xlim(min(all_lon) - margin_deg, max(all_lon) + margin_deg)
    ax.set_ylim(min(all_lat) - margin_deg, max(all_lat) + margin_deg)

    handles = (
        [Line2D([0], [0], color=ROUTE, lw=3,
                label=f"Principal — {result.total_distance_nm:.1f} NM")]
        + alt_handles
        + [Line2D([0], [0], marker="^", color="none", markerfacecolor=ORIGIN_MK,
                  markersize=12, label=f"Origem ({result.points[0]['name']})"),
           Line2D([0], [0], marker="*", color="none", markerfacecolor=DEST_MK,
                  markersize=16, label=f"Destino ({result.points[-1]['name']})")]
    )
    ax.legend(handles=handles, loc="upper right", facecolor=LAND,
              edgecolor="white", labelcolor="white", fontsize=9)

    ax.set_title(title or "Malha Aérea VFR — Rotas candidatas",
                 color="white", fontsize=16, pad=15)
    ax.set_xlabel("Longitude", color="white")
    ax.set_ylabel("Latitude", color="white")
    ax.tick_params(colors="white")
    plt.tight_layout()
    plt.savefig(output_path, facecolor=fig.get_facecolor(),
                edgecolor="none", dpi=300)
    plt.close(fig)
    return output_path