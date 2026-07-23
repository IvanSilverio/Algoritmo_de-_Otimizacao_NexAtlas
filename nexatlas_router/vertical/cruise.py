"""Altitude de cruzeiro — implementação fiel ao documento
"Cálculo da Altitude de Cruzeiro" (especificação NexAtlas, independente de código).

Quatro etapas sequenciais:
  1. Faixa viável (mínima/máxima admissíveis).
  2. Alvo por distância × teto (heurística de referência).
  3. Enquadramento no nível legal (regra semicircular).
  4. Validação final dentro da faixa viável.

Todas as altitudes em PÉS; distância em NM; rumo em graus (proa magnética
origem→destino, 1..360). Retorna a altitude sugerida, ou None se a rota não
comporta cruzeiro definido.
"""
from __future__ import annotations

from typing import Optional


def _band_pct(ceiling: float, dist: float) -> tuple[float, float]:
    """Percentuais (mín, máx) do teto conforme banda do teto e distância."""
    if ceiling <= 15000:
        t = [(50, 25, 35), (100, 40, 50), (200, 55, 65), (400, 65, 70),
             (float("inf"), 80, 100)]
    elif ceiling <= 30000:
        t = [(50, 15, 25), (100, 35, 45), (200, 55, 65), (500, 75, 85),
             (float("inf"), 85, 95)]
    else:
        t = [(50, 10, 20), (100, 25, 35), (200, 45, 55), (500, 65, 75),
             (float("inf"), 80, 90)]
    for lim, lo, hi in t:
        if dist <= lim:
            return lo, hi
    return t[-1][1], t[-1][2]


def suggest_cruise_altitude(
    route_distance: float,          # NM
    route_direction: float,         # graus, 1..360 (proa magnética origem→destino)
    departure_elevation: float,     # ft
    destination_elevation: float,   # ft
    operational_ceiling: float,     # ft
    rate_climb: float, rate_descent: float,     # ft/min
    speed_climb: float, speed_descent: float,   # kt
) -> Optional[float]:
    east = 0 <= route_direction < 180

    # --- Etapa 1: faixa viável ---
    base_min = max(departure_elevation or 0, destination_elevation or 0)
    min_altitude = max(base_min + 500, 500)

    def leg_distance(alt):
        climb = ((alt - departure_elevation) / rate_climb) / 60 * speed_climb
        descent = ((alt - destination_elevation) / rate_descent) / 60 * speed_descent
        return climb + descent

    start = -(-base_min // 500) * 500              # ceil ao múltiplo de 500
    ceil_floor = (operational_ceiling // 500) * 500
    max_altitude = ceil_floor
    alt = start
    while alt <= ceil_floor:
        if leg_distance(alt) > route_distance:
            max_altitude = alt - 500
            break
        alt += 500

    if min_altitude > max_altitude:
        return None                                # rota não comporta cruzeiro

    # --- Etapa 2: alvo por distância × teto ---
    lo, hi = _band_pct(operational_ceiling, route_distance)
    target = operational_ceiling * ((lo + hi) / 2 / 100)

    minimal = max(departure_elevation, destination_elevation) or 500
    target = max(target, minimal)
    if operational_ceiling <= 15000 and target > 10500:
        target = 10500
    base = int(target // 1000) * 1000

    # --- Etapa 3: enquadramento legal (semicircular) ---
    if target < 14500:                             # VFR: milhar ± 500
        if east:
            altitude = base + 500 if base % 2000 != 0 else base - 500
        else:
            altitude = base + 500 if base % 2000 == 0 else base - 500
    elif target <= 41000:                          # IFR / RVSM: milhar cheio
        if east:
            altitude = base if base % 2000 != 0 else base - 1000
        else:
            altitude = base if base % 2000 == 0 else base - 1000
    else:                                          # acima de 41.000: passos de 2.000
        cands = list(range(42000, int(operational_ceiling) + 1, 2000))
        want_odd = east
        pool = [c for i, c in enumerate(cands) if (i % 2 == 0) == want_odd]
        altitude = min(pool, key=lambda c: abs(c - target)) if pool else base

    if operational_ceiling <= 15000 and altitude > 10500:
        altitude = 10500

    # --- Etapa 4: validação contra a faixa viável ---
    if altitude < min_altitude:
        altitude = ((min_altitude + 500) // 500) * 500
    if altitude > max_altitude:
        altitude = max_altitude

    return float(altitude)
