"""Rumo verdadeiro -> magnético (para a regra par/ímpar das pernas DIRETO).

MODELO (explícito): World Magnetic Model (WMM), via `pygeomag` (coeficientes
embutidos). Declinação D negativa a Oeste (Brasil ~ -17° a -23°).
Convenção ICA 100-12: rumo_magnético = rumo_verdadeiro - D.

Os corredores REA já trazem proa MAGNÉTICA no banco (Edge.heading); isto é só
para as pernas DIRETO, cujo rumo é geométrico.
"""
from __future__ import annotations

import datetime as dt

from ..geo import LonLat, initial_bearing

try:
    from pygeomag import GeoMag
    _GM = GeoMag()
    _HAS_WMM = True
except Exception:            # pragma: no cover
    _GM = None
    _HAS_WMM = False

_FALLBACK_DECL = -21.0       # média p/ Brasil, só se pygeomag ausente (não recomendado)


def _decimal_year(date: dt.date | None = None) -> float:
    d = date or dt.date.today()
    return d.year + (d.timetuple().tm_yday - 1) / 365.0


def declination(lat: float, lon: float, date: dt.date | None = None) -> float:
    """Declinação magnética (graus, Oeste negativo) no ponto. Fonte: WMM."""
    if not _HAS_WMM:
        return _FALLBACK_DECL
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
