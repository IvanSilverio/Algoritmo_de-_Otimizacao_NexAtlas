"""Rumo verdadeiro -> magnético (para a regra par/ímpar das pernas DIRETO).

MODELO (explícito): World Magnetic Model (WMM), via `pygeomag`, com os
coeficientes carregados de `wmm/wmm-2025.json` — cópia espelho do modelo usado
pelo produto TS (`@cristianob/geomagnetism`; ver `wmm/README.md`). Declinação D
negativa a Oeste (Brasil ~ -17° a -23°). Convenção ICA 100-12:
rumo_magnético = rumo_verdadeiro - D.

Os corredores REA já trazem proa MAGNÉTICA no banco (Edge.heading); isto é só
para as pernas DIRETO, cujo rumo é geométrico.

Edição WMM em uso — travada explicitamente, sem auto-seleção por data
(`high_resolution=False`, `base_year` não usado). Revisar quando sair o
WMM-2030 (início de 2030); até lá a janela inteira de testes (meados de 2027)
cai dentro da validade do WMM-2025 (2024-11-13 a 2029-11-13).
"""
from __future__ import annotations

import datetime as dt

from ..geo import LonLat, initial_bearing
from .wmm.loader import load_wmm_2025_coefficients_data

WMM_EDITION = "WMM-2025"

try:
    from pygeomag import GeoMag
    _GM = GeoMag(coefficients_data=load_wmm_2025_coefficients_data())
    _HAS_WMM = True
except Exception:            # pragma: no cover
    _GM = None
    _HAS_WMM = False

def _decimal_year(date: dt.date | None = None) -> float:
    d = date or dt.date.today()
    return d.year + (d.timetuple().tm_yday - 1) / 365.0


def declination(lat: float, lon: float, date: dt.date | None = None) -> float:
    """Declinação magnética (graus, Oeste negativo) no ponto. Fonte: WMM.

    SEM fallback: se o pygeomag não estiver instalado, levanta erro. Chutar a
    declinação produziria paridade ímpar/par — e portanto nível de cruzeiro —
    errada; é mais seguro falhar alto do que sugerir uma altitude incorreta.
    """
    if not _HAS_WMM:
        raise RuntimeError(
            "WMM indisponível (pygeomag não instalado): não é possível calcular a "
            "proa magnética. Instale com `pip install pygeomag`.")
    return _GM.calculate(lat, lon, 0, _decimal_year(date)).d


def magnetic_bearing(a: LonLat, b: LonLat, date: dt.date | None = None) -> tuple[float, float, float]:
    """Proa MAGNÉTICA da perna a->b, pela declinação no ponto médio.
    Retorna (magnetico, verdadeiro, declinacao)."""
    tv = initial_bearing(a, b)
    latm, lonm = (a.lat + b.lat) / 2.0, (a.lon + b.lon) / 2.0
    d = declination(latm, lonm, date)
    return (tv - d) % 360.0, tv, d


def has_wmm() -> bool:
    return _HAS_WMM