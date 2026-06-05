"""Utilidades geográficas.

ATENÇÃO (vindo da auditoria do banco): o PostGIS armazena geometria como
[Longitude, Latitude] (X, Y). Todo este pacote padroniza a struct interna
como (lon, lat) para casar com o banco, e a conversão acontece AQUI,
em um único lugar.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_008.8  # raio médio (mesma ordem do ST_DistanceSphere)
M_PER_NM = 1852.0


@dataclass(frozen=True)
class LonLat:
    """Coordenada no padrão do banco: longitude primeiro."""
    lon: float
    lat: float


def haversine_m(a: LonLat, b: LonLat) -> float:
    """Distância esférica em metros entre dois pontos (lon, lat).

    Compatível em ~0,3% com ST_DistanceSphere; usada apenas para as
    arestas sintéticas calculadas em Python. As arestas reais dos
    corredores chegam com peso já calculado pelo PostGIS.
    """
    phi1, phi2 = math.radians(a.lat), math.radians(b.lat)
    dphi = phi2 - phi1
    dlmb = math.radians(b.lon - a.lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def m_to_nm(meters: float) -> float:
    return meters / M_PER_NM
