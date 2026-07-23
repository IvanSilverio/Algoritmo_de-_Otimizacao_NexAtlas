"""Contrato V1 (lateral) -> V3 (vertical).

Esta é a FRONTEIRA entre as duas camadas. A V3 consome apenas `LateralRoute`
— nunca as entranhas da V1. O único ponto que "olha" a V1 é o adaptador
`lateral_route_from_v1`, que traduz a rota do Dijkstra/`plan_v1_route` para o
contrato. Enquanto a V1 continuar produzindo uma rota (edges + nós), ela pode
ser reescrita à vontade sem tocar na V3.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..geo import LonLat, haversine_m, m_to_nm


@dataclass(frozen=True)
class LateralLeg:
    from_name: str
    to_name: str
    from_pos: LonLat
    to_pos: LonLat
    distance_nm: float
    is_corridor: bool
    corridor: Optional[str]                 # nome do corredor REA ("DIRETO" quando não é corredor)
    lower_limit_ft: Optional[float]         # faixa do corredor (None nas DIRETO)
    higher_limit_ft: Optional[float]
    corridor_heading_mag: Optional[float]   # proa MAGNÉTICA do corredor (só corredores)
    synthetic: bool


@dataclass(frozen=True)
class LateralRoute:
    origin_name: str
    dest_name: str
    origin_pos: LonLat
    dest_pos: LonLat
    legs: tuple[LateralLeg, ...]

    @property
    def total_distance_nm(self) -> float:
        return sum(l.distance_nm for l in self.legs)


def lateral_route_from_v1(graph, route) -> LateralRoute:
    """Adapta a rota da V1 (DecodedRoute: node_ids + edges) para o contrato.

    Distância por perna: corredor real usa `weight_m` (comprimento REAL do
    corredor, com curvas); perna sintética (DIRETO) usa a reta geodésica —
    mesma convenção de v1._real_distance_m, para casar com o relatório lateral.
    """
    legs: list[LateralLeg] = []
    for e in route.edges:
        a = graph.nodes[e.source]
        b = graph.nodes[e.target]
        is_direto = e.synthetic or (not e.corridor) or e.corridor == "DIRETO"
        dist_m = (haversine_m(a.pos, b.pos) if e.synthetic else e.weight_m)
        legs.append(LateralLeg(
            from_name=a.name, to_name=b.name,
            from_pos=a.pos, to_pos=b.pos,
            distance_nm=m_to_nm(dist_m),
            is_corridor=not is_direto,
            corridor=("DIRETO" if is_direto else e.corridor),
            lower_limit_ft=(None if is_direto else _f(e.lower_limit)),
            higher_limit_ft=(None if is_direto else _f(e.higher_limit)),
            corridor_heading_mag=(None if is_direto else _f(e.heading)),
            synthetic=bool(e.synthetic),
        ))
    origin = graph.nodes[route.node_ids[0]]
    dest = graph.nodes[route.node_ids[-1]]
    return LateralRoute(
        origin_name=origin.name, dest_name=dest.name,
        origin_pos=origin.pos, dest_pos=dest.pos,
        legs=tuple(legs),
    )


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
