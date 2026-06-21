#!/usr/bin/env python3
"""Diagnóstico de rota REA — descobre POR QUE uma rota cai no fallback direto.

Conecta ao banco published, monta o subgrafo de O -> D (igual ao motor) e
relata, com números reais:
  • diagnóstico das arestas sintéticas (entradas, saídas, travas, pontes);
  • nó REA mais próximo da origem e do destino (com distância);
  • quantos corredores são is_mandatory;
  • conectividade da MALHA por Dijkstra (sem aresta direta): conecta? quantos
    hops, que corredores; se não conecta, ONDE quebra (alcance da origem x
    nós que alcançam o destino);
  • pares de nós mais próximos entre cartas distintas (gap inter-TMA real).

Uso:
    source .env.sh
    python diagnose_route.py SBBH SBMT
"""
from __future__ import annotations

import os
import sys
from collections import deque

import psycopg2

from nexatlas_router.db import PostgisLoader
from nexatlas_router.dijkstra import dijkstra
from nexatlas_router.geo import haversine_m, m_to_nm


def get_conn():
    conn = psycopg2.connect(
        host=os.environ.get("NEXATLAS_DB_HOST", "jetstream.nexatlas.com"),
        port=os.environ.get("NEXATLAS_DB_PORT", "5433"),
        dbname=os.environ.get("NEXATLAS_DB_NAME", "jetstream"),
        user=os.environ.get("NEXATLAS_DB_USER", "ivansilverio"),
        password=os.environ["NEXATLAS_DB_PASSWORD"],
    )
    with conn.cursor() as cur:
        cur.execute("SET search_path TO published, public;")
    conn.commit()
    return conn


def _reachable_forward(graph, start) -> set:
    seen = {start}
    q = deque([start])
    while q:
        u = q.popleft()
        for e in graph.adj.get(u, []):
            if e.target not in seen:
                seen.add(e.target)
                q.append(e.target)
    return seen


def _reachable_backward(graph, target) -> set:
    rev: dict[str, list[str]] = {}
    for u, edges in graph.adj.items():
        for e in edges:
            rev.setdefault(e.target, []).append(e.source)
    seen = {target}
    q = deque([target])
    while q:
        u = q.popleft()
        for p in rev.get(u, []):
            if p not in seen:
                seen.add(p)
                q.append(p)
    return seen


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("uso: python diagnose_route.py ORIGEM DESTINO")
    o_icao, d_icao = sys.argv[1].upper(), sys.argv[2].upper()

    conn = get_conn()
    loader = PostgisLoader(conn)
    graph, meta = loader.build_subgraph(o_icao, d_icao,
                                        chart_radius_nm=60.0, link_radius_nm=30.0)
    oid, did = meta["origin_id"], meta["dest_id"]

    print(f"\n=== DIAGNÓSTICO {o_icao} -> {d_icao} ===")
    print(f"Cartas: {meta['charts']}")
    print(f"Nós: {graph.n} | Diagnóstico sintético: {meta['synthetic_diagnostics']}")

    real_edges = [e for es in graph.adj.values() for e in es if not e.synthetic]
    n_mand = sum(1 for e in real_edges if e.is_mandatory)
    print(f"\nArestas de corredor REAIS: {len(real_edges)} "
          f"({n_mand} obrigatórias, {len(real_edges)-n_mand} opcionais)")

    # nó REA mais próximo da origem e do destino
    waypoints = [(nid, n) for nid, n in graph.nodes.items() if n.kind == "waypoint"]
    if waypoints:
        o_pos, d_pos = graph.nodes[oid].pos, graph.nodes[did].pos
        near_o = min(waypoints, key=lambda kv: haversine_m(o_pos, kv[1].pos))
        near_d = min(waypoints, key=lambda kv: haversine_m(d_pos, kv[1].pos))
        print(f"\nNó REA mais próximo da ORIGEM:  {near_o[1].name} "
              f"({m_to_nm(haversine_m(o_pos, near_o[1].pos)):.1f} NM, "
              f"carta {near_o[1].chart})")
        print(f"Nó REA mais próximo do DESTINO: {near_d[1].name} "
              f"({m_to_nm(haversine_m(d_pos, near_d[1].pos)):.1f} NM, "
              f"carta {near_d[1].chart})")
        print(f"  (raio de link atual = 30 NM; se acima disso, aumente --link-radius)")

    # conectividade pura da MALHA (sem aresta direta — build não cria mais)
    mesh = dijkstra(graph, oid, did)
    print("\n--- Conectividade da MALHA (Dijkstra, sem atalho direto) ---")
    if mesh is not None and mesh.complete:
        corridors = []
        for e in mesh.edges:
            if not e.synthetic and e.corridor and e.corridor not in corridors:
                corridors.append(e.corridor)
        synth = sum(1 for e in mesh.edges if e.synthetic)
        print(f"CONECTA ✓  hops={len(mesh.node_ids)-1}  "
              f"dist={m_to_nm(mesh.distance_m):.1f} NM  "
              f"(arestas sintéticas no caminho: {synth})")
        print(f"Corredores: {corridors if corridors else '(nenhum — só sintéticas!)'}")
        print("Rota:", " -> ".join(graph.nodes[n].name for n in mesh.node_ids))
    else:
        print("NÃO CONECTA ✗  -> isolando o lado que quebra:")
        fwd = _reachable_forward(graph, oid)
        bwd = _reachable_backward(graph, did)
        wp_fwd = sum(1 for nid, n in waypoints if nid in fwd)
        wp_bwd = sum(1 for nid, n in waypoints if nid in bwd)
        print(f"  A origem alcança {len(fwd)} nós no total "
              f"({wp_fwd} waypoints REA).")
        print(f"  {wp_bwd} waypoints REA alcançam o destino.")
        if did in fwd:
            print("  (estranho: destino alcançável — rode de novo)")
        elif wp_fwd == 0:
            print("  >> A ORIGEM não entra na malha (nenhuma entrada). "
                  "Aumente --link-radius ou verifique geom da origem.")
        elif wp_bwd == 0:
            print("  >> NADA alcança o DESTINO. Saídas bloqueadas pela Trava "
                  "(veja exits_locked) ou destino fora do raio de link.")
        else:
            print("  >> Origem e destino tocam a malha, mas os dois lados não "
                  "se ligam: falta PONTE inter-TMA. Aumente --inter-tma ou "
                  "bridges_per_chart_pair.")

    # gap real entre cartas
    by_chart: dict[str, list] = {}
    for nid, n in waypoints:
        by_chart.setdefault(n.chart, []).append(n)
    charts = list(by_chart)
    if len(charts) > 1:
        print("\n--- Gap mínimo entre cartas (salto inter-TMA real) ---")
        for i in range(len(charts)):
            for j in range(i + 1, len(charts)):
                best = min(
                    (haversine_m(a.pos, b.pos)
                     for a in by_chart[charts[i]] for b in by_chart[charts[j]]),
                    default=None)
                if best is not None:
                    print(f"  {charts[i]}  <->  {charts[j]}: "
                          f"{m_to_nm(best):.1f} NM (cap inter_tma = 300 NM)")

    conn.close()
    print()


if __name__ == "__main__":
    main()