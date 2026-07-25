"""Camada V3 — Navegação vertical (perfil sobre a rota lateral da V1).

Separada da V1 por um contrato (contract.LateralRoute). A V1 pode evoluir à
vontade; enquanto emitir uma rota adaptável, a V3 não é afetada.

Uso típico:
    from nexatlas_router.vertical import (
        Terrain, load_from_db, find, plan_from_v1)
    catalogo = load_from_db(conn)             # aeronaves utilizáveis
    ac = find(catalogo, "C172")
    terreno = Terrain()                        # CDN (injetável; use stub em teste)
    perfil = plan_from_v1(graph, route, ac, terreno)
"""
from .aircraft import (Aeronave, build_catalog, load_from_db, load_from_json, find)
from .terrain import Terrain
from .contract import LateralRoute, LateralLeg, lateral_route_from_v1
from .profile import PerfilVertical, Vertice, plan_vertical_profile
from .plot_profile import plot_vertical_profile
from . import rules, magnetic

__all__ = [
    "Aeronave", "build_catalog", "load_from_db", "load_from_json", "find",
    "Terrain", "LateralRoute", "LateralLeg", "lateral_route_from_v1",
    "PerfilVertical", "Vertice", "plan_vertical_profile", "plan_from_v1",
    "plot_vertical_profile", "rules", "magnetic",
]


def plan_from_v1(graph, route, aeronave, terreno, **kw) -> PerfilVertical:
    """Atalho: adapta a rota da V1 para o contrato e gera o perfil vertical.

    `route` é o DecodedRoute retornado pelo Dijkstra (ou result.route). Aceita
    também um V1RouteResult que exponha `.route`, se existir.
    """
    r = getattr(route, "route", route)
    lateral = lateral_route_from_v1(graph, r)
    return plan_vertical_profile(lateral, aeronave, terreno, **kw)