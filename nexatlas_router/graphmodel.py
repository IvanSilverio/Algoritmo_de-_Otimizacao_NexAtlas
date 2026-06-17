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

    def _gateway_waypoints(self) -> set[str]:
        """IDs de waypoints que são PORTÕES conectados à malha.

        Correção: Removemos a dependência da string 'PORTÃO'. Qualquer nó 
        que participe de uma aresta real de corredor é agora considerado um 
        'Gateway Virtual' em potencial. A restrição de uso será feita pela 
        distância geográfica na hora de criar as pontes.
        """
        connected: set[str] = set()
        for src, edges in self.adj.items():
            for e in edges:
                if e.synthetic:
                    continue
                connected.add(e.source)
                connected.add(e.target)

        gateways: set[str] = set()
        for nid, node in self.nodes.items():
            if node.kind != "waypoint":
                continue
            
            # Se faz parte da malha de corredores, ele é um portão válido.
            if nid in connected:
                gateways.add(nid)
                
        return gateways

    def _region_has_corridor(self, anchor_pos, radius_nm: float) -> bool:
        """A região do âncora é ESTRUTURADA? (tem corredor real por perto)"""
        radius_m = radius_nm * 1852.0
        connected: set[str] = set()
        for edges in self.adj.values():
            for e in edges:
                if not e.synthetic:
                    connected.add(e.source)
                    connected.add(e.target)
        for nid in connected:
            if haversine_m(self.nodes[nid].pos, anchor_pos) <= radius_m:
                return True
        return False

    def add_synthetic_edges(
        self,
        origin_id: str,
        dest_id: str,
        max_link_nm: float = 30.0,
        free_entry_nm: float = 15.0,
        gateways_only: bool = True,
    ) -> dict:
        """Liga os aeródromos à malha e cria as pontes de cruzeiro unidirecionais."""
        origin = self.nodes[origin_id]
        dest = self.nodes[dest_id]
        max_link_m = max_link_nm * 1852.0

        gateways = self._gateway_waypoints() if gateways_only else None

        dep_structured = self._region_has_corridor(origin.pos, max_link_nm)
        arr_structured = self._region_has_corridor(dest.pos, max_link_nm)

        linked_dep = 0
        linked_arr = 0
        
        # 1. Liga Aeroportos aos Portões/Nós Visuais da sua própria TMA
        for nid, node in self.nodes.items():
            if node.kind != "waypoint":
                continue
            if gateways is not None and nid not in gateways:
                continue
            
            d_o = haversine_m(origin.pos, node.pos)
            if d_o <= max_link_m:
                self.add_edge(Edge(origin_id, nid, d_o, synthetic=True))
                linked_dep += 1
                
            d_d = haversine_m(node.pos, dest.pos)
            if d_d <= max_link_m:
                self.add_edge(Edge(nid, dest_id, d_d, synthetic=True))
                linked_arr += 1

        def classify(structured, linked):
            if structured and linked > 0: return "gateway"
            if not structured: return "free"
            return "no_gate"

        dep_status = classify(dep_structured, linked_dep)
        arr_status = classify(arr_structured, linked_arr)
        
        gateways_list = list(gateways) if gateways else []

        # 2. PARTIÇÃO DE NÓS (Evita o Efeito Ping-Pong)
        # Classifica se o portão pertence à malha de saída ou à malha de chegada
        # baseado em quem ele está fisicamente mais próximo.
        dep_gateways = [g for g in gateways_list if haversine_m(self.nodes[g].pos, origin.pos) < haversine_m(self.nodes[g].pos, dest.pos)]
        arr_gateways = [g for g in gateways_list if haversine_m(self.nodes[g].pos, dest.pos) < haversine_m(self.nodes[g].pos, origin.pos)]

        # 3. PONTE DE CRUZEIRO UNIDIRECIONAL
        
        # Cenário A: Partida Estruturada -> Destino Livre
        if dep_status == "gateway" and arr_status in ("free", "no_gate"):
            for gid in dep_gateways:
                node_g = self.nodes[gid]
                d = haversine_m(node_g.pos, dest.pos)
                self.add_edge(Edge(gid, dest_id, d, synthetic=True))

        # Cenário B: Partida Livre -> Destino Estruturado
        elif dep_status in ("free", "no_gate") and arr_status == "gateway":
            for gid in arr_gateways:
                node_g = self.nodes[gid]
                d = haversine_m(origin.pos, node_g.pos)
                self.add_edge(Edge(origin_id, gid, d, synthetic=True))

        # Cenário C: Ambos Estruturados (Ex: SP -> Manaus, SP -> RJ)
        elif dep_status == "gateway" and arr_status == "gateway":
            for gid_dep in dep_gateways:
                node_dep = self.nodes[gid_dep]
                for gid_arr in arr_gateways:
                    node_arr = self.nodes[gid_arr]
                    
                    # A aresta agora é estritamente unidirecional: Origem -> Destino
                    d = haversine_m(node_dep.pos, node_arr.pos)
                    if d > (max_link_m * 1.5): # Só cria ponte se a distância for viável
                        self.add_edge(Edge(gid_dep, gid_arr, d, synthetic=True))

        # Cenário D: Ambos Livres (Interior -> Interior)
        elif dep_status in ("free", "no_gate") and arr_status in ("free", "no_gate"):
            self.add_edge(
                Edge(origin_id, dest_id, haversine_m(origin.pos, dest.pos), synthetic=True)
            )

        # [Fallback de Segurança]: Garante que o grafo nunca fique 100% desconexo.
        # A penalidade severa no GWO (mandatory_factor = 20) vai impedir que o 
        # otimizador use esta aresta a menos que seja a única saída possível.
        if dep_status in ("gateway", "no_gate") or arr_status in ("gateway", "no_gate"):
            self.add_edge(
                Edge(origin_id, dest_id, haversine_m(origin.pos, dest.pos), synthetic=True)
            )

        return {
            "departure_status": dep_status,
            "arrival_status": arr_status,
            "departure_structured": dep_structured,
            "arrival_structured": arr_structured,
            "gateways_linked_departure": linked_dep,
            "gateways_linked_arrival": linked_arr,
        }

    # ---------------------------------------------------------------- query
    @property
    def n(self) -> int:
        return len(self.rev_index)

    def successors(self, node_id: str) -> list[Edge]:
        return self.adj[node_id]

    def direct_distance_m(self, origin_id: str, dest_id: str) -> float:
        return haversine_m(self.nodes[origin_id].pos, self.nodes[dest_id].pos)

    def corridor_nodes_near(self, anchor_id: str, radius_nm: float) -> set[str]:
        anchor = self.nodes[anchor_id]
        radius_m = radius_nm * 1852.0
        out: set[str] = set()
        for edges in self.adj.values():
            for e in edges:
                if e.synthetic: continue
                for nid in (e.source, e.target):
                    node = self.nodes[nid]
                    if haversine_m(node.pos, anchor.pos) <= radius_m:
                        out.add(nid)
        return out

    def mandatory_arrival_nodes(self, dest_id: str, radius_nm: float = 15.0) -> set[str]:
        dest = self.nodes[dest_id]
        out: set[str] = set()
        for edges in self.adj.values():
            for e in edges:
                if not e.is_mandatory or e.synthetic: continue
                tgt = self.nodes[e.target]
                if haversine_m(tgt.pos, dest.pos) <= radius_nm * 1852.0:
                    out.add(e.target)
        return out