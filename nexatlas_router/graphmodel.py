"""Modelo do subgrafo de roteamento em memória (esquema published).

O grafo é DIRECIONADO (special_routes_connections define source_id -> target_id
com proa). Além das arestas reais dos corredores REA, recebe arestas sintéticas
que materializam os trechos "DIRETO" observados nos casos de referência:

  - origem -> qualquer nó REA no raio        (entrada DIRETA na malha)
  - nó REA no raio -> destino                (saída DIRETA da malha)  *com TRAVA*
  - nó REA -> nó REA entre cartas distintas  (salto DIRETO entre TMAs) *com TRAVA*

A aresta direta origem->destino NÃO é criada junto com as demais: ela é um
atalho que venceria qualquer rota por corredor na minimização de distância.
É adicionada só como FALLBACK (add_direct_fallback) quando a malha REA não
conecta os pontos — política "malha-primeiro" em v1.plan_v1_route.

TRAVA DE CONTINUIDADE (CRÍTICO)
-------------------------------
Para impedir que a rota "fuja" no meio de um corredor obrigatório, nenhuma
aresta sintética de SAÍDA é criada a partir de um nó que ainda possua uma
aresta REAL de saída com is_mandatory=True. A aeronave é forçada a concluir o
trecho obrigatório antes de poder saltar para o destino ou para outra TMA.
"""
from __future__ import annotations

