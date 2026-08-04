"""Constantes regulamentares da V3 (RBAC 91 / ICA 100-12).

- Folga de terreno (91.119): 300 m (1000 ft) acima do maior obstáculo num raio
  de 600 m sobre áreas habitadas. Usamos 1000 ft. O raio de 600 m vira a janela
  de amostragem do terreno (radius_px ≈ 4 em z=10, ~150 m/pixel).
- STEP_NM: passo de amostragem do terreno ao longo da perna.

Obs.: o enquadramento semicircular do cruzeiro é feito no cruise.py (spec do
documento "Cálculo da Altitude de Cruzeiro"), não aqui.
"""
from __future__ import annotations

# Defaults regulamentares (ajustáveis por chamada).
CLEARANCE_FT = 1000.0          # folga de terreno (91.119 área habitada)
OBSTACLE_RADIUS_M = 600.0      # raio do obstáculo em torno da aeronave (91.119)
Z_METERS_PER_PIXEL = 150.0     # ~resolução do terreno em z=10
RADIUS_PX = max(1, round(OBSTACLE_RADIUS_M / Z_METERS_PER_PIXEL))   # ≈ 4
STEP_NM = 0.5                  # passo de amostragem ao longo da perna