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
    geom: Optional[tuple] = None        # traçado real do corredor: ((lon,lat), ...) — só nas arestas reais


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

    # ------------------------------------------------------ consultas de topologia
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

    def _has_real_incoming(self, node_id: str) -> bool:
        """O nó é ALCANÇADO por algum corredor real? (chega-se nele voando REA).

        Usado na saída ao destino: só se sai de uma TMA por um nó em que se
        ENTROU por corredor — evita usar um waypoint isolado/sem corredor de
        entrada como mero degrau de saída (pular a malha). Quando é PERMITIDO
        usar a saída é decidido pela fase 'owes' no caminho mínimo."""
        return any((not e.synthetic) and e.target == node_id
                   for src in self.adj for e in self.adj[src])

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

    # ----------------------------------------------- portão geométrico (V2: cruzamento)
    # PROBLEMA: um trecho sintético "DIRETO" é uma reta que só conhece suas duas
    # pontas. Ela pode passar POR CIMA de um corredor REA obrigatório que a rota
    # não voa — algo proibido (cortar o espaço protegido de um corredor). A fase
    # 'owes' resolve a topologia ("entrar obriga a voar"), mas NÃO a geometria.
    # Aqui adicionamos um teste geométrico na CONSTRUÇÃO do grafo: a aresta
    # sintética que cruzar um corredor obrigatório de uma carta RELEVANTE não é
    # criada. Assim o caminho mínimo de fase é forçado a entrar pelo portal certo.
    @staticmethod
    def _proper_cross(p1, p2, p3, p4) -> bool:
        """True se os segmentos p1p2 e p3p4 se cruzam no INTERIOR (cruzamento
        próprio). Toque em extremidade compartilhada (ex.: portal) ou colinear
        NÃO conta — todas as orientações precisam ser estritamente não-nulas."""
        def o(a, b, c) -> float:
            return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        d1, d2 = o(p3, p4, p1), o(p3, p4, p2)
        d3, d4 = o(p1, p2, p3), o(p1, p2, p4)
        if d1 == 0 or d2 == 0 or d3 == 0 or d4 == 0:
            return False
        return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)

    def _mandatory_segments(self, charts: set) -> list[tuple]:
        """Sub-segmentos (p1,p2) dos corredores REAIS OBRIGATÓRIOS das cartas
        relevantes. Usa o traçado real (Edge.geom) quando há; senão, a reta
        entre os dois nós. Trabalha em lon/lat planar — para a escala de uma TMA
        o cruzamento é invariante (um reescalonamento de longitude é afim)."""
        segs: list[tuple] = []
        if not charts:
            return segs
        for src, edges in self.adj.items():
            for e in edges:
                if e.synthetic or not e.is_mandatory:
                    continue
                if self.nodes[e.source].chart not in charts:
                    continue
                if e.geom and len(e.geom) >= 2:
                    pts = e.geom
                else:
                    a, b = self.nodes[e.source].pos, self.nodes[e.target].pos
                    pts = ((a.lon, a.lat), (b.lon, b.lat))
                for i in range(len(pts) - 1):
                    segs.append((pts[i], pts[i + 1]))
        return segs

    def _crosses_mandatory(self, a_pos, b_pos, segs: list) -> bool:
        """A reta a->b cruza algum corredor obrigatório (interior)? Ignora o
        sub-segmento que compartilha extremidade com a reta (toque no portal)."""
        if not segs:
            return False
        p1, p2 = (a_pos.lon, a_pos.lat), (b_pos.lon, b_pos.lat)
        eps = 1e-6
        def _touch(p, q) -> bool:
            return abs(p[0] - q[0]) < eps and abs(p[1] - q[1]) < eps
        for q1, q2 in segs:
            if _touch(p1, q1) or _touch(p1, q2) or _touch(p2, q1) or _touch(p2, q2):
                continue
            if self._proper_cross(p1, p2, q1, q2):
                return True
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

        # PORTÃO GEOMÉTRICO: só atua contra os corredores OBRIGATÓRIOS das cartas
        # RELEVANTES (carta da origem e/ou do destino quando a ponta está em TMA).
        # Sem ponta em TMA => conjunto vazio => portão é no-op (preserva os casos
        # "Nenhuma REA" e "REA não relevante": o DIRETO continua liberado).
        relevant_charts = set()
        if origin_in_tma and origin_chart:
            relevant_charts.add(origin_chart)
        if dest_in_tma and dest_chart:
            relevant_charts.add(dest_chart)
        mand_segs = self._mandatory_segments(relevant_charts)
        self._gate_charts = relevant_charts          # cartas que o portão protege
        entries_gated = exits_gated = bridges_gated = 0
        entries_relaxed = exits_relaxed = 0

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
        # portão: descarta entradas cuja reta cruza um corredor obrigatório.
        kept_entries = [nid for nid in entry_targets
                        if not self._crosses_mandatory(origin.pos, self.nodes[nid].pos, mand_segs)]
        entries_gated = len(entry_targets) - len(kept_entries)
        if not kept_entries and entry_targets:   # válvula: não isolar a origem
            kept_entries, entries_gated, entries_relaxed = entry_targets, 0, 1
        for nid in kept_entries:
            d = haversine_m(origin.pos, self.nodes[nid].pos)
            self.add_edge(Edge(origin_id, nid, w(d), corridor="DIRETO", synthetic=True))
            linked_in += 1

        # 2) SAÍDA: REA -> destino.
        #    A saída sai de um nó ALCANÇADO por corredor (tem corredor de entrada);
        #    QUANDO ela pode ser usada é decidido pela fase 'owes' no caminho mínimo
        #    (só num terminal natural, owes_real==0). Não há mais trava estática:
        #    a antiga trava bloqueava nós com corredor obrigatório de saída — mas
        #    é justamente o caso de um PORTÃO (ex.: DUTRA) cujo corredor volta para
        #    dentro da TMA; ele é a saída natural rumo ao destino e deve poder sair.
        if dest_in_tma:
            exit_pool = [n for n in rea_nodes
                         if self.nodes[n].chart == dest_chart and self._has_real_incoming(n)]
            exit_sources = self._k_nearest(dest.pos, exit_pool or rea_nodes, entry_exit_k)
        else:
            exit_sources = [n for n in rea_nodes if self._has_real_incoming(n)] or rea_nodes
        exit_candidates = [(haversine_m(self.nodes[nid].pos, dest.pos), nid)
                           for nid in exit_sources]
        # portão: descarta saídas cuja reta até o destino cruza corredor obrigatório.
        kept_exits = [(d, nid) for d, nid in exit_candidates
                      if not self._crosses_mandatory(self.nodes[nid].pos, dest.pos, mand_segs)]
        exits_gated = len(exit_candidates) - len(kept_exits)
        if not kept_exits and exit_candidates:    # válvula: não isolar o destino
            kept_exits, exits_gated, exits_relaxed = exit_candidates, 0, 1
        for d, nid in kept_exits:
            self.add_edge(Edge(nid, dest_id, w(d), corridor="DIRETO", synthetic=True))
            linked_out += 1
        exits_safety_valve = 0
        if linked_out == 0 and rea_nodes:              # válvula de saída (último recurso)
            d, nid = min(((haversine_m(self.nodes[n].pos, dest.pos), n) for n in rea_nodes),
                         key=lambda t: t[0])
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
                # portão: a ponte não pode cortar um corredor obrigatório relevante.
                if self._crosses_mandatory(pa, self.nodes[b].pos, mand_segs):
                    bridges_gated += 1
                    continue
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
            "entries_gated_crossing": entries_gated,
            "exits_gated_crossing": exits_gated,
            "bridges_gated_crossing": bridges_gated,
            "entries_relaxed_crossing": entries_relaxed,
            "exits_relaxed_crossing": exits_relaxed,
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