"""nexatlas_router — motor de rotas V1 (VFR com corredores visuais)."""
from .geo import LonLat, haversine_m, m_to_nm
from .graphmodel import Edge, Node, RouteGraph
from .gwo import DecodedRoute, GWOConfig, GWOResult, GWORouter
from .dijkstra import dijkstra
from .v1 import V1RouteResult, plan_v1_route

__all__ = [
    "LonLat", "haversine_m", "m_to_nm",
    "Edge", "Node", "RouteGraph",
    "DecodedRoute", "GWOConfig", "GWOResult", "GWORouter",
    "dijkstra",
    "V1RouteResult", "plan_v1_route",
]
