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
