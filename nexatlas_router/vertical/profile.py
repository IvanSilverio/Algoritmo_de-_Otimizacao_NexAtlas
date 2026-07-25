"""Motor do perfil vertical V3 (sobre a rota lateral pronta).

Modelo (alinhado às reuniões de 15 e 22/jul, explicação do Cristiano):
  - Corredores são voados no seu higher_limit; as transições entre altitudes
    são feitas na RAZÃO MÁXIMA da aeronave (não em rampa linear), atingindo a
    altitude-alvo num PONTO VIRTUAL logo à frente e nivelando até a próxima
    transição — o conceito de "start".
  - O trecho de cruzeiro é a maior sequência de pernas FORA de corredor. Nele a
    aeronave sobe de onde parou (H_pre) até a altitude de cruzeiro (TOC), nivela,
    e desce até o próximo alvo (H_post). A reentrada no 1º corredor de chegada é
    "cross" (chega nele já no higher_limit) — esse ponto de início da descida é
    o TOD.
  - TOC/TOD são o 1º/último ponto que atinge o cruzeiro; se a rota não comporta
    cruzeiro, viram o início/fim do segmento mais alto.
  - A altitude de cruzeiro vem de cruise.suggest_cruise_altitude (documento
    "Cálculo da Altitude de Cruzeiro").

A saída é uma LISTA DE VÉRTICES (reais + virtuais) — fonte única do gráfico e do
JSON. Tempo somado por segmento (subida/cruzeiro/descida nas velocidades da fase).
Terreno é injetado (elevation/max_along) e usado só como AVISO se o perfil furar
o relevo. Consome apenas o contrato LateralRoute.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .aircraft import Aeronave
from .contract import LateralRoute, LateralLeg
from .magnetic import magnetic_bearing
from .cruise import suggest_cruise_altitude
from . import rules


# --------------------------------------------------------------------------- tempo
@dataclass
class FaseTempo:
    dist_nm: float
    tempo_min: float


# --------------------------------------------------------------------------- vértices
@dataclass
class Vertice:
    x_nm: float               # distância acumulada desde a origem
    alt_ft: float             # altitude neste ponto
    tipo: str                 # origem|destino|corredor|ponto|virtual|toc|tod
    nome: Optional[str]       # nome do waypoint (None nos virtuais)
    real: bool                # True se é ponto real da rota (mostrado ao piloto)


@dataclass
class PerfilVertical:
    aeronave: str
    cruzeiro_ft: float
    alcancou_cruzeiro: bool
    origem_elev_ft: float
    destino_elev_ft: float
    vertices: list            # list[Vertice] — fonte única (reais + virtuais)
    toc_nm: float
    tod_nm: float
    subida: FaseTempo
    cruzeiro: FaseTempo
    descida: FaseTempo
    terreno_perfil: list = field(default_factory=list)   # [(dist_nm, elev_ft)]
    avisos: list = field(default_factory=list)
    diag: dict = field(default_factory=dict)

    @property
    def tempo_total_min(self) -> float:
        return self.subida.tempo_min + self.cruzeiro.tempo_min + self.descida.tempo_min


# --------------------------------------------------------------- helpers de geometria
def _leg_lonlat(leg: LateralLeg):
    return [(leg.from_pos.lon, leg.from_pos.lat), (leg.to_pos.lon, leg.to_pos.lat)]


def _trans_dist(dalt_ft: float, rate_fpm: float, speed_kt: float) -> float:
    """Distância horizontal (NM) para variar |dalt| ft na razão/velocidade dadas."""
    if rate_fpm <= 0 or speed_kt <= 0:
        return 0.0
    return abs(dalt_ft) / rate_fpm / 60.0 * speed_kt


def _longest_free_run(legs) -> tuple[int, int]:
    """Sequência contígua de pernas FORA de corredor com MAIOR DISTÂNCIA -> [ini, fim).

    Medir por distância (não por número de pernas) é essencial: um único DIRETO
    en-route de 1725 NM deve vencer um DIRETO de saída de 15 NM.
    """
    best = (0, 0, -1.0)          # (ini, fim, distância)
    run_start = None
    run_dist = 0.0
    for i, l in enumerate(legs):
        if not l.is_corridor:
            if run_start is None:
                run_start, run_dist = i, 0.0
            run_dist += l.distance_nm
        else:
            if run_start is not None and run_dist > best[2]:
                best = (run_start, i, run_dist)
            run_start = None
    if run_start is not None and run_dist > best[2]:
        best = (run_start, len(legs), run_dist)
    return best[0], best[1]


# --------------------------------------------------------------------------- API
def plan_vertical_profile(lateral: LateralRoute, aeronave: Aeronave, terreno, *,
                          margem_ft: float = rules.CLEARANCE_FT,
                          step_nm: float = rules.STEP_NM,
                          radius_px: int = rules.RADIUS_PX) -> PerfilVertical:
    legs = list(lateral.legs)
    avisos: list[str] = []
    ac = aeronave

    # Elevação de origem/destino: terreno no ponto (radius_px=0). (Futuro: cota do aeródromo.)
    def _elev(pos):
        try:
            return float(terreno.elevation(pos.lon, pos.lat, radius_px=0))
        except Exception as e:
            avisos.append(f"terreno indisponível na origem/destino ({e}); usei 0 ft")
            return 0.0
    elev_o = _elev(lateral.origin_pos)
    elev_d = _elev(lateral.dest_pos)

    n = len(legs)
    cum = [0.0]
    for l in legs:
        cum.append(cum[-1] + l.distance_nm)
    total = cum[-1]

    # ---- trecho de cruzeiro = maior corrida fora de corredor ----
    cs, ce = _longest_free_run(legs)
    tem_cruise_stretch = ce > cs

    # ---- altitude de cruzeiro (documento) ----
    # Voo IFR (a V3 acrescenta altitude/cruzeiro): usa o teto OPERACIONAL cheio da
    # aeronave e os níveis IFR (a spec enquadra em milhar conforme a regra).
    # A distância passada à spec é a do TRECHO EN-ROUTE (onde o cruzeiro de fato
    # acontece), não a total — senão o cruzeiro fica alto demais e a aeronave sobe
    # mais do que cruza. Sem corredor (DIRETO puro) o en-route é a rota inteira.
    dist_cruz = (cum[ce] - cum[cs]) if tem_cruise_stretch else total
    route_dir = magnetic_bearing(lateral.origin_pos, lateral.dest_pos)[0]
    cruise = suggest_cruise_altitude(
        dist_cruz, route_dir, elev_o, elev_d, ac.teto_ft,
        ac.rate_ac_fpm, ac.rate_dc_fpm, ac.speed_ac_kt, ac.speed_dc_kt)
    fonte_cruz = (f"spec(dist_en-route {dist_cruz:.0f}NM de {total:.0f} total, rumo "
                  f"{route_dir:.0f}°, teto {ac.teto_ft:.0f}) → {cruise}"
                  if cruise is not None else "spec → None (rota curta)")

    # helper: his de corredor (ou None)
    def his(i):
        l = legs[i]
        return float(l.higher_limit_ft) if (l.is_corridor and l.higher_limit_ft) else None

    def next_corr_his(i, hi_lim):
        for j in range(i, hi_lim):
            h = his(j)
            if h is not None:
                return h
        return None

    # H_pre: altitude ao fim da pré-região (início do trecho de cruzeiro)
    if tem_cruise_stretch and cs > 0:
        h = None
        for j in range(cs - 1, -1, -1):
            if his(j) is not None:
                h = his(j); break
        H_pre = h if h is not None else elev_o
    else:
        H_pre = elev_o
    # H_post: altitude ao início da pós-região (fim do trecho de cruzeiro)
    if tem_cruise_stretch and ce < n:
        H_post = next_corr_his(ce, n)
        if H_post is None:
            H_post = elev_d
    else:
        H_post = elev_d

    vertices: list[Vertice] = []

    def add(x, alt, tipo, nome=None, real=False):
        vertices.append(Vertice(round(float(x), 4), round(float(alt), 2), tipo, nome, real))

    # transição "start" a partir de (x0,alt0) rumo a alt_target, dentro de [x0,x1];
    # devolve altitude ao final (em x1). Insere ponto virtual se atingir antes de x1.
    def start_to(x0, alt0, x1, alt_target):
        if abs(alt_target - alt0) <= 1:
            return alt0
        if alt_target > alt0:
            d = _trans_dist(alt_target - alt0, ac.rate_ac_fpm, ac.speed_ac_kt)
        else:
            d = _trans_dist(alt0 - alt_target, ac.rate_dc_fpm, ac.speed_dc_kt)
        if d <= (x1 - x0) + 1e-9:
            add(x0 + d, alt_target, "virtual")
            return alt_target
        frac = (x1 - x0) / d if d > 0 else 1.0
        return alt0 + (alt_target - alt0) * frac        # rampa parcial (carrega)

    # =================== origem ===================
    add(0.0, elev_o, "origem", legs[0].from_name if n else lateral.origin_name, True)
    cur = elev_o

    # =================== pré-região [0, pre_hi): subida em degraus ===================
    # A aeronave sobe SEMPRE na razão máxima (start): entra no corredor e alcança o
    # higher_limit num ponto virtual, depois nivela. Em trecho longo alcança o limite
    # já na entrada; em trecho curto, um pouco dentro do corredor (é o máximo físico).
    pre_hi = cs if tem_cruise_stretch else n
    for i in range(0, pre_hi):
        x0, x1 = cum[i], cum[i + 1]
        if i > 0:
            add(x0, cur, "corredor" if legs[i].is_corridor else "ponto", legs[i].from_name, True)
        tgt = his(i)
        if tgt is None:                                  # DIRETO de subida: rumo ao próximo corredor
            tgt = next_corr_his(i + 1, pre_hi)
            if tgt is None:
                tgt = cruise if cruise is not None else cur
        cur = start_to(x0, cur, x1, tgt)

    # =================== trecho de cruzeiro [cs, ce) ===================
    alcancou = False
    cruz_efet = cur
    x_toc = x_tod = None
    if tem_cruise_stretch:
        x_en0, x_en1 = cum[cs], cum[ce]
        if x_en0 > 1e-6:                                  # evita duplicar a origem
            add(x_en0, cur, "ponto", legs[cs].from_name, True)   # início do trecho de cruzeiro
        H_pre = cur
        L_en = x_en1 - x_en0
        alvo = cruise if cruise is not None else max(H_pre, H_post)
        # distâncias de subir a 'alvo' e descer de 'alvo' a H_post
        d_up = (_trans_dist(alvo - H_pre, ac.rate_ac_fpm, ac.speed_ac_kt) if alvo > H_pre
                else _trans_dist(H_pre - alvo, ac.rate_dc_fpm, ac.speed_dc_kt))
        d_dn = (_trans_dist(alvo - H_post, ac.rate_dc_fpm, ac.speed_dc_kt) if alvo > H_post
                else _trans_dist(H_post - alvo, ac.rate_ac_fpm, ac.speed_ac_kt))
        if cruise is not None and L_en >= d_up + d_dn - 1e-9:
            # cabe cruzeiro nivelado
            x_toc = x_en0 + d_up
            x_tod = x_en1 - d_dn
            add(x_toc, alvo, "toc", "TOC", False)
            if x_tod > x_toc + 1e-6:
                add(x_tod, alvo, "tod", "TOD", False)
            else:
                x_tod = x_toc
            cur = H_post
            alcancou = True
            cruz_efet = alvo
        else:
            # não cabe: pico de encontro dentro de [x_en0, x_en1]
            a = ac.rate_ac_fpm * 60.0 / ac.speed_ac_kt      # ft por NM subindo
            b = ac.rate_dc_fpm * 60.0 / ac.speed_dc_kt      # ft por NM descendo
            xm = (H_post + b * L_en - H_pre) / (a + b) if (a + b) > 0 else L_en / 2
            xm = max(0.0, min(L_en, xm))
            peak = H_pre + a * xm
            if cruise is not None:
                peak = min(peak, cruise)
            x_toc = x_tod = x_en0 + xm
            add(x_toc, peak, "virtual", None, False)    # só um vértice geométrico (apex);
            cur = H_post                                # o TOC/TOD real será o platô mais alto
            alcancou = False
            cruz_efet = peak
        add(x_en1, cur, "ponto", legs[ce - 1].to_name, True)   # fim do trecho (entrada 1º corredor chegada / ou destino)

    # =================== pós-região [ce, n): descida em degraus ===================
    if tem_cruise_stretch:
        # último corredor real do bloco de chegada (para separar descida final)
        arr_last = -1
        for i in range(ce, n):
            if legs[i].is_corridor:
                arr_last = i
        for i in range(ce, n):
            x0, x1 = cum[i], cum[i + 1]
            if i > ce:
                add(x0, cur, "corredor" if legs[i].is_corridor else "ponto", legs[i].from_name, True)
            if legs[i].is_corridor:
                tgt = his(i) if his(i) is not None else cur
                if i == ce:
                    cur = tgt                 # 1ª chegada: já entrou no his (cross)
                else:
                    cur = start_to(x0, cur, x1, tgt)   # degraus subsequentes (start)
            else:
                # DIRETO de chegada -> descida final ao destino (cross no destino)
                if i > arr_last >= 0 or arr_last < 0:
                    d_final = _trans_dist(cur - elev_d, ac.rate_dc_fpm, ac.speed_dc_kt) if cur > elev_d else 0.0
                    x_desc = max(x0, total - d_final)
                    if x_desc > x0 + 1e-6:
                        add(x_desc, cur, "virtual")
                    cur = elev_d
                else:
                    cur = start_to(x0, cur, x1, next_corr_his(i + 1, n) or cur)

    # =================== destino ===================
    add(total, elev_d, "destino", legs[-1].to_name if n else lateral.dest_name, True)

    # ordena por x (os virtuais podem sair fora de ordem em bordas)
    vertices.sort(key=lambda v: v.x_nm)

    # TOC/TOD: quando NÃO há cruzeiro nivelado (voo só de corredores, dominado por
    # corredores, ou rota curta), são o INTERVALO da altitude máxima do perfil
    # (1º ao último ponto no topo) — assim o "cruzeiro" pega o platô mais alto.
    if not alcancou:
        max_alt = max(v.alt_ft for v in vertices)
        tops = [v.x_nm for v in vertices if v.alt_ft >= max_alt - 1.0]
        x_toc, x_tod = min(tops), max(tops)
        cruz_efet = max_alt
    if x_toc is None:
        x_toc = x_tod = 0.0

    # ---- tempo por segmento: subida/descida pela RAZÃO (alt/razão em min);
    #      trecho nivelado pela velocidade de cruzeiro (dist/vel). Modelo do Cristiano
    #      (e correto mesmo quando a rampa de decolagem é comprimida até o 1º corredor). ----
    def _bucket_time():
        sub = cru = des = 0.0
        sub_d = cru_d = des_d = 0.0
        for A, B in zip(vertices, vertices[1:]):
            d = B.x_nm - A.x_nm
            if d <= 0:
                continue
            dalt = B.alt_ft - A.alt_ft
            if dalt > 1:
                t = dalt / ac.rate_ac_fpm if ac.rate_ac_fpm > 0 else 0.0
            elif dalt < -1:
                t = (-dalt) / ac.rate_dc_fpm if ac.rate_dc_fpm > 0 else 0.0
            else:
                t = d / ac.speed_cruise_kt * 60.0 if ac.speed_cruise_kt > 0 else 0.0
            mid = (A.x_nm + B.x_nm) / 2.0
            if mid <= x_toc + 1e-6:
                sub += t; sub_d += d
            elif mid >= x_tod - 1e-6:
                des += t; des_d += d
            else:
                cru += t; cru_d += d
        return (FaseTempo(sub_d, sub), FaseTempo(cru_d, cru), FaseTempo(des_d, des))
    sub, cru, des = _bucket_time()

    # ---- aviso de terreno (não altera altitude; só alerta) ----
    for A, B in zip(vertices, vertices[1:]):
        if B.x_nm - A.x_nm <= 0:
            continue
        # amostra o terreno sob o segmento e compara com a menor altitude do trecho
        pa = _interp_pos(lateral, legs, cum, A.x_nm)
        pb = _interp_pos(lateral, legs, cum, B.x_nm)
        try:
            tmax = terreno.max_along([pa, pb], step_nm=step_nm, radius_px=radius_px)
            if min(A.alt_ft, B.alt_ft) < tmax + margem_ft - 1:
                avisos.append(f"perfil pode furar terreno entre {A.x_nm:.0f} e {B.x_nm:.0f} NM "
                              f"({tmax:.0f}+{margem_ft:.0f} > {min(A.alt_ft, B.alt_ft):.0f} ft)")
        except Exception:
            pass

    # ---- silhueta do terreno (para o gráfico) ----
    terreno_perfil = _terrain_silhouette(lateral, legs, cum, total, terreno, step_nm, avisos)

    return PerfilVertical(
        aeronave=ac.label,
        cruzeiro_ft=float(cruz_efet), alcancou_cruzeiro=alcancou,
        origem_elev_ft=elev_o, destino_elev_ft=elev_d,
        vertices=vertices, toc_nm=float(x_toc), tod_nm=float(x_tod),
        subida=sub, cruzeiro=cru, descida=des,
        terreno_perfil=terreno_perfil, avisos=avisos,
        diag={"fonte_cruzeiro": fonte_cruz, "cruise_stretch": (cs, ce),
              "H_pre": H_pre, "H_post": H_post, "route_dir": route_dir,
              "n_vertices": len(vertices), "margem_ft": margem_ft,
              "corredores_x": [(cum[i], cum[i + 1]) for i in range(n) if legs[i].is_corridor]},
    )


# ------------------------------------------------------------------ posição ao longo
def _interp_pos(lateral, legs, cum, x):
    """(lon,lat) na distância acumulada x ao longo da rota lateral."""
    if not legs:
        return (lateral.origin_pos.lon, lateral.origin_pos.lat)
    for i, l in enumerate(legs):
        if x <= cum[i + 1] or i == len(legs) - 1:
            seg = cum[i + 1] - cum[i]
            f = 0.0 if seg <= 0 else (x - cum[i]) / seg
            f = max(0.0, min(1.0, f))
            return (l.from_pos.lon + (l.to_pos.lon - l.from_pos.lon) * f,
                    l.from_pos.lat + (l.to_pos.lat - l.from_pos.lat) * f)
    return (legs[-1].to_pos.lon, legs[-1].to_pos.lat)


def _terrain_silhouette(lateral, legs, cum, total, terreno, step_nm, avisos):
    perfil = []
    passo = max(step_nm, total / 250.0) if total > 0 else step_nm
    cumd = 0.0
    try:
        for l in legs:
            d = l.distance_nm
            k = max(1, int(round(d / passo)))
            for s in range(k):
                f = s / k
                lon = l.from_pos.lon + (l.to_pos.lon - l.from_pos.lon) * f
                lat = l.from_pos.lat + (l.to_pos.lat - l.from_pos.lat) * f
                perfil.append((cumd + d * f, float(terreno.elevation(lon, lat, radius_px=0))))
            cumd += d
        perfil.append((cumd, float(terreno.elevation(lateral.dest_pos.lon,
                                                      lateral.dest_pos.lat, radius_px=0))))
    except Exception as e:
        avisos.append(f"terreno indisponível para a silhueta ({e})")
        return []
    return perfil