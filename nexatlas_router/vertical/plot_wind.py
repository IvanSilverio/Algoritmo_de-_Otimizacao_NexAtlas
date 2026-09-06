"""Sondagem vertical de vento — TODOS os níveis do dataset, num ponto e hora.

Ferramenta de EXPLORAÇÃO do dataset de vento (wind.py), independente de
qualquer rota V1/V3: mostra, para um único (lon, lat) e horário, a barbela de
vento (símbolo meteorológico padrão, `ax.barbs` nativo do matplotlib) em cada
nível de altitude que o CDN publica, com o terreno como referência de solo.
Não usa rota nem aeronave — não toca em profile.py/plot_profile.py.
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Optional

from .terrain import Terrain
from .wind import Wind

OCEAN = "#0f172a"; GRID = "#334155"
TERR_FILL = "#4b3f2a"; TERR_EDGE = "#a8823c"
BARB = "#38bdf8"; TXT = "#e2e8f0"


def _dir_de_onde_vem(u_kt: float, v_kt: float) -> float:
    """Direção meteorológica (graus, de onde o vento SOPRA; 0=norte, 90=leste).

    (u,v) é o vetor de deslocamento do ar (leste,norte) — "de onde vem" é o
    vetor oposto. Convenção verificada nos 4 quadrantes cardeais antes de
    implementar (u=0,v=+20 => 180°/sul; u=0,v=-20 => 0°/norte; u=+20,v=0 =>
    270°/oeste; u=-20,v=0 => 90°/leste).
    """
    return math.degrees(math.atan2(-u_kt, -v_kt)) % 360.0


def plot_wind_sounding(wind: Wind, terrain: Terrain, lon: float, lat: float,
                        hora_unix: float, output_path: str,
                        nome_local: Optional[str] = None) -> str:
    """Gera a sondagem vertical (todos os níveis reais do CDN) em `output_path`.

    Amostra `wind.vento_em` em cada nível de `wind.meta.levels` (não inventa
    níveis intermediários) e `terrain.elevation` no ponto exato (radius_px=0,
    mesma convenção de origem/destino usada no perfil de rota).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not wind.disponivel():
        raise RuntimeError(f"CDN de vento indisponível: {wind.erro}")

    niveis = sorted(wind.meta.levels)
    falhas_antes = wind.falhas
    amostras = []
    for nivel in niveis:
        u_kt, v_kt = wind.vento_em(lon, lat, nivel, hora_unix)
        vel_kt = math.hypot(u_kt, v_kt)
        dir_deg = _dir_de_onde_vem(u_kt, v_kt) if vel_kt > 1e-6 else 0.0
        amostras.append((nivel, u_kt, v_kt, vel_kt, dir_deg))
    n_falhas = wind.falhas - falhas_antes

    try:
        elev_ft = terrain.elevation(lon, lat, radius_px=0)
    except Exception:
        elev_ft = 0.0

    hora_real = wind._nearest(hora_unix, wind.meta.timestamps)

    top = max(niveis) * 1.08 + 500
    fig = plt.figure(figsize=(7.6, 11.0), dpi=200)
    fig.patch.set_facecolor(OCEAN)
    ax = fig.add_axes([0.15, 0.09, 0.52, 0.80])
    ax.set_facecolor(OCEAN)

    # --- terreno: faixa de solo até a elevação no ponto (ponto exato) ---
    ax.fill_between([-1, 1], 0, elev_ft, color=TERR_FILL, alpha=0.95, zorder=1)
    ax.axhline(elev_ft, color=TERR_EDGE, lw=1.2, zorder=1.2)

    # --- uma barbela por nível ACIMA do terreno local (coluna única em x=0).
    # Níveis abaixo do terreno não são desenháveis: o CDN de vento devolve um
    # valor (é uma grade atmosférica, não sabe onde está o chão), mas naquele
    # ponto essa altitude fica dentro do solo — mostrar a barbela ali sobre o
    # preenchimento do terreno seria enganoso (nada voa "dentro" da montanha).
    visiveis = [a for a in amostras if a[0] >= elev_ft]
    xs = [0.0] * len(visiveis)
    us = [a[1] for a in visiveis]
    vs = [a[2] for a in visiveis]
    ys = [a[0] for a in visiveis]
    ax.barbs(xs, ys, us, vs, length=8, pivot="tip", color=BARB, zorder=5)

    for nivel, u_kt, v_kt, vel_kt, dir_deg in visiveis:
        ax.annotate(f"{vel_kt:.0f} kt · {dir_deg:03.0f}°", (0.18, nivel),
                    xytext=(10, 0), textcoords="offset points",
                    va="center", ha="left", color=TXT, fontsize=8.5, zorder=6)

    ax.set_xlim(-1, 1)
    ax.set_ylim(-top * 0.03, top)
    ax.set_yticks(niveis)
    ax.set_yticklabels([f"{n:,.0f} ft".replace(",", ".") for n in niveis])
    ax.set_xticks([])
    ax.grid(True, axis="y", color=GRID, alpha=0.3, lw=0.6)
    ax.tick_params(colors="white")
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.set_ylabel("Altitude", color="white", labelpad=8)

    hora_pedida_txt = dt.datetime.fromtimestamp(hora_unix, dt.timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    hora_real_txt = dt.datetime.fromtimestamp(hora_real, dt.timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    local_txt = nome_local or f"{lon:.4f}, {lat:.4f}"

    titulo = f"Sondagem de vento — {local_txt}"
    sub = f"pedido: {hora_pedida_txt}  ·  usado (mais próximo, a cada 3h): {hora_real_txt}"
    if n_falhas:
        sub += f"  ·  ⚠ {n_falhas}/{len(niveis)} níveis sem dado (CDN)"
    ax.set_title(titulo + "\n" + sub, color="white", fontsize=12, pad=12, linespacing=1.5)

    fig.text(0.72, 0.80, "Local", color="white", fontsize=10, fontweight="bold", va="top")
    fig.text(0.72, 0.76, f"lon  {lon:.4f}\nlat  {lat:.4f}\nterreno  {elev_ft:.0f} ft",
             color=TXT, fontsize=9, va="top", family="monospace", linespacing=1.6)

    fig.savefig(output_path, facecolor=fig.get_facecolor(), edgecolor="none", dpi=200)
    plt.close(fig)
    return output_path
