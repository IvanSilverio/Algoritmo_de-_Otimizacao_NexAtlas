"""Plotagem da rota V1 (esquema published) — imagem única.

Fundo escuro #0f172a, território #1e293b, waypoints REA em ciano. A rota
principal é destacada em verde (mais espessa, por cima); até 3 alternativas
(k-shortest/Yen) aparecem em cores distintas, mais finas, por baixo. Abaixo do
mapa, um painel de texto lista os nomes dos vértices de cada rota, na mesma
cor da linha correspondente.

REGRA VISUAL:
  * Arestas de corredor REA com is_mandatory=True  -> VERMELHO SÓLIDO (forte).
  * Arestas de corredor REA opcionais              -> AZUL/VERDE TRACEJADO.
  * Trechos sintéticos "DIRETO" não são desenhados como corredor.

NOTA: este módulo trabalha sobre o RouteGraph já carregado em memória — não
emite SQL. A migração de esquema (v2 -> published) está toda em db.py; aqui
apenas consumimos is_mandatory das arestas. (plot_national.py é quem mantém
as queries diretas e foi atualizado para published.special_routes_*.)
"""
from __future__ import annotations

import textwrap
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
ROUTE = "#34d399"          # destaque da rota principal (verde-primavera)
ORIGIN_MK = "#fbbf24"      # âmbar
DEST_MK = "#f472b6"        # rosa
MANDATORY_EDGE = "#ef4444" # vermelho sólido — corredor obrigatório
OPTIONAL_EDGE = "#38bdf8"  # azul tracejado — corredor opcional

# Paleta das rotas candidatas (distinta de rota principal/origem/destino/corredor)
ALT_COLORS = ["#c084fc", "#f97316", "#e879f9", "#fde047", "#5eead4"]


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


