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

        Portão = waypoint cujo nome contém 'PORTÃO'/'PORTAO'. Conectado =
        participa de ao menos uma aresta REAL de corredor (como origem ou
        destino). Portões isolados (sem corredor) NÃO entram — entrar neles
        seria um beco sem saída.
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
            name = (node.name or "").upper()
            is_gate = "PORTÃO" in name or "PORTAO" in name
            if is_gate and nid in connected:
                gateways.add(nid)
        return gateways

    def _region_has_corridor(self, anchor_pos, radius_nm: float) -> bool:
        """A região do âncora é ESTRUTURADA? (tem corredor real por perto)

        True se existe qualquer waypoint de corredor real dentro do raio —
        indica TMA estruturada, onde a entrada deve ser por portão. False
        indica região livre (cidade média), onde a rota direta é legítima.
        """
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
        """Liga os aeródromos à malha conforme o tipo de espaço aéreo.

        Dois cenários (regra aeronáutica):
        - TMA ESTRUTURADA (há corredor na região do extremo): entrada/saída
          SÓ por portão conectado, dentro de `max_link_nm`. Pode exigir
          desvio (quebra da linha reta) para interceptar o portão.
        - REGIÃO LIVRE (sem corredor por perto): navegação direta legítima;
          liga origem↔destino sem portão (cidade média, ingresso via APP).

        Retorna um diagnóstico por extremo, para a camada de saída poder
        avisar se um destino ESTRUTURADO ficou sem portão viável (caso que
        merece atenção, não um fallback silencioso).
        """
        origin = self.nodes[origin_id]
        dest = self.nodes[dest_id]
        max_link_m = max_link_nm * 1852.0

        gateways = self._gateway_waypoints() if gateways_only else None

        # A região de cada extremo é estruturada?
        dep_structured = self._region_has_corridor(origin.pos, max_link_nm)
        arr_structured = self._region_has_corridor(dest.pos, max_link_nm)

        linked_dep = 0
        linked_arr = 0
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

        # Classifica cada extremo:
        #   'gateway'  → estruturada e achou portão (entrada correta por portão)
        #   'free'     → região livre, rota direta legítima
        #   'no_gate'  → ESTRUTURADA mas sem portão viável no raio (ALERTA)
        def classify(structured, linked):
            if structured and linked > 0:
                return "gateway"
            if not structured:
                return "free"
            return "no_gate"

        dep_status = classify(dep_structured, linked_dep)
        arr_status = classify(arr_structured, linked_arr)

        # Rota direta: legítima quando algum extremo é LIVRE; também usada como
        # último recurso quando um extremo estruturado ficou sem portão (mas
        # nesse caso o status 'no_gate' sinaliza que a rota pode ser inválida).
        need_direct = (dep_status in ("free", "no_gate")
                       or arr_status in ("free", "no_gate"))
        if need_direct:
            self.add_edge(
                Edge(origin_id, dest_id,
                     haversine_m(origin.pos, dest.pos), synthetic=True)
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