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

import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Optional

from ..geo import initial_bearing
from .aircraft import Aeronave
from .contract import LateralRoute, LateralLeg
from .magnetic import magnetic_bearing, declination
from .cruise import suggest_cruise_altitude
from .wind import Wind, ground_speed
from . import rules


# --------------------------------------------------------------------------- tempo
@dataclass
class FaseTempo:
    dist_nm: float
    tempo_min: float


# --------------------------------------------------------------------------- vento
@dataclass
class SegmentoVento:
    """Um trecho vértice-a-vértice recalculado com o triângulo do vento
    (TAREFA_vento.md §3) — só para diagnóstico/terminal; não afeta a geometria."""
    x0_nm: float
    x1_nm: float
    fase: str                # subida | cruzeiro | descida
    alt_ft: float             # altitude média do trecho (ponto de amostra do vento)
    rumo_deg: float           # rumo VERDADEIRO da perna
    tas_kt: float
    u_kt: float
    v_kt: float
    comp_cauda_kt: float      # + cauda, - proa
    deriva_deg: float
    gs_kt: float
    tempo_min: float           # tempo do trecho COM vento
    tempo_min_sem_vento: float  # tempo do trecho SEM vento (mesma fórmula, u=v=0)


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
    # Combustível por fase (na unidade nativa do banco — ver aircraft.py); None
    # se a aeronave não tiver dados completos de combustível (nunca quebra o perfil).
    comb_subida: Optional[float] = None
    comb_cruzeiro: Optional[float] = None
    comb_descida: Optional[float] = None
    comb_total: Optional[float] = None
    comb_unit: Optional[str] = None       # unidade de QUANTIDADE (ex.: "l", "us gal", "lb")
    fuel_type: Optional[str] = None       # avgas / jet-a (informativo)
    # Vento (TAREFA_vento.md, passo 1) — tempo/combustível recalculados por
    # trecho com o triângulo do vento; None se `wind` não foi passado a
    # plan_vertical_profile (chamador optou por não calcular). Os campos SEM
    # vento acima nunca mudam — ficam para comparação.
    hora_partida_utc: Optional[float] = None
    segmentos_vento: list = field(default_factory=list)   # list[SegmentoVento]
    subida_vento: Optional[FaseTempo] = None
    cruzeiro_vento: Optional[FaseTempo] = None
    descida_vento: Optional[FaseTempo] = None
    comb_subida_vento: Optional[float] = None
    comb_cruzeiro_vento: Optional[float] = None
    comb_descida_vento: Optional[float] = None
    comb_total_vento: Optional[float] = None

    @property
    def tempo_total_min(self) -> float:
        return self.subida.tempo_min + self.cruzeiro.tempo_min + self.descida.tempo_min

    @property
    def tempo_total_vento_min(self) -> Optional[float]:
        if self.subida_vento is None:
            return None
        return (self.subida_vento.tempo_min + self.cruzeiro_vento.tempo_min
                + self.descida_vento.tempo_min)


# --------------------------------------------------------------- helpers de geometria
def _leg_lonlat(leg: LateralLeg):
    return [(leg.from_pos.lon, leg.from_pos.lat), (leg.to_pos.lon, leg.to_pos.lat)]


def _trans_dist(dalt_ft: float, rate_fpm: float, speed_kt: float) -> float:
    """Distância horizontal (NM) para variar |dalt| ft na razão/velocidade dadas."""
    if rate_fpm <= 0 or speed_kt <= 0:
        return 0.0
    return abs(dalt_ft) / rate_fpm / 60.0 * speed_kt


def _qty_unit(rate_unit: Optional[str]) -> Optional[str]:
    """Unidade de QUANTIDADE a partir da unidade de vazão do banco (ex.: 'l/h' ->
    'l', 'us gal/h' -> 'us gal'). Consumo (vazão) x tempo = quantidade; sem a
    troca de forma, mostrar "42.3 l/h" no total seria dimensionalmente errado."""
    u = (rate_unit or "").strip()
    if u.lower().endswith("/h"):
        u = u[:-2].strip()
    return u or None


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


def _niveis_legais(route_dir: float, teto: float) -> list[float]:
    """Níveis de cruzeiro legais (regra semicircular, mesma do documento) até o teto.
    VFR (<14500): milhar ímpar/par + 500 conforme direção; IFR/RVSM: milhar cheio."""
    east = 0 <= route_dir < 180
    out: list[float] = []
    milhar = 1000
    while milhar + 500 < 14500:                     # faixa VFR: milhar ± 500
        if ((milhar // 1000) % 2 == 1) == east and (milhar + 500) <= teto + 1e-6:
            out.append(float(milhar + 500))
        milhar += 1000
    m = 15000 if east else 16000                    # faixa IFR/RVSM: milhar cheio
    while m <= min(int(teto), 41000) + 1e-6:
        out.append(float(m))
        m += 2000
    return sorted(out)


def _nivel_legal_acima(piso_ft: float, route_dir: float, teto: float) -> float:
    """Menor nível legal >= piso; se nenhum couber sob o teto, o mais alto legal."""
    niveis = _niveis_legais(route_dir, teto)
    acima = [lv for lv in niveis if lv >= piso_ft - 1e-6]
    if acima:
        return acima[0]
    if niveis:
        return niveis[-1]
    return float(int((piso_ft + 499) // 500) * 500)   # teto muito baixo: fallback numérico


# --------------------------------------------------------------------------- API
def plan_vertical_profile(lateral: LateralRoute, aeronave: Aeronave, terreno,
                          wind: Optional[Wind] = None, *,
                          margem_ft: float = rules.CLEARANCE_FT,
                          step_nm: float = rules.STEP_NM,
                          radius_px: int = rules.RADIUS_PX,
                          hora_partida_utc: Optional[float] = None) -> PerfilVertical:
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

    # ---- PISOS do cruzeiro (só quando há trecho de cruzeiro) ----
    # (1) piso de CORREDOR: o cruzeiro não pode ficar ABAIXO do corredor que ele
    #     conecta (senão a aeronave desceria abaixo do corredor e subiria para
    #     entrar nele). Sobe para o NÍVEL do corredor (altitude de carta).
    # (c) piso de TERRENO en-route (+500, como o documento estende "o ponto mais
    #     alto"): se ainda ficar abaixo da serra, sobe para o menor NÍVEL LEGAL.
    if tem_cruise_stretch and cruise is not None:
        hpre_corr = next((his(j) for j in range(cs - 1, -1, -1) if his(j) is not None), None)
        hpost_corr = next_corr_his(ce, n)
        corr_floor = max([x for x in (hpre_corr, hpost_corr) if x is not None], default=None)
        if corr_floor is not None and cruise < corr_floor - 1e-6:
            avisos.append(f"cruzeiro elevado de {cruise:.0f} para {corr_floor:.0f} ft "
                          f"(nível do corredor conectado).")
            fonte_cruz += f" | piso corredor → {corr_floor:.0f}"
            cruise = corr_floor
        en_path = [(legs[cs].from_pos.lon, legs[cs].from_pos.lat)]
        for i in range(cs, ce):
            en_path.append((legs[i].to_pos.lon, legs[i].to_pos.lat))
        try:
            terr_max = float(terreno.max_along(en_path, step_nm=step_nm, radius_px=radius_px))
            piso = terr_max + 500.0
            if cruise < piso - 1e-6:
                novo = _nivel_legal_acima(piso, route_dir, ac.teto_ft)
                if novo < piso - 1e-6:
                    avisos.append(f"ATENÇÃO: terreno en-route {terr_max:.0f} ft exige piso "
                                  f"{piso:.0f} ft, acima do teto ({ac.teto_ft:.0f}); "
                                  f"cruzeiro no máximo legal {novo:.0f} ft — terreno NÃO liberado.")
                else:
                    avisos.append(f"cruzeiro elevado de {cruise:.0f} para {novo:.0f} ft "
                                  f"(piso de terreno {terr_max:.0f}+500={piso:.0f} ft).")
                fonte_cruz += f" | piso terreno {piso:.0f} → {novo:.0f}"
                cruise = novo
        except Exception as e:
            avisos.append(f"não foi possível checar o piso de terreno do cruzeiro ({e}).")

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

    # Descida FINAL, sempre na razão do banco, governada pela LINHA DE APROXIMAÇÃO
    # que vem do destino: alt(x) = min(teto_corredor(x), alt_aprox(x)). A aeronave
    # entra aqui já na altitude que mantém (cruzeiro ou pico) e desce quando a linha
    # de aproximação a alcança — o "TOD de aproximação" (relativo ao aeródromo) — indo
    # reta até a pista, atravessando os corredores de chegada por baixo dos tetos
    # (higher_limit é máximo, não piso). Só fica nivelada num teto se esse teto estiver
    # ABAIXO da linha de aproximação naquele ponto.
    def _descida_final(x_start, alt_start):
        slope = (ac.rate_dc_fpm * 60.0 / ac.speed_dc_kt
                 if (ac.rate_dc_fpm > 0 and ac.speed_dc_kt > 0) else 0.0)
        # ALVOS que a aeronave deve atingir DESCENDO na razão do banco: cada corredor
        # de chegada no seu higher_limit (na ENTRADA do corredor) e o destino. A
        # aeronave mantém a altitude até o início de descida MAIS URGENTE (o "TOD" de
        # cada alvo, relativo a ele) e desce reto na razão do banco até atingi-lo;
        # repete. Isso faz "cross" no corredor quando ele está longe (a linha ainda
        # está acima do teto), e atravessa o corredor por baixo do teto quando ele
        # está perto (a linha do destino já está abaixo do teto).
        alvos = sorted(set(
            [(cum[i], float(his(i))) for i in range(n)
             if legs[i].is_corridor and his(i) is not None and cum[i] > x_start + 1e-9]
            + [(float(total), float(elev_d))]))
        poly = [(x_start, alt_start)]
        cur = alt_start
        x = x_start
        rem = list(alvos)
        guard = 0
        while slope > 0 and guard < 4 * n + 8:
            guard += 1
            pend = [(xt, at) for xt, at in rem if xt > x + 1e-9 and at < cur - 1e-6]
            if not pend:
                break
            xs, xt, at = min((xt - (cur - at) / slope, xt, at) for xt, at in pend)
            xs = max(xs, x)
            if xs > x + 1e-6:
                poly.append((xs, cur))       # fim do nível mantido (início da descida)
            poly.append((xt, at))            # chega no alvo (na razão do banco)
            cur = at
            x = xt
            rem = [(a, b) for a, b in rem if a > x + 1e-9]
        if poly[-1][0] < total - 1e-9:
            poly.append((total, cur))        # nivelado até o destino (se sobrou)

        def alt_at(xq):
            for (xa, aa), (xb, ab) in zip(poly, poly[1:]):
                if xa - 1e-9 <= xq <= xb + 1e-9:
                    return aa if xb - xa < 1e-9 else aa + (ab - aa) * (xq - xa) / (xb - xa)
            return poly[-1][1]

        # vértices: breakpoints da descida (virtuais) + ENTRADAS de corredor (reais),
        # cada um na altitude interpolada da poligonal.
        pts = {}
        for xa, _ in poly[1:-1]:
            pts[round(xa, 3)] = (xa, False, "virtual", None)
        for i in range(n):
            xe = cum[i]
            if x_start + 1e-9 < xe < total - 1e-9:
                pts[round(xe, 3)] = (xe, True,
                                     "corredor" if legs[i].is_corridor else "ponto",
                                     legs[i].from_name)
        for key in sorted(pts):
            xq, real, tipo, nome = pts[key]
            add(xq, alt_at(xq), tipo, nome, real)
        return poly[-1][1]

    # =================== origem ===================
    add(0.0, elev_o, "origem", legs[0].from_name if n else lateral.origin_name, True)
    cur = elev_o

    # =================== pré-região [0, pre_hi): subida em degraus ===================
    # A aeronave sobe SEMPRE na razão máxima (start): entra no corredor e alcança o
    # higher_limit num ponto virtual, depois nivela. Em trecho longo alcança o limite
    # já na entrada; em trecho curto, um pouco dentro do corredor (é o máximo físico).
    # sem trecho de cruzeiro: sobe só até o corredor de PICO (1º a alcançar o
    # maior teto do trajeto); dali em diante a descida governa (mesma linha de
    # aproximação da chegada, aplicada abaixo após a pré-região).
    if tem_cruise_stretch:
        pre_hi = cs
    else:
        _tetos = [(i, his(i)) for i in range(n) if his(i) is not None]
        _global_max = max(h for _, h in _tetos) if _tetos else None
        peak_i = (next(i for i, h in _tetos if h >= _global_max - 1e-6)
                  if _global_max is not None else -1)
        pre_hi = (peak_i + 1) if peak_i >= 0 else n
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
        slope_dc = (ac.rate_dc_fpm * 60.0 / ac.speed_dc_kt
                    if (ac.rate_dc_fpm > 0 and ac.speed_dc_kt > 0) else 0.0)
        d_up = (_trans_dist(alvo - H_pre, ac.rate_ac_fpm, ac.speed_ac_kt) if alvo > H_pre
                else _trans_dist(H_pre - alvo, ac.rate_dc_fpm, ac.speed_dc_kt))
        x_toc = x_en0 + d_up                              # onde a aeronave alcança 'alvo'
        # TOD relativo ao AERÓDROMO: onde a linha de aproximação (do destino, na razão
        # do banco) cruza a altitude de cruzeiro. É daqui que a descida começa.
        x_tod_aero = (total - (alvo - elev_d) / slope_dc) if slope_dc > 0 else total
        cabe = (cruise is not None and x_toc <= x_en1 + 1e-9 and x_toc <= x_tod_aero + 1e-9)
        if cabe:
            # cabe cruzeiro nivelado; a descida (a partir do TOD de aproximação) é
            # delegada à linha de aproximação, que governa até a pista.
            if x_toc > x_en0 + 1e-6:
                add(x_toc, alvo, "toc", "TOC", False)
            alcancou = True
            cruz_efet = alvo
            cur = _descida_final(x_toc, alvo)
        else:
            # não cabe cruzeiro: pico onde a subida (de H_pre) encontra a linha de
            # aproximação (do destino); dali a descida governa até a pista.
            a = ac.rate_ac_fpm * 60.0 / ac.speed_ac_kt
            b = slope_dc
            if a + b > 0:
                x_peak = (elev_d + b * total + a * x_en0 - H_pre) / (a + b)
            else:
                x_peak = x_en0 + L_en / 2.0
            x_peak = max(x_en0, min(total, x_peak))
            peak = H_pre + a * (x_peak - x_en0)
            if cruise is not None:
                peak = min(peak, alvo)
            if x_peak > x_en0 + 1e-6:
                add(x_peak, peak, "virtual", None, False)
            alcancou = False
            cruz_efet = peak
            cur = _descida_final(x_peak, peak)
    elif pre_hi < n:
        add(cum[pre_hi], cur, "corredor" if legs[pre_hi].is_corridor else "ponto",
            legs[pre_hi].from_name, True)
        cur = _descida_final(cum[pre_hi], cur)

    # =================== destino ===================
    add(total, elev_d, "destino", legs[-1].to_name if n else lateral.dest_name, True)

    # ordena por x (os virtuais podem sair fora de ordem em bordas)
    vertices.sort(key=lambda v: v.x_nm)

    # TOC/TOD calculados AO FINAL, sobre a rota inteira: intervalo da altitude
    # máxima do perfil. TOC = 1º ponto que atinge o topo (fim da ÚLTIMA subida);
    # TOD = último ponto no topo (início da descida). Vale para todos os casos
    # (com ou sem cruzeiro nivelado); o "topo" pode ser o cruzeiro ou o corredor
    # mais alto. Assim, se a aeronave já nivelou num corredor na altitude de
    # cruzeiro, o TOC começa ali (e não depois).
    max_alt = max(v.alt_ft for v in vertices)
    tops = [v.x_nm for v in vertices if v.alt_ft >= max_alt - 1.0]
    x_toc, x_tod = min(tops), max(tops)
    cruz_efet = max_alt

    # ---- tempo por segmento, bucketizado pela NATUREZA do trecho (não pela
    #      posição): SUBIDA = só quando realmente sobe; DESCIDA = só quando desce;
    #      CRUZEIRO = trecho NIVELADO (cruzeiro + corredores nivelados). Subida e
    #      descida contam pela razão (alt/razão); nivelado pela velocidade. ----
    def _bucket_time():
        sub = cru = des = 0.0
        sub_d = cru_d = des_d = 0.0
        for A, B in zip(vertices, vertices[1:]):
            d = B.x_nm - A.x_nm
            if d <= 0:
                continue
            dalt = B.alt_ft - A.alt_ft
            if dalt > 1:                                   # subindo de fato
                sub += dalt / ac.rate_ac_fpm if ac.rate_ac_fpm > 0 else 0.0
                sub_d += d
            elif dalt < -1:                                # descendo de fato
                des += (-dalt) / ac.rate_dc_fpm if ac.rate_dc_fpm > 0 else 0.0
                des_d += d
            else:                                          # nivelado (cruzeiro/corredor)
                cru += d / ac.speed_cruise_kt * 60.0 if ac.speed_cruise_kt > 0 else 0.0
                cru_d += d
        return (FaseTempo(sub_d, sub), FaseTempo(cru_d, cru), FaseTempo(des_d, des))
    sub, cru, des = _bucket_time()

    # ---- combustível por fase: consumo (na unidade nativa do banco) x tempo
    #      da fase (reaproveita os tempos já bucketizados acima). Tudo-ou-nada:
    #      só calcula se as 3 fases + a unidade estiverem disponíveis, senão
    #      fica indisponível (None) — nunca quebra o cálculo do perfil. ----
    if ac.fuel_ac is not None and ac.fuel_cruise is not None and ac.fuel_dc is not None and ac.fuel_unit:
        comb_subida = ac.fuel_ac * (sub.tempo_min / 60.0)
        comb_cruzeiro = ac.fuel_cruise * (cru.tempo_min / 60.0)
        comb_descida = ac.fuel_dc * (des.tempo_min / 60.0)
        comb_total = comb_subida + comb_cruzeiro + comb_descida
        comb_unit = _qty_unit(ac.fuel_unit)
    else:
        comb_subida = comb_cruzeiro = comb_descida = comb_total = comb_unit = None

    # ---- aviso de terreno (só alerta; não altera altitude) ----
    # Evita falsos positivos: só avisa em trecho NIVELADO e FORA de corredor.
    # A subida de decolagem e a descida final ficam perto do relevo por natureza
    # (é normal e legal), e os corredores têm folga própria de carta — por isso
    # ambos são ignorados aqui.
    corr_ranges = [(cum[i], cum[i + 1]) for i in range(n) if legs[i].is_corridor]

    def _in_corr(x):
        return any(a - 1e-9 <= x <= b + 1e-9 for a, b in corr_ranges)

    for A, B in zip(vertices, vertices[1:]):
        if B.x_nm - A.x_nm <= 0 or abs(B.alt_ft - A.alt_ft) > 1:   # pula subida/descida
            continue
        mid = (A.x_nm + B.x_nm) / 2.0
        if _in_corr(mid):                                           # pula corredor
            continue
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

    # ---- vento: tempo/combustível recalculados por trecho (TAREFA_vento.md,
    #      passo 1). NÃO toca na geometria acima (vértices já estão prontos);
    #      wind=None -> campos ficam None (chamador optou por não calcular). ----
    hora_vento = hora_partida_utc
    subida_vento = cruzeiro_vento = descida_vento = None
    comb_subida_vento = comb_cruzeiro_vento = comb_descida_vento = comb_total_vento = None
    segmentos_vento: list = []
    if wind is not None:
        if hora_vento is None:
            hora_vento = time.time()
            quando = dt.datetime.fromtimestamp(hora_vento, dt.timezone.utc)
            avisos.append("hora de partida não informada; usando agora para o "
                          f"vento ({quando:%Y-%m-%d %H:%M} UTC).")
        subida_vento, cruzeiro_vento, descida_vento, segmentos_vento = _vento_por_segmento(
            vertices, legs, cum, ac, wind, hora_vento, avisos)
        if ac.fuel_ac is not None and ac.fuel_cruise is not None and ac.fuel_dc is not None and ac.fuel_unit:
            comb_subida_vento = ac.fuel_ac * (subida_vento.tempo_min / 60.0)
            comb_cruzeiro_vento = ac.fuel_cruise * (cruzeiro_vento.tempo_min / 60.0)
            comb_descida_vento = ac.fuel_dc * (descida_vento.tempo_min / 60.0)
            comb_total_vento = comb_subida_vento + comb_cruzeiro_vento + comb_descida_vento

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
        comb_subida=comb_subida, comb_cruzeiro=comb_cruzeiro, comb_descida=comb_descida,
        comb_total=comb_total, comb_unit=comb_unit, fuel_type=ac.fuel_type,
        hora_partida_utc=hora_vento, segmentos_vento=segmentos_vento,
        subida_vento=subida_vento, cruzeiro_vento=cruzeiro_vento, descida_vento=descida_vento,
        comb_subida_vento=comb_subida_vento, comb_cruzeiro_vento=comb_cruzeiro_vento,
        comb_descida_vento=comb_descida_vento, comb_total_vento=comb_total_vento,
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


def _locate(legs, cum, x):
    """(índice da perna, (lon,lat) interpolado) na distância x — mesma busca
    de `_interp_pos`, mas também devolve a perna (p/ o rumo verdadeiro do vento)."""
    if not legs:
        return 0, (0.0, 0.0)
    for i, l in enumerate(legs):
        if x <= cum[i + 1] or i == len(legs) - 1:
            seg = cum[i + 1] - cum[i]
            f = 0.0 if seg <= 0 else (x - cum[i]) / seg
            f = max(0.0, min(1.0, f))
            return i, (l.from_pos.lon + (l.to_pos.lon - l.from_pos.lon) * f,
                       l.from_pos.lat + (l.to_pos.lat - l.from_pos.lat) * f)
    return len(legs) - 1, (legs[-1].to_pos.lon, legs[-1].to_pos.lat)


def _leg_true_heading(leg: LateralLeg) -> float:
    """Rumo VERDADEIRO da perna, para o triângulo do vento (u/v são leste/norte
    verdadeiros). Corredor: já vem do banco, mas em proa MAGNÉTICA
    (`corridor_heading_mag`, ver contract.py) — reconverte pra verdadeira somando
    a declinação WMM no meio da perna (`verdadeiro = magnético + D`, mesma
    convenção do magnetic.py). DIRETO: sem rumo de carta (é reta geodésica) —
    calcula da geometria com `initial_bearing`."""
    if leg.is_corridor and leg.corridor_heading_mag is not None:
        latm = (leg.from_pos.lat + leg.to_pos.lat) / 2.0
        lonm = (leg.from_pos.lon + leg.to_pos.lon) / 2.0
        d = declination(latm, lonm)
        return (leg.corridor_heading_mag + d) % 360.0
    return initial_bearing(leg.from_pos, leg.to_pos)


def _vento_por_segmento(vertices, legs, cum, ac: Aeronave, wind: Wind,
                        hora_partida_utc: float, avisos: list):
    """Percorre os mesmos trechos vértice-a-vértice de `_bucket_time`, recalculando
    o tempo com o triângulo do vento (TAREFA_vento.md §3). NÃO toca em x/altitude
    dos vértices — só tempo/combustível.

    ETA de cada trecho = hora_partida_utc + tempo ACUMULADO SEM VENTO até o
    vértice inicial (evita a circularidade vento->tempo->ETA->vento). `dist/tas`
    sem vento é matematicamente igual a Δaltitude/razão (a distância de cada
    trecho de subida/descida foi construída exatamente por essa relação em
    `start_to`/`_descida_final`), então o acumulado aqui bate com subida.tempo_min
    etc. — por isso não precisa reaproveitar `_bucket_time` para o sem-vento.
    """
    hdg_cache: dict[int, float] = {}

    def heading_da_perna(i):
        if i not in hdg_cache:
            hdg_cache[i] = _leg_true_heading(legs[i])
        return hdg_cache[i]

    sub = cru = des = 0.0
    sub_d = cru_d = des_d = 0.0
    t_acum_sem_vento_min = 0.0
    segmentos: list[SegmentoVento] = []
    falhas_antes = wind.falhas
    clamps_gs = 0

    for A, B in zip(vertices, vertices[1:]):
        d = B.x_nm - A.x_nm
        if d <= 0:
            continue
        dalt = B.alt_ft - A.alt_ft
        if dalt > 1:
            fase, tas = "subida", ac.speed_ac_kt
        elif dalt < -1:
            fase, tas = "descida", ac.speed_dc_kt
        else:
            fase, tas = "cruzeiro", ac.speed_cruise_kt

        t_sem_vento = (d / tas * 60.0) if tas > 0 else 0.0
        eta = hora_partida_utc + t_acum_sem_vento_min * 60.0

        i, (lon, lat) = _locate(legs, cum, (A.x_nm + B.x_nm) / 2.0)
        rumo = heading_da_perna(i)
        alt_mid = (A.alt_ft + B.alt_ft) / 2.0
        u, v = wind.vento_em(lon, lat, alt_mid, eta)
        gs, comp_cauda, deriva = ground_speed(rumo, tas, u, v)
        gs_eff = gs
        if gs_eff < 0.5:                        # vento de proa > TAS (patológico)
            gs_eff = 0.5
            clamps_gs += 1
        t_com_vento = d / gs_eff * 60.0

        segmentos.append(SegmentoVento(
            x0_nm=A.x_nm, x1_nm=B.x_nm, fase=fase, alt_ft=alt_mid, rumo_deg=rumo,
            tas_kt=tas, u_kt=u, v_kt=v, comp_cauda_kt=comp_cauda, deriva_deg=deriva,
            gs_kt=gs, tempo_min=t_com_vento, tempo_min_sem_vento=t_sem_vento,
        ))

        if fase == "subida":
            sub += t_com_vento; sub_d += d
        elif fase == "descida":
            des += t_com_vento; des_d += d
        else:
            cru += t_com_vento; cru_d += d
        t_acum_sem_vento_min += t_sem_vento

    n_falhas = wind.falhas - falhas_antes
    if n_falhas > 0:
        avisos.append(f"vento indisponível em {n_falhas} de {len(segmentos)} trecho(s) "
                      f"(CDN); usado vento=0 nesses trechos.")
    if clamps_gs > 0:
        avisos.append(f"vento de proa extremo (> TAS) em {clamps_gs} trecho(s); "
                      f"groundspeed limitada a 0.5 kt nesses trechos.")
    return (FaseTempo(sub_d, sub), FaseTempo(cru_d, cru), FaseTempo(des_d, des), segmentos)


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