from dataclasses import dataclass
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
    corridor: Optional[str] = None      # name em special_routes_connections (ex: "REA KILO")
    connection_id: Optional[str] = None
    is_mandatory: bool = False          # is_mandatory do trecho REA
    lower_limit: Optional[int] = None
    higher_limit: Optional[int] = None
    synthetic: bool = False             # True para arestas "DIRETO" criadas em runtime


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

    # ------------------------------------------------------ TRAVA DE CONTINUIDADE
    def _has_mandatory_real_exit(self, node_id: str) -> bool:
        """TRAVA DE CONTINUIDADE: o nó tem alguma aresta REAL de saída obrigatória?

        Se sim, ele NÃO pode receber pontes sintéticas de saída (para o destino
        ou para outra TMA): a aeronave precisa seguir o trecho obrigatório até o
        fim. Arestas sintéticas são ignoradas neste teste.
        """
        return any((not e.synthetic) and e.is_mandatory
                   for e in self.adj.get(node_id, []))

    def _has_real_edge(self, src: str, tgt: str) -> bool:
        """Já existe um corredor REAL entre estes dois nós? (evita duplicar com ponte)."""
        return any((not e.synthetic) and e.target == tgt
                   for e in self.adj.get(src, []))

    def _has_real_outgoing(self, node_id: str) -> bool:
        """O nó tem alguma aresta REAL de saída? (não é beco-sem-saída de corredor).

        Usado nas pontes inter-TMA: ao saltar para outra carta, a aeronave deve
        cair num nó de onde AINDA dá para voar um corredor real — assim a TMA de
        destino é efetivamente usada (impede pular para o fim da cadeia e sair
        direto, análogo ao caso SWPI no lado da chegada)."""
        return any(not e.synthetic for e in self.adj.get(node_id, []))

    def _nearest_rea_m(self, pos, rea_nodes) -> float:
        """Distância (m) do ponto ao nó REA mais próximo (inf se não houver)."""
        if not rea_nodes:
            return float("inf")
        return min(haversine_m(pos, self.nodes[nid].pos) for nid in rea_nodes)

    def _k_nearest(self, pos, rea_nodes, k: int) -> list[str]:
        """Os k nós REA mais próximos de um ponto (por distância geodésica)."""
        return [nid for _, nid in
                sorted((haversine_m(pos, self.nodes[nid].pos), nid)
                       for nid in rea_nodes)[:k]]

    def _reaches(self, origin_id: str, dest_id: str) -> bool:
        """Existe caminho origem->destino no grafo atual? (BFS sobre adj)."""
        seen = {origin_id}
        stack = [origin_id]
        while stack:
            u = stack.pop()
            if u == dest_id:
                return True
            for e in self.adj.get(u, []):
                if e.target not in seen:
                    seen.add(e.target)
                    stack.append(e.target)
        return False

    def add_synthetic_edges(
        self,
        origin_id: str,
        dest_id: str,
        tma_radius_nm: float = 60.0,
        entry_exit_k: int = 6,
        inter_tma_nm: float = 300.0,
        bridge_k: int = 6,
        synth_penalty: float = 1.0,
    ) -> dict:
        """Liga aeródromos e TMAs por trechos 'DIRETO', seguindo a regra REA.

        REGRA OPERACIONAL (extraída dos casos de referência): se o aeródromo
        está DENTRO de uma TMA com malha REA, a aeronave é OBRIGADA a usar os
        corredores — não existe trecho direto origem->destino "pulando" a REA.
        O direto só vale quando a ponta NÃO está em TMA REA (entrar/sair da
        malha por um voo livre longo) ou quando NENHUMA ponta tem REA.

        Modelo ASSIMÉTRICO conforme a ponta esteja "em TMA":
          • ponta EM TMA  -> conecta só aos k nós REA MAIS PRÓXIMOS.
          • ponta FORA    -> conecta a TODOS os nós REA (voo livre longo).
          • aresta direta origem->destino só se NENHUMA ponta em TMA.

        PREFERÊNCIA POR CORREDOR: os trechos sintéticos "DIRETO" recebem um peso
        levemente inflado (synth_penalty). Assim, em empates de distância, a
        rota prefere voar o corredor REA real a "recortar" um waypoint por um
        trecho direto (corrige casos como SWPI->SJSE, em que a rota colava em
        TERRA por reta em vez de voar RESERVA SILVA->TERRA). O peso real
        (geográfico) é recomputado para o relatório em v1.

        VÁLVULA DE PONTE: se, após as pontes normais, a malha ainda não conectar
        origem->destino, forçamos pontes inter-TMA (rumo ao destino) IGNORANDO a
        Trava — caso contrário duas TMAs com tudo obrigatório ficariam desconexas
        e a rota cairia indevidamente no direto.
        """
        origin = self.nodes[origin_id]
        dest = self.nodes[dest_id]
        inter_tma_m = inter_tma_nm * 1852.0
        tma_radius_m = tma_radius_nm * 1852.0

        def w(d: float) -> float:
            return d * synth_penalty   # peso de otimização do trecho sintético

        rea_nodes = [nid for nid, n in self.nodes.items() if n.kind == "waypoint"]

        # Está a ponta DENTRO de uma TMA REA? (define a regra de obrigatoriedade)
        origin_in_tma = self._nearest_rea_m(origin.pos, rea_nodes) <= tma_radius_m
        dest_in_tma = self._nearest_rea_m(dest.pos, rea_nodes) <= tma_radius_m

        linked_in = linked_out = locked_out = bridges = 0

        # Carta local de cada ponta (a TMA em que o aeródromo está).
        def _local_chart(pos):
            best = min(rea_nodes, key=lambda n: haversine_m(pos, self.nodes[n].pos),
                       default=None)
            return self.nodes[best].chart if best else None
        origin_chart = _local_chart(origin.pos) if origin_in_tma else None
        dest_chart = _local_chart(dest.pos) if dest_in_tma else None

        # 1) ENTRADA: origem -> REA.
        #    Em TMA: liga aos k nós-portal (com corredor de saída) mais próximos
        #    DA PRÓPRIA CARTA da origem — entrar já obriga a voar o corredor
        #    daquela TMA (a fase 'owes' do caminho restrito cuida do resto).
        #    Fora de TMA: liga a todos (voo livre longo até a malha da chegada).
        if origin_in_tma:
            portals = [n for n in rea_nodes
                       if self._has_real_outgoing(n) and self.nodes[n].chart == origin_chart]
            entry_targets = self._k_nearest(origin.pos, portals or rea_nodes, entry_exit_k)
        else:
            entry_targets = rea_nodes
        for nid in entry_targets:
            d = haversine_m(origin.pos, self.nodes[nid].pos)
            self.add_edge(Edge(origin_id, nid, w(d), corridor="DIRETO", synthetic=True))
            linked_in += 1

        # 2) SAÍDA: REA -> destino, COM TRAVA DE CONTINUIDADE.
        #    Em TMA: só dos k mais próximos DA CARTA do destino. Fora: de todos.
        if dest_in_tma:
            exit_pool = [n for n in rea_nodes if self.nodes[n].chart == dest_chart]
            exit_sources = self._k_nearest(dest.pos, exit_pool or rea_nodes, entry_exit_k)
        else:
            exit_sources = rea_nodes
        exit_candidates = [(haversine_m(self.nodes[nid].pos, dest.pos), nid)
                           for nid in exit_sources]
        for d, nid in exit_candidates:
            if self._has_mandatory_real_exit(nid):     # TRAVA
                locked_out += 1
                continue
            self.add_edge(Edge(nid, dest_id, w(d), corridor="DIRETO", synthetic=True))
            linked_out += 1
        exits_safety_valve = 0
        if linked_out == 0 and exit_candidates:        # válvula de saída
            d, nid = min(exit_candidates, key=lambda t: t[0])
            self.add_edge(Edge(nid, dest_id, w(d), corridor="DIRETO", synthetic=True))
            linked_out += 1; exits_safety_valve = 1

        # 3) PONTES INTER-TMA (entre cartas), EM DIREÇÃO AO DESTINO. Travadas.
        by_chart: dict[str, list[str]] = {}
        for nid in rea_nodes:
            by_chart.setdefault(self.nodes[nid].chart, []).append(nid)
        charts = list(by_chart)

        d_dest = {nid: haversine_m(self.nodes[nid].pos, dest.pos) for nid in rea_nodes}
        for a in rea_nodes:
            # NOTA: a Trava de Continuidade NÃO se aplica às pontes inter-TMA.
            # A spec a definia só para as "pontes diretas para o DESTINO" (saídas).
            # Em transições entre TMAs o piloto salta a partir do nó que melhor
            # alimenta a próxima TMA (ex.: JAZIDA->CAMPO MOURÃO), mesmo que esse
            # nó tenha corredor obrigatório. Travar aqui descartava esses saltos.
            chart_a = self.nodes[a].chart
            pa = self.nodes[a].pos
            cands = []
            for b in rea_nodes:
                if self.nodes[b].chart == chart_a or d_dest[b] >= d_dest[a]:
                    continue
                # alvo da ponte deve poder VOAR um corredor depois (não beco)
                if not self._has_real_outgoing(b):
                    continue
                d = haversine_m(pa, self.nodes[b].pos)
                if d <= inter_tma_m and not self._has_real_edge(a, b):
                    cands.append((d, b))
            cands.sort(key=lambda t: t[0])
            for d, b in cands[:bridge_k]:
                self.add_edge(Edge(a, b, w(d), corridor="DIRETO", synthetic=True))
                bridges += 1

        # 3b) VÁLVULA DE PONTE: se a malha ainda não conecta origem->destino
        #     (Trava bloqueou todas as pontes possíveis), força as melhores
        #     pontes progressivas IGNORANDO a Trava, até conectar.
        bridges_safety_valve = 0
        if len(charts) > 1 and rea_nodes and not self._reaches(origin_id, dest_id):
            def _forced_pairs(require_outgoing: bool):
                out = []
                for a in rea_nodes:
                    ca = self.nodes[a].chart
                    pa = self.nodes[a].pos
                    for b in rea_nodes:
                        if self.nodes[b].chart == ca or d_dest[b] >= d_dest[a]:
                            continue
                        if require_outgoing and not self._has_real_outgoing(b):
                            continue
                        d = haversine_m(pa, self.nodes[b].pos)
                        if d <= inter_tma_m and not self._has_real_edge(a, b):
                            out.append((d, a, b))
                out.sort(key=lambda t: t[0])
                return out
            # 1ª tentativa: pontes que caem em nó com corredor (usa a TMA destino);
            # 2ª tentativa (se ainda desconexo): qualquer par viável.
            for require_out in (True, False):
                for d, a, b in _forced_pairs(require_out):
                    if self._reaches(origin_id, dest_id):
                        break
                    self.add_edge(Edge(a, b, w(d), corridor="DIRETO", synthetic=True))
                    bridges += 1; bridges_safety_valve += 1
                if self._reaches(origin_id, dest_id):
                    break

        # 4) DIRETO origem->destino: SÓ se NENHUMA ponta está em TMA REA.
        direct_created = False
        if not origin_in_tma and not dest_in_tma:
            self.add_edge(Edge(origin_id, dest_id,
                               w(self.direct_distance_m(origin_id, dest_id)),
                               corridor="DIRETO", synthetic=True))
            direct_created = True

        # Flag lida por v1.plan_v1_route: ponta em TMA REA => rota OBRIGADA a
        # usar >=1 corredor real (caminho mínimo restrito em dijkstra.shortest_route).
        self.requires_corridor = origin_in_tma or dest_in_tma

        return {
            "origin_in_tma": origin_in_tma,
            "dest_in_tma": dest_in_tma,
            "entries_linked": linked_in,
            "exits_linked": linked_out,
            "exits_locked_by_continuity": locked_out,
            "exits_safety_valve": exits_safety_valve,
            "inter_tma_bridges": bridges,
            "bridges_safety_valve": bridges_safety_valve,
            "direct_created": direct_created,
            "requires_corridor": self.requires_corridor,
            "n_rea_nodes": len(rea_nodes),
            "n_charts": len(charts),
        }

    def add_direct_fallback(self, origin_id: str, dest_id: str) -> bool:
        """Adiciona a aresta DIRETO origem->destino (fallback de factibilidade).

        Chamada só quando a malha REA não conectou os pontos (casos "Nenhuma
        REA" e "REA não relevante", ex.: SBHT->SBPJ, SWWA->SBUL). Retorna True
        se a aresta foi criada.
        """
        if self._has_real_edge(origin_id, dest_id):
            return False
        if any(e.target == dest_id and e.synthetic for e in self.adj.get(origin_id, [])):
            return False
        self.add_edge(Edge(origin_id, dest_id,
                           self.direct_distance_m(origin_id, dest_id),
                           corridor="DIRETO", synthetic=True))
        return True

    # ---------------------------------------------------------------- query
    @property
    def n(self) -> int:
        return len(self.rev_index)

    def successors(self, node_id: str) -> list[Edge]:
        return self.adj[node_id]

    def direct_distance_m(self, origin_id: str, dest_id: str) -> float:
        return haversine_m(self.nodes[origin_id].pos, self.nodes[dest_id].pos)