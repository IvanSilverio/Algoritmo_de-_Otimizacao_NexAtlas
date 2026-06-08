"""Modelo do subgrafo de roteamento em memória.

O grafo é DIRECIONADO (special_routes_connections define source_id -> target_id
com proa obrigatória). Além das arestas reais dos corredores, o grafo recebe
arestas sintéticas:

  - origem -> waypoints próximos      (entrada nos corredores)
  - waypoints próximos -> destino     (saída dos corredores)
  - origem -> destino                 (rota direta; garante solução factível)

Cada nó recebe um índice inteiro denso [0..N-1] usado como dimensão do vetor
de prioridades do lobo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .geo import LonLat, haversine_m


@dataclass(frozen=True)
class Node:
    id: str                 # id do banco (waypoint) ou código do aeródromo
    name: str
    pos: LonLat
    kind: str               # 'aerodrome' | 'waypoint'
    chart: Optional[str] = None


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    weight_m: float
    corridor: Optional[str] = None      # name em special_routes_connections
    connection_id: Optional[str] = None
    is_mandatory: bool = False
    lower_limit: Optional[int] = None
    higher_limit: Optional[int] = None
    synthetic: bool = False             # True para arestas criadas em runtime


class RouteGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.adj: dict[str, list[Edge]] = {}        # sucessores (digrafo)
        self.index: dict[str, int] = {}             # node_id -> dimensão do lobo
        self.rev_index: list[str] = []              # dimensão -> node_id

    # ---------------------------------------------------------------- build
    def add_node(self, node: Node) -> None:
        if node.id in self.nodes:
            return
        self.nodes[node.id] = node
        self.adj[node.id] = []
        self.index[node.id] = len(self.rev_index)
        self.rev_index.append(node.id)

    def add_edge(self, edge: Edge) -> None:
        if edge.source not in self.nodes or edge.target not in self.nodes:
            raise KeyError(
                f"Aresta {edge.source}->{edge.target} referencia nó ausente. "
                "Garanta que os waypoints da carta foram carregados antes."
            )
        self.adj[edge.source].append(edge)

    def add_synthetic_edges(
        self,
        origin_id: str,
        dest_id: str,
        max_link_nm: float = 30.0,
    ) -> None:
        """Liga aeródromos à malha de corredores e cria a rota direta.

        max_link_nm controla o raio de 'entrada' nos corredores. 30 NM é um
        chute razoável para a V1; vale expor como parâmetro de tuning.
        """
        origin = self.nodes[origin_id]
        dest = self.nodes[dest_id]
        max_link_m = max_link_nm * 1852.0

        for node in self.nodes.values():
            if node.kind != "waypoint":
                continue
            d_o = haversine_m(origin.pos, node.pos)
            if d_o <= max_link_m:
                self.add_edge(Edge(origin_id, node.id, d_o, synthetic=True))
            d_d = haversine_m(node.pos, dest.pos)
            if d_d <= max_link_m:
                self.add_edge(Edge(node.id, dest_id, d_d, synthetic=True))

        # Rota direta: a V1 exige essa alternativa e ela garante factibilidade.
        self.add_edge(
            Edge(origin_id, dest_id, haversine_m(origin.pos, dest.pos), synthetic=True)
        )

    # ---------------------------------------------------------------- query
    @property
    def n(self) -> int:
        return len(self.rev_index)

    def successors(self, node_id: str) -> list[Edge]:
        return self.adj[node_id]

    def direct_distance_m(self, origin_id: str, dest_id: str) -> float:
        return haversine_m(self.nodes[origin_id].pos, self.nodes[dest_id].pos)

    def corridor_nodes_near(self, anchor_id: str, radius_nm: float) -> set[str]:
        """Waypoints participantes de corredores REAIS num raio do âncora.

        Usado para decidir se existe corredor visual aplicável à saída
        (âncora = origem) ou à chegada (âncora = destino). Regra do escopo
        V1 + orientação do piloto: se existir, a passagem é OBRIGATÓRIA.
        """
        anchor = self.nodes[anchor_id]
        radius_m = radius_nm * 1852.0
        out: set[str] = set()
        for edges in self.adj.values():
            for e in edges:
                if e.synthetic:
                    continue
                for nid in (e.source, e.target):
                    node = self.nodes[nid]
                    if haversine_m(node.pos, anchor.pos) <= radius_m:
                        out.add(nid)
        return out

    def mandatory_arrival_nodes(self, dest_id: str, radius_nm: float = 15.0) -> set[str]:
        """Heurística V1 para is_mandatory na chegada.

        Retorna os nós-alvo de conexões obrigatórias cujo target está a até
        radius_nm do destino. Se o conjunto não for vazio, a rota deve
        passar por pelo menos um deles antes de pousar — caso contrário o
        fitness aplica penalidade. Refinar na V2 com a semântica oficial
        de corredor obrigatório por carta.
        """
        dest = self.nodes[dest_id]
        out: set[str] = set()
        for edges in self.adj.values():
            for e in edges:
                if not e.is_mandatory or e.synthetic:
                    continue
                tgt = self.nodes[e.target]
                if haversine_m(tgt.pos, dest.pos) <= radius_nm * 1852.0:
                    out.add(e.target)
        return out
