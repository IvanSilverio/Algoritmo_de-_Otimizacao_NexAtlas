"""Demo sem banco: cluster sintético no estilo 'REA São Paulo'.

Gera um subgrafo direcionado com ~60 waypoints e ~150 conexões na região de
SP, dois aeródromos (origem/destino), roda o GWO e valida contra o Dijkstra
em múltiplas seeds.

Uso:
    python -m nexatlas_router.demo
"""
from __future__ import annotations

import json
import random

from .geo import LonLat, haversine_m
from .graphmodel import Edge, Node, RouteGraph
from .gwo import GWOConfig, GWORouter
from .dijkstra import dijkstra
from .v1 import plan_v1_route


def build_synthetic_chart(seed: int = 7) -> tuple[RouteGraph, str, str]:
    """Cluster sintético: grade irregular de waypoints entre SBMT e SBJD."""
    rnd = random.Random(seed)
    g = RouteGraph()

    origin = Node("ADHP:SBMT", "Campo de Marte", LonLat(-46.6377, -23.5092), "aerodrome")
    dest = Node("ADHP:SBJD", "Jundiaí", LonLat(-46.9436, -23.1817), "aerodrome")
    g.add_node(origin)
    g.add_node(dest)

    # Waypoints espalhados na caixa entre os dois aeródromos (com folga)
    n_wp = 60
    wps: list[Node] = []
    for i in range(n_wp):
        lon = rnd.uniform(-47.10, -46.45)
        lat = rnd.uniform(-23.60, -23.05)
        node = Node(f"WP{i:03d}", f"REF {i:03d}", LonLat(lon, lat),
                    "waypoint", chart="REA Sintética SP")
        wps.append(node)
        g.add_node(node)

    # Conexões direcionadas entre waypoints próximos (corredores)
    corridor_names = ["ALFA", "BRAVO", "CHARLIE", "DELTA", "ECO"]
    n_edges = 0
    for a in wps:
        # liga aos 4 vizinhos mais próximos, direção sorteada
        others = sorted(
            (b for b in wps if b.id != a.id),
            key=lambda b: haversine_m(a.pos, b.pos),
        )[:4]
        for b in others:
            w = haversine_m(a.pos, b.pos)
            name = rnd.choice(corridor_names)
            g.add_edge(Edge(a.id, b.id, w, corridor=name,
                            connection_id=f"C{n_edges:04d}",
                            lower_limit=1000, higher_limit=4500))
            n_edges += 1
            if rnd.random() < 0.5:  # ~metade das conexões é bidirecional
                g.add_edge(Edge(b.id, a.id, w, corridor=name,
                                connection_id=f"C{n_edges:04d}",
                                lower_limit=1000, higher_limit=4500))
                n_edges += 1

    g.add_synthetic_edges(origin.id, dest.id, max_link_nm=12.0)
    return g, origin.id, dest.id


def main() -> None:
    g, o, d = build_synthetic_chart()
    print(f"Subgrafo: {g.n} nós, {sum(len(v) for v in g.adj.values())} arestas\n")

    oracle = dijkstra(g, o, d)
    print(f"[Dijkstra/oráculo]  distância ótima: {oracle.distance_m/1852:.2f} NM "
          f"({len(oracle.node_ids)} pontos)")

    print("\n=== Cenário 1: validação do otimizador (regra de corredor OFF) ===")
    hits = 0
    seeds = range(10)
    for s in seeds:
        cfg = GWOConfig(n_wolves=30, n_iterations=200, max_hops=40, seed=s,
                        enforce_corridor_rule=False)
        res = GWORouter(g, o, d, cfg).run()
        gap = (res.best.distance_m - oracle.distance_m) / oracle.distance_m * 100
        ok = gap <= 1.0
        hits += ok
        print(f"[GWO seed={s}]  {res.best.distance_m/1852:.2f} NM  "
              f"gap={gap:+.2f}%  iter={res.iterations_run}  "
              f"{'OK' if ok else 'ACIMA DO LIMITE'}")
    print(f"Taxa de acerto (gap <= 1%): {hits}/{len(list(seeds))}")

    print("\n=== Cenário 2: regra do piloto — corredor existente é OBRIGATÓRIO ===")
    ok2 = 0
    for s in seeds:
        cfg = GWOConfig(n_wolves=30, n_iterations=200, max_hops=40, seed=s)
        router = GWORouter(g, o, d, cfg)
        res = router.run()
        used = {nid for e in res.best.edges if not e.synthetic
                for nid in (e.source, e.target)}
        dep_ok = (not router.dep_corridor_nodes
                  or bool(router.dep_corridor_nodes & used))
        arr_ok = (not router.arr_corridor_nodes
                  or bool(router.arr_corridor_nodes & used))
        valid = res.best.complete and dep_ok and arr_ok
        ok2 += valid
        print(f"[GWO seed={s}]  {res.best.distance_m/1852:.2f} NM  "
              f"pontos={len(res.best.node_ids)}  "
              f"saída={'OK' if dep_ok else 'X'}  "
              f"chegada={'OK' if arr_ok else 'X'}")
    print(f"Rotas cumprindo a obrigatoriedade: {ok2}/10")

    print("\n--- Saída V1 (seed=0, regra ativa) ---")
    out = plan_v1_route(g, o, d, GWOConfig(seed=0, n_iterations=200, max_hops=40))
    payload = out.to_dict()
    payload["meta"].pop("fitness_history")
    print(json.dumps(payload, indent=2, ensure_ascii=False))



if __name__ == "__main__":
    main()