def plot_v1_combined(graph: RouteGraph, result: V1RouteResult,
                     output_path: str = "rota_v1.png",
                     title: Optional[str] = None,
                     margin_deg: float = 0.12,
                     max_alternatives: int = 3) -> str:
    """Mapa único: malha REA + rota principal + até `max_alternatives`
    alternativas (k-shortest/Yen), com um painel de texto abaixo listando os
    vértices de cada rota (mesma cor da linha no mapa).
    """
    alternatives = result.meta.get("alternatives", [])[:max_alternatives]
    routes = [("Principal", ROUTE, result.total_distance_nm, result.points)]
    for i, alt in enumerate(alternatives):
        routes.append((f"Alt {i + 1}", ALT_COLORS[i % len(ALT_COLORS)],
                       alt["total_distance_nm"], alt["points"]))

    # ---- painel de texto: nomes dos vértices por rota ---------------------
    text_lines: list[tuple[Optional[str], str, bool]] = []
    for label, color, dist, pts in routes:
        text_lines.append((color, f"{label} — {dist:.1f} NM", True))
        names = " → ".join(p["name"] for p in pts)
        for wrapped in (textwrap.wrap(names, width=150) or [""]):
            text_lines.append((color, "    " + wrapped, False))
        text_lines.append((None, "", False))
    n_lines = len(text_lines)

    map_h = 11.0
    text_h = max(1.8, n_lines * 0.20)
    fig = plt.figure(figsize=(13, map_h + text_h + 1.0), dpi=300)
    fig.patch.set_facecolor(OCEAN)
    gs = fig.add_gridspec(2, 1, height_ratios=[map_h, text_h], hspace=0.25)
    ax = fig.add_subplot(gs[0])
    ax_txt = fig.add_subplot(gs[1])
    ax.set_facecolor(OCEAN)
    ax_txt.set_facecolor(OCEAN)
    ax_txt.axis("off")
    _try_plot_brazil(ax)

    # ---- malha do subgrafo: cor por OBRIGATORIEDADE ------------------------
    drew_mandatory = drew_optional = False
    for edges in graph.adj.values():
        for e in edges:
            if e.synthetic:
                continue  # trechos "DIRETO" não são corredor REA
            a, b = graph.nodes[e.source].pos, graph.nodes[e.target].pos
            if e.is_mandatory:
                ax.plot([a.lon, b.lon], [a.lat, b.lat],
                        color=MANDATORY_EDGE, linewidth=1.6, alpha=0.9,
                        zorder=2, solid_capstyle="round")
                drew_mandatory = True
            else:
                ax.plot([a.lon, b.lon], [a.lat, b.lat],
                        color=OPTIONAL_EDGE, linewidth=0.9, alpha=0.7,
                        linestyle="--", zorder=2)
                drew_optional = True

    # ---- waypoints REA ------------------------------------------------------
    wlon = [n.pos.lon for n in graph.nodes.values() if n.kind == "waypoint"]
    wlat = [n.pos.lat for n in graph.nodes.values() if n.kind == "waypoint"]
    ax.scatter(wlon, wlat, color=NODE, s=9, zorder=3, alpha=0.6)

    # ---- rotas: alternativas por baixo, principal por cima ----------------
    all_lon = list(wlon)
    all_lat = list(wlat)
    for label, color, dist, pts in reversed(routes):
        lon = [p["lon"] for p in pts]
        lat = [p["lat"] for p in pts]
        all_lon += lon
        all_lat += lat
        is_main = label == "Principal"
        ax.plot(lon, lat, color=color,
                linewidth=3.2 if is_main else 1.9,
                alpha=1.0 if is_main else 0.88,
                zorder=6 if is_main else 4, solid_capstyle="round")
        ax.scatter(lon[1:-1], lat[1:-1], color=color,
                   s=42 if is_main else 20, zorder=7 if is_main else 5,
                   edgecolors=OCEAN, linewidths=1.2 if is_main else 0.6,
                   alpha=1.0 if is_main else 0.88)

    # origem / destino (da rota principal)
    ax.scatter([result.points[0]["lon"]], [result.points[0]["lat"]],
               marker="^", s=220, color=ORIGIN_MK, zorder=8,
               edgecolors=OCEAN, linewidths=1.5)
    ax.scatter([result.points[-1]["lon"]], [result.points[-1]["lat"]],
               marker="*", s=340, color=DEST_MK, zorder=8,
               edgecolors=OCEAN, linewidths=1.5)

    # nomes no mapa: só a rota principal (4 rotas sobrepostas poluiriam)
    for p in result.points[1:-1]:
        ax.annotate(p["name"], (p["lon"], p["lat"]),
                    textcoords="offset points", xytext=(6, 6),
                    color="white", fontsize=7, alpha=0.85, zorder=9)

    # margem extra no topo/direita: dá "céu" livre para a legenda (upper right)
    # não cobrir malha/rotas, independente do formato da rota encontrada.
    ax.set_xlim(min(all_lon) - margin_deg, max(all_lon) + margin_deg * 2.5)
    ax.set_ylim(min(all_lat) - margin_deg, max(all_lat) + margin_deg * 4.0)

    # ---- legenda ------------------------------------------------------------
    handles = [Line2D([0], [0], color=color, lw=2.6 if label == "Principal" else 2.2,
                      label=f"{label} — {dist:.1f} NM")
               for label, color, dist, _ in routes]
    handles += [
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
              edgecolor="white", labelcolor="white", fontsize=8.5)

    ax.set_title(title or "Malha Aérea VFR — Rota V1 e Alternativas",
                 color="white", fontsize=16, pad=15)
    ax.set_xlabel("Longitude", color="white", labelpad=10)
    ax.set_ylabel("Latitude", color="white")
    ax.tick_params(colors="white")

    # ---- painel de texto ----------------------------------------------------
    ax_txt.set_xlim(0, 1)
    ax_txt.set_ylim(0, n_lines)
    for i, (color, text, is_header) in enumerate(text_lines):
        if not text:
            continue
        y = n_lines - i - 0.5
        ax_txt.text(0.005, y, text, color=color or "white",
                    fontsize=9.5 if is_header else 8.2,
                    fontweight="bold" if is_header else "normal",
                    family="monospace", va="center", ha="left")

    # ajuste manual (em vez de tight_layout): preserva o hspace generoso do
    # gridspec — tight_layout tende a espremê-lo e o rótulo "Longitude" volta
    # a colidir com a 1ª linha do painel de texto.
    fig.subplots_adjust(left=0.055, right=0.985, top=0.965, bottom=0.02,
                        hspace=0.25)
    plt.savefig(output_path, facecolor=fig.get_facecolor(),
                edgecolor="none", dpi=300)
    plt.close(fig)
    return output_path