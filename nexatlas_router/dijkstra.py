"""Dijkstra exato com ESTADO DE FASE (regra REA) e k-shortest (Yen).

`shortest_route` é a AUTORIDADE da rota principal: caminho mínimo exato e
determinístico que codifica a regra das TMAs (voar os corredores antes de
pegar um DIRETO). `k_shortest_routes` gera as K melhores rotas DISTINTAS e
VÁLIDAS pelo algoritmo de Yen, sobre o MESMO grafo com fase — é o gerador de
alternativas (substitui o GWO, que não conhecia a regra de fase). A função
`dijkstra` (sem fase) permanece como oráculo simples de teste.
"""
from __future__ import annotations

import heapq
from typing import Optional

from .graphmodel import Edge, RouteGraph
from .gwo import DecodedRoute
from .geo import haversine_m as _haversine


def dijkstra(graph: RouteGraph, origin_id: str, dest_id: str) -> Optional[DecodedRoute]:
    dist: dict[str, float] = {origin_id: 0.0}
    prev: dict[str, tuple[str, Edge]] = {}
    heap: list[tuple[float, str]] = [(0.0, origin_id)]
    done: set[str] = set()

    while heap:
        d, u = heapq.heappop(heap)
        if u in done:
            continue
        done.add(u)
        if u == dest_id:
            break
        for e in graph.successors(u):
            nd = d + e.weight_m
            if nd < dist.get(e.target, float("inf")):
                dist[e.target] = nd
                prev[e.target] = (u, e)
                heapq.heappush(heap, (nd, e.target))

    if dest_id not in dist:
        return None

    node_ids = [dest_id]
    edges: list[Edge] = []
    cur = dest_id
    while cur != origin_id:
        parent, edge = prev[cur]
        edges.append(edge)
        node_ids.append(parent)
        cur = parent
    node_ids.reverse()
    edges.reverse()

    route = DecodedRoute(node_ids, edges, dist[dest_id], complete=True)
    route.fitness = dist[dest_id]
    return route


# --------------------------------------------------------------------------
# Núcleo com estado de fase (nó, owes, used) — compartilhado por shortest_route
# e pelos sub-caminhos 'spur' do Yen.
# --------------------------------------------------------------------------
def _make_owes(graph: RouteGraph, dest_id: str):
    """Constrói as duas funções de obrigação de fase (owes). Fatorado para o
    k-shortest reaproveitar EXATAMENTE a mesma regra da rota principal.

      owes : 1 se a aeronave está 'dentro' de um corredor e DEVE continuar por
             corredor real (não pode pegar DIRETO agora).
      used : 1 se já voou >=1 corredor real (exigido se uma ponta está em TMA).

    owes_real  -> chegou por CORREDOR: deve 1 se há corredor de saída que AVANÇA
                  rumo ao destino; senão 0 (terminal natural -> pode sair).
    owes_synth -> chegou por trecho SINTÉTICO (entrou/saltou numa TMA): deve 1 se
                  v tem QUALQUER corredor real de saída (entrar obriga a voar).
    """
    nodes = graph.nodes
    dpos = nodes[dest_id].pos
    d_dest = {nid: _haversine(nodes[nid].pos, dpos) for nid in nodes}

    def owes_real(v: str) -> int:
        for e in graph.adj.get(v, []):
            if (not e.synthetic) and d_dest[e.target] < d_dest[v] - 1.0:
                return 1
        return 0

    def owes_synth(v: str) -> int:
        for e in graph.adj.get(v, []):
            if not e.synthetic:
                return 1
        return 0

    return owes_real, owes_synth


def _edge_key(e: Edge) -> tuple:
    """Identidade de uma aresta DIRIGIDA, para banimento no Yen."""
    return (e.source, e.target, e.corridor, e.synthetic)


def _phase_shortest(graph: RouteGraph, dest_id: str, need_used: int,
                    start_state: tuple, start_cost: float,
                    banned_edges: frozenset, banned_nodes: frozenset,
                    owes_real, owes_synth):
    """Caminho mínimo com estado de fase, GENÉRICO: parte de `start_state`
    (nó, owes, used) com `start_cost`, podendo proibir arestas dirigidas
    (`banned_edges`) e nós (`banned_nodes`, exceto o nó inicial). Objetivo:
    (dest, owes==0, used>=need_used). Retorna (node_ids, edges, custo) ou None.
    É o núcleo do shortest_route e dos sub-caminhos 'spur' do Yen.
    """
    INF = float("inf")
    dist: dict[tuple, float] = {start_state: start_cost}
    prev: dict[tuple, tuple[tuple, Edge]] = {}
    heap: list[tuple[float, str, int, int]] = [
        (start_cost, start_state[0], start_state[1], start_state[2])]
    done: set[tuple] = set()
    goal: Optional[tuple] = None

    while heap:
        d, u, owes, used = heapq.heappop(heap)
        st = (u, owes, used)
        if st in done:
            continue
        done.add(st)
        if u == dest_id and owes == 0 and used >= need_used:
            goal = st
            break
        for e in graph.adj.get(u, []):
            if e.target in banned_nodes:
                continue
            if _edge_key(e) in banned_edges:
                continue
            real = not e.synthetic
            if owes == 1 and not real:
                continue                      # dentro de corredor: não pode DIRETO
            nused = 1 if (used or real) else 0
            nowes = owes_real(e.target) if real else owes_synth(e.target)
            ns = (e.target, nowes, nused)
            nd = d + e.weight_m
            if nd < dist.get(ns, INF):
                dist[ns] = nd
                prev[ns] = (st, e)
                heapq.heappush(heap, (nd, e.target, nowes, nused))

    if goal is None:
        return None

    node_ids = [goal[0]]
    edges: list[Edge] = []
    cur = goal
    while cur != start_state:
        parent_state, edge = prev[cur]
        edges.append(edge)
        node_ids.append(parent_state[0])
        cur = parent_state
    node_ids.reverse()
    edges.reverse()
    return node_ids, edges, dist[goal]


def shortest_route(graph: RouteGraph, origin_id: str, dest_id: str,
                   require_real_edge: bool = False) -> Optional[DecodedRoute]:
    """Caminho mínimo EXATO com ESTADO DE FASE (regra REA das TMAs).

    Ao entrar numa TMA com REA, a aeronave precisa VOAR os corredores dela até
    um terminal natural antes de pegar um DIRETO (entrada/saída/salto). Modelado
    como rótulo de estado (nó, owes, used) no Dijkstra — exato e determinístico.
    A OBRIGAÇÃO depende de COMO se chega ao nó (owes_real vs owes_synth, ver
    _make_owes). Objetivo: (dest, owes=0, used>=need). Impede tanto 'tangenciar
    um waypoint e sair reto' quanto 'entrar/saltar numa TMA e pular o corredor'.
    """
    owes_real, owes_synth = _make_owes(graph, dest_id)
    need_used = 1 if require_real_edge else 0
    res = _phase_shortest(graph, dest_id, need_used, (origin_id, 0, 0), 0.0,
                          frozenset(), frozenset(), owes_real, owes_synth)
    if res is None:
        return None
    node_ids, edges, cost = res
    route = DecodedRoute(node_ids, edges, cost, complete=True)
    route.fitness = cost
    return route


def k_shortest_routes(graph: RouteGraph, origin_id: str, dest_id: str,
                      k: int = 5, require_real_edge: bool = False
                      ) -> list[DecodedRoute]:
    """As K melhores rotas DISTINTAS e VÁLIDAS (algoritmo de Yen) sobre o mesmo
    grafo com estado de fase. A 1ª é idêntica à de shortest_route; as seguintes
    são as próximas melhores, TODAS respeitando owes/used (não pulam corredor
    nem entram no meio) e ordenadas por distância de otimização. Substitui o GWO
    como gerador de alternativas: determinístico e correto por construção.

    Yen sobre grafo com fase: o sub-caminho 'spur' parte do nó de desvio JÁ COM
    a fase (owes/used) acumulada ao longo do trecho-raiz (replay das transições),
    de modo que cada alternativa nasce válida.
    """
    owes_real, owes_synth = _make_owes(graph, dest_id)
    need_used = 1 if require_real_edge else 0

    first = _phase_shortest(graph, dest_id, need_used, (origin_id, 0, 0), 0.0,
                            frozenset(), frozenset(), owes_real, owes_synth)
    if first is None:
        return []

    A: list[tuple] = [first]               # (node_ids, edges, custo_total)
    seen: set[tuple] = {tuple(first[0])}
    B: list[tuple] = []                    # candidatos: (custo, node_ids, edges)

    while len(A) < k:
        prev_nodes, prev_edges, _ = A[-1]

        # Fase/custo acumulados ao longo do trecho-raiz (replay das transições),
        # para iniciar cada 'spur' no estado correto.
        owes_at = [0]                      # estado ANTES de cada nó (índice i)
        used_at = [0]
        cost_at = [0.0]
        for j, e in enumerate(prev_edges):
            real = not e.synthetic
            used_at.append(1 if (used_at[-1] or real) else 0)
            owes_at.append(owes_real(prev_nodes[j + 1]) if real
                           else owes_synth(prev_nodes[j + 1]))
            cost_at.append(cost_at[-1] + e.weight_m)

        for i in range(len(prev_nodes) - 1):
            spur_node = prev_nodes[i]
            root_nodes = prev_nodes[:i + 1]

            # Banir as arestas que recriariam rotas já achadas com o mesmo prefixo.
            banned_edges: set = set()
            for (a_nodes, a_edges, _) in A:
                if a_nodes[:i + 1] == root_nodes and i < len(a_edges):
                    banned_edges.add(_edge_key(a_edges[i]))
            # Banir nós do trecho-raiz (exceto o spur) -> caminhos sem laço.
            banned_nodes = frozenset(root_nodes[:-1])

            start_state = (spur_node, owes_at[i], used_at[i])
            spur = _phase_shortest(graph, dest_id, need_used, start_state,
                                   cost_at[i], frozenset(banned_edges),
                                   banned_nodes, owes_real, owes_synth)
            if spur is None:
                continue
            s_nodes, s_edges, s_cost = spur
            full_nodes = root_nodes[:-1] + s_nodes
            full_edges = prev_edges[:i] + s_edges
            key = tuple(full_nodes)
            if key in seen:
                continue
            if len(set(full_nodes)) != len(full_nodes):
                continue                       # descarta candidato com laço
            if not any(tuple(bn) == key for (_, bn, _) in B):
                B.append((s_cost, full_nodes, full_edges))

        if not B:
            break
        B.sort(key=lambda t: t[0])
        chosen = None
        while B:                               # dedup robusto na seleção
            cost, nodes_b, edges_b = B.pop(0)
            if tuple(nodes_b) not in seen:
                chosen = (cost, nodes_b, edges_b)
                break
        if chosen is None:
            break
        cost, nodes_b, edges_b = chosen
        seen.add(tuple(nodes_b))
        A.append((nodes_b, edges_b, cost))

    out: list[DecodedRoute] = []
    for node_ids, edges, cost in A:
        r = DecodedRoute(node_ids, edges, cost, complete=True)
        r.fitness = cost
        out.append(r)
    return out