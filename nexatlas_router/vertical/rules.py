"""Regras verticais VFR (ICA 100-12 / RBAC 91).

- Regra semicircular (tabela de níveis de cruzeiro VFR), por RUMO MAGNÉTICO:
    rumo 0..179   -> ímpar milhar + 500  (3500, 5500, 7500, 9500, ...)
    rumo 180..359 -> par  milhar + 500  (4500, 6500, 8500, ...)
  Aplica-se acima de 900 m (3000 ft) AGL.
- Folga de terreno (91.119): 300 m (1000 ft) acima do maior obstáculo num raio
  de 600 m sobre áreas habitadas (valor mais restritivo, seguro em geral);
  150 m (500 ft) fora delas. Usamos 1000 ft. O raio de 600 m vira a janela de
  amostragem do terreno (radius_px≈4 em z=10).
"""
from __future__ import annotations

# Defaults regulamentares (ajustáveis por chamada).
CLEARANCE_FT = 1000.0          # folga de terreno (91.119 área habitada)
BASE_AGL_FT = 3000.0           # limiar da regra semicircular (ICA 100-12)
OBSTACLE_RADIUS_M = 600.0      # "raio de 600 m em torno da aeronave" (91.119)
Z_METERS_PER_PIXEL = 150.0     # ~resolução do terreno em z=10
RADIUS_PX = max(1, round(OBSTACLE_RADIUS_M / Z_METERS_PER_PIXEL))   # ≈ 4
STEP_NM = 0.5                  # passo de amostragem ao longo da perna


def vfr_cruise_levels(mag_heading: float, floor_ft: float,
                      ceiling_ft: float) -> list[float]:
    """Níveis VFR válidos (pés) para o rumo magnético, entre piso e teto.

    Só níveis da tabela acima de 3000 ft (>=3500 ímpar / >=4500 par)."""
    odd = (mag_heading % 360.0) < 180.0
    levels: list[float] = []
    t = 1
    while True:
        lvl = t * 1000 + 500
        if lvl > ceiling_ft + 1e-6:
            break
        applicable = (t % 2 == 1) if odd else (t % 2 == 0)
        if applicable and lvl >= floor_ft and lvl >= 3500:
            levels.append(float(lvl))
        t += 1
    return levels