"""Dijkstra exato — oráculo de testes para o GWO.

Não faz parte do produto final da V1; serve para os testes automatizados
verificarem que o GWO converge para o ótimo (ou perto dele) no mesmo
subgrafo. Mantém a mesma assinatura de saída (DecodedRoute).
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


def shortest_route(graph: RouteGraph, origin_id: str, dest_id: str,
                   require_real_edge: bool = False) -> Optional[DecodedRoute]:
    """Caminho mínimo EXATO com ESTADO DE FASE (regra REA das TMAs).

    A regra operacional dos casos de referência: ao entrar numa TMA com REA, a
    aeronave precisa VOAR os corredores dela até um terminal (ponto de onde não
    há corredor que avance rumo ao destino); só então pode pegar um trecho
    DIRETO (entrada/saída/salto entre TMAs). Isso é modelado como rótulo de
    estado no Dijkstra (label-setting), continuando exato e determinístico:

        estado = (nó, owes, used)
          owes : 1 se a aeronave está "dentro" de um corredor e DEVE continuar
                 por corredor real (não pode pegar DIRETO agora).
          used : 1 se já voou >=1 corredor real (exigido quando uma ponta está
                 em TMA REA).

    Transições (a OBRIGAÇÃO depende de COMO se chega ao nó):
      • aresta REAL (corredor): sempre permitida. Ao chegar em v por corredor,
        owes=1 se v ainda tem corredor real que AVANÇA rumo ao destino (a cadeia
        continua); senão owes=0 — v é o fim natural da cadeia na direção do voo,
        e aí PODE pegar DIRETO (ex.: sair no DUTRA rumo a SBMT).
      • aresta SINTÉTICA (DIRETO/ponte/saída): só permitida se owes==0. Ao chegar
        num nó por trecho sintético (entrada na origem ou pouso de uma ponte),
        owes=1 se v tem QUALQUER corredor real de saída — ou seja, ENTRAR numa
        TMA obriga a voar o corredor dela, mesmo que o primeiro corredor não
        "avance" geometricamente (corredores REA serpenteiam). Isso fecha o buraco
        de "pousar num portal e sair reto" (TARUMÃ/ATUBA) e o de "saltar direto no
        portão de saída" (DUTRA), porque pousar lá por ponte passa a exigir voar.

    Objetivo: chegar a (dest, owes=0, used>=need). Impede tanto "tangenciar um
    waypoint e sair reto" quanto "entrar/saltar numa TMA e pular o corredor".
    """
    INF = float("inf")
    nodes = graph.nodes
    dpos = nodes[dest_id].pos
    d_dest = {nid: _haversine(nodes[nid].pos, dpos) for nid in nodes}

    def owes_real(v: str) -> int:
        # Chegou por CORREDOR: deve 1 se há corredor de saída que AVANÇA (cadeia
        # continua na direção do destino); senão 0 (terminal natural -> pode sair).
        for e in graph.adj.get(v, []):
            if (not e.synthetic) and d_dest[e.target] < d_dest[v] - 1.0:
                return 1
        return 0

    def owes_synth(v: str) -> int:
        # Chegou por trecho SINTÉTICO (entrou/saltou numa TMA): deve 1 se v tem
        # QUALQUER corredor real de saída -> tem que voar o corredor antes de sair.
        for e in graph.adj.get(v, []):
            if not e.synthetic:
                return 1
        return 0

    need_used = 1 if require_real_edge else 0
    start = (origin_id, 0, 0)
    dist: dict[tuple, float] = {start: 0.0}
    prev: dict[tuple, tuple[tuple, Edge]] = {}
    heap: list[tuple[float, str, int, int]] = [(0.0, origin_id, 0, 0)]
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

    node_ids = [dest_id]
    edges: list[Edge] = []
    cur = goal
    while cur != start:
        parent_state, edge = prev[cur]
        edges.append(edge)
        node_ids.append(parent_state[0])
        cur = parent_state
    node_ids.reverse()
    edges.reverse()

    route = DecodedRoute(node_ids, edges, dist[goal], complete=True)
    route.fitness = dist[goal]
    return route