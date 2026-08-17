"""Gráfico do perfil vertical a partir da LISTA DE VÉRTICES (reais + virtuais).

Desenha o caminho em DEGRAUS: sobe na razão máxima até o ponto virtual, nivela,
sobe/desce de novo — nunca rampa linear ao longo da perna. Corredores em verde,
transições/cruzeiro em azul. Pontos de mudança de altitude marcados com letra
(A, B, C…) e legenda por fora; TOC/TOD sobre a linha do cruzeiro. Terreno por
baixo. Não toca no motor e não precisa do CDN (silhueta já vem no perfil).
"""
from __future__ import annotations

from typing import Optional

OCEAN = "#0f172a"; GRID = "#334155"
TERR_FILL = "#4b3f2a"; TERR_EDGE = "#a8823c"
CORR = "#34d399"; FREE = "#38bdf8"; TOCTOD = "#f472b6"; STEEP = "#ef4444"
WP = "#e2e8f0"; ORIGIN_MK = "#fbbf24"; DEST_MK = "#f472b6"


def _letter(i: int) -> str:
    s = ""; i += 1
    while i > 0:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def plot_vertical_profile(perfil, output_path: str, titulo: Optional[str] = None) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    V = perfil.vertices
    if not V:
        raise ValueError("perfil sem vértices para plotar")
    xs = [v.x_nm for v in V]
    ys = [v.alt_ft for v in V]
    total = xs[-1] if xs else 1.0
    corr_ranges = perfil.diag.get("corredores_x", [])

    def in_corr(x):
        return any(a - 1e-9 <= x <= b + 1e-9 for a, b in corr_ranges)

    fig = plt.figure(figsize=(17.0, 6.6), dpi=200)
    fig.patch.set_facecolor(OCEAN)
    ax = fig.add_axes([0.052, 0.16, 0.70, 0.70])
    ax.set_facecolor(OCEAN)

    # --- terreno ---
    terr_max = 0.0
    if perfil.terreno_perfil:
        tx = [d for d, _ in perfil.terreno_perfil]
        ty = [e for _, e in perfil.terreno_perfil]
        ax.fill_between(tx, 0, ty, color=TERR_FILL, alpha=0.95, zorder=1)
        ax.plot(tx, ty, color=TERR_EDGE, lw=1.0, zorder=1.2)
        terr_max = max(ty) if ty else 0.0
    top = max(max(ys), terr_max, perfil.cruzeiro_ft) * 1.18 + 500

    # --- linhas horizontais nas altitudes visitadas (corredores + cruzeiro) ---
    niveis = {round(perfil.cruzeiro_ft)}
    for (a, b) in corr_ranges:
        for v in V:
            if a - 1e-9 <= v.x_nm <= b + 1e-9 and v.real:
                niveis.add(round(v.alt_ft))
    niveis = sorted(niveis)
    dedup = []
    for nlv in niveis:
        if not dedup or nlv - dedup[-1] >= 250:
            dedup.append(nlv)
    for nlv in dedup:
        ax.axhline(nlv, color=GRID, ls="--", lw=0.7, alpha=0.5, zorder=1.5)
    yt = sorted(set(dedup + [round(perfil.origem_elev_ft), round(perfil.destino_elev_ft)]))
    ax.set_yticks(yt)
    ax.set_yticklabels([f"{v} ft" for v in yt])

    # --- caminho em degraus (segmento a segmento entre vértices) ---
    # Um segmento é "de corredor" (verde) se o seu meio cai numa faixa de corredor,
    # OU se é NIVELADO na altitude de um corredor e está FORA do cruzeiro (TOC–TOD):
    # esse é o trecho reto que voa no nível do corredor entrando/saindo dele (após a
    # aeronave atingir a altitude num ponto virtual). O cruzeiro (entre TOC e TOD)
    # permanece azul mesmo que sua altitude coincida com a de um corredor.
    corr_alts = {round(v.alt_ft) for v in V if v.tipo == "corredor"}
    _toc, _tod = perfil.toc_nm, perfil.tod_nm
    _ingreme = perfil.descida_ingreme_nm

    def _is_corr_seg(A, B, mid):
        if in_corr(mid):
            return True
        if (abs(B.alt_ft - A.alt_ft) <= 1 and round(A.alt_ft) in corr_alts
                and not (_toc - 1e-6 <= mid <= _tod + 1e-6)):
            return True
        return False

    def _is_ingreme_seg(mid):
        return any(x0 - 1e-6 <= mid <= x1 + 1e-6 for x0, x1 in _ingreme)

    for A, B in zip(V, V[1:]):
        mid = (A.x_nm + B.x_nm) / 2.0
        if _is_ingreme_seg(mid):
            col = STEEP
        elif _is_corr_seg(A, B, mid):
            col = CORR
        else:
            col = FREE
        lw = 3.6 if col == CORR else 2.4
        ax.plot([A.x_nm, B.x_nm], [A.alt_ft, B.alt_ft], color=col, lw=lw,
                solid_capstyle="round", zorder=4)

    # --- rótulo de altitude dos corredores (dedup adjacente) ---
    last = None
    for A, B in zip(V, V[1:]):
        mid = (A.x_nm + B.x_nm) / 2.0
        if in_corr(mid) and abs(B.alt_ft - A.alt_ft) <= 1:      # trecho nivelado dentro de corredor
            lbl = f"{A.alt_ft:.0f}"
            if lbl != last:
                ax.annotate(lbl, (mid, A.alt_ft), xytext=(0, 7), textcoords="offset points",
                            ha="center", color=CORR, fontsize=8, zorder=6)
            last = lbl

    # --- letras nos pontos REAIS onde a altitude muda vs. o real anterior ---
    reais = [v for v in V if v.real and v.tipo in ("corredor", "ponto")]
    corners = []
    prev_alt = perfil.origem_elev_ft
    for v in reais:
        if abs(v.alt_ft - prev_alt) > 1:
            corners.append(v)
        prev_alt = v.alt_ft
    for idx, v in enumerate(corners):
        L = _letter(idx)
        ax.plot([v.x_nm, v.x_nm], [0, v.alt_ft], color=GRID, ls=":", lw=0.7, alpha=0.55, zorder=1.6)
        ax.scatter([v.x_nm], [v.alt_ft], s=18, color=WP, zorder=6, edgecolors=OCEAN, linewidths=0.8)
        ax.annotate(L, (v.x_nm, 0), xytext=(0, -3), textcoords="offset points",
                    ha="center", va="top", color=WP, fontsize=9, fontweight="bold", zorder=6)

    # --- TOC / TOD sobre a linha (usa toc_nm/tod_nm do perfil — fonte autoritativa,
    #     igual ao terminal; a altitude aí é o cruzeiro/ponto mais alto) ---
    toc, tod, alt = perfil.toc_nm, perfil.tod_nm, perfil.cruzeiro_ft
    if abs(tod - toc) < 0.5:                     # TOC e TOD coincidem: um marcador só
        ax.scatter([toc], [alt], s=72, facecolor="white", edgecolors=TOCTOD,
                   linewidths=2.2, zorder=9)
        ax.annotate("TOC/TOD", (toc, alt), xytext=(0, 12), textcoords="offset points",
                    ha="center", va="bottom", color=TOCTOD, fontsize=11, fontweight="bold", zorder=9)
    else:
        for x, lbl in ((toc, "TOC"), (tod, "TOD")):
            ax.scatter([x], [alt], s=72, facecolor="white", edgecolors=TOCTOD,
                       linewidths=2.2, zorder=9)
            ax.annotate(lbl, (x, alt), xytext=(0, 12), textcoords="offset points",
                        ha="center", va="bottom", color=TOCTOD, fontsize=11, fontweight="bold", zorder=9)

    # --- origem / destino ---
    ax.scatter([0], [perfil.origem_elev_ft], marker="^", s=160, color=ORIGIN_MK,
               zorder=10, edgecolors=OCEAN, linewidths=1.3)
    ax.scatter([total], [perfil.destino_elev_ft], marker="*", s=280, color=DEST_MK,
               zorder=10, edgecolors=OCEAN, linewidths=1.3)
    o_name = V[0].nome or "origem"; d_name = V[-1].nome or "destino"
    ax.annotate(f"{o_name}\n{perfil.origem_elev_ft:.0f} ft", (0, perfil.origem_elev_ft),
                xytext=(6, 2), textcoords="offset points", ha="left", va="bottom",
                color="white", fontsize=9, fontweight="bold", zorder=10)
    ax.annotate(f"{d_name}\n{perfil.destino_elev_ft:.0f} ft", (total, perfil.destino_elev_ft),
                xytext=(-6, 2), textcoords="offset points", ha="right", va="bottom",
                color="white", fontsize=9, fontweight="bold", zorder=10)

    ax.set_xlim(-total * 0.04, total * 1.04)
    ax.set_ylim(-top * 0.06, top)
    ax.set_xlabel("Distância (NM)", color="white", labelpad=8)
    ax.set_ylabel("Altitude", color="white", labelpad=8)
    ax.grid(True, axis="x", color=GRID, alpha=0.25, lw=0.6)
    ax.tick_params(colors="white")
    for s in ax.spines.values():
        s.set_color(GRID)

    sub = (f"{perfil.aeronave}  ·  cruzeiro {perfil.cruzeiro_ft:.0f} ft  ·  "
           f"subida {perfil.subida.tempo_min:.0f} + cruzeiro {perfil.cruzeiro.tempo_min:.0f} + "
           f"descida {perfil.descida.tempo_min:.0f} = {perfil.tempo_total_min:.0f} min")
    ax.set_title((titulo or "Perfil vertical") + "\n" + sub, color="white",
                 fontsize=13, pad=12, linespacing=1.5)

    handles = [
        Line2D([0], [0], color=CORR, lw=3.4, label="Corredor REA (no higher_limit)"),
        Line2D([0], [0], color=FREE, lw=2.4, label="Subida / cruzeiro / descida"),
        Line2D([0], [0], marker="o", color="none", markerfacecolor="white",
               markeredgecolor=TOCTOD, markeredgewidth=2, markersize=9, label="TOC / TOD"),
    ]
    if _ingreme:
        handles.append(Line2D([0], [0], color=STEEP, lw=2.4,
                               label="Descida acima da razão (atenção)"))
    ax.legend(handles=handles, loc="upper center", ncol=len(handles), facecolor="#1e293b",
              edgecolor=GRID, labelcolor="white", fontsize=8.4, framealpha=0.9)

    # --- legenda dos pontos (letra -> waypoint) por fora ---
    fig.text(0.775, 0.86, "Pontos de mudança de altitude", color="white",
             fontsize=10, fontweight="bold", va="top", ha="left")
    linhas = [f"{_letter(i)}  —  {v.nome}" for i, v in enumerate(corners)]
    linhas += ["", f"△  —  {o_name} (origem)", f"★  —  {d_name} (destino)"]
    fig.text(0.775, 0.81, "\n".join(linhas), color=WP, fontsize=9,
             va="top", ha="left", family="monospace", linespacing=1.5)

    fig.savefig(output_path, facecolor=fig.get_facecolor(), edgecolor="none", dpi=200)
    plt.close(fig)
    return output_path