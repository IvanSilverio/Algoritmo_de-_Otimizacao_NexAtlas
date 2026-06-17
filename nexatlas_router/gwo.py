"""Grey Wolf Optimizer com codificação por prioridades (random keys).

Mundo contínuo (onde o GWO original opera, sem nenhuma modificação):
    P ∈ R^{n_wolves × N},  P[i][j] ∈ [0, 1]
    P[i][j] = prioridade que o lobo i atribui ao nó de índice j.

Mundo discreto (onde a rota existe):
    decode() caminha pelo digrafo a partir da origem escolhendo sempre o
    sucessor de maior prioridade. Toda rota decodificada é topologicamente
    válida por construção.

Fitness (minimização):
    f = distância_total
        + [incompleta] * (M + λ * distância_restante_até_o_destino)
        + μ * violações_de_corredor_obrigatório
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .geo import haversine_m
from .graphmodel import Edge, RouteGraph


@dataclass
class DecodedRoute:
    node_ids: list[str]
    edges: list[Edge]
    distance_m: float
    complete: bool
    fitness: float = float("inf")


@dataclass
class GWOConfig:
    n_wolves: int = 30
    n_iterations: int = 150
    max_hops: int = 80                     # AUMENTADO: de 30 para 80 para suportar rotas longas com múltiplos TMAs
    seed: Optional[int] = None
    # Penalidades (calibradas como múltiplos da distância direta)
    incomplete_base_factor: float = 10.0   # M = fator * distância direta
    incomplete_dist_factor: float = 2.0    # λ
    mandatory_factor: float = 20.0         # AUMENTADO: Penalidade extrema para evitar o bypass (fallback) do grafo
    # Regra V1 (piloto orientador): corredor existente => passagem obrigatória
    enforce_corridor_rule: bool = True
    corridor_radius_nm: float = 30.0       # raio de "corredor aplicável"
    # Critério de parada antecipada: iterações sem melhora do alfa
    patience: Optional[int] = 50


@dataclass
class GWOResult:
    best: DecodedRoute
    alternatives: list[DecodedRoute] = field(default_factory=list)  # próximas melhores, distintas
    history: list[float] = field(default_factory=list)   # fitness do alfa/iteração
    iterations_run: int = 0


class GWORouter:
    def __init__(self, graph: RouteGraph, origin_id: str, dest_id: str,
                 config: Optional[GWOConfig] = None) -> None:
        self.g = graph
        self.origin = origin_id
        self.dest = dest_id
        self.cfg = config or GWOConfig()
        self.rng = np.random.default_rng(self.cfg.seed)

        self.direct_m = graph.direct_distance_m(origin_id, dest_id)
        self.M = self.cfg.incomplete_base_factor * self.direct_m
        self.mu = self.cfg.mandatory_factor * self.direct_m

        # Regra V1: corredor aplicável => uso obrigatório.
        if self.cfg.enforce_corridor_rule:
            r = self.cfg.corridor_radius_nm
            self.dep_corridor_nodes = graph.corridor_nodes_near(origin_id, r)
            self.arr_corridor_nodes = graph.corridor_nodes_near(dest_id, r)
        else:
            self.dep_corridor_nodes = set()
            self.arr_corridor_nodes = set()

    # ------------------------------------------------------------- decoding
    def decode(self, priorities: np.ndarray) -> DecodedRoute:
        path = [self.origin]
        edges: list[Edge] = []
        visited = {self.origin}
        current = self.origin
        dist = 0.0

        for _ in range(self.cfg.max_hops):
            if current == self.dest:
                return DecodedRoute(path, edges, dist, complete=True)

            candidates = [e for e in self.g.successors(current)
                          if e.target not in visited]
            if not candidates:
                return DecodedRoute(path, edges, dist, complete=False)

            best = max(candidates,
                       key=lambda e: priorities[self.g.index[e.target]])
            edges.append(best)
            dist += best.weight_m
            current = best.target
            path.append(current)
            visited.add(current)

        complete = current == self.dest
        return DecodedRoute(path, edges, dist, complete=complete)

    # -------------------------------------------------------------- fitness
    def fitness(self, route: DecodedRoute) -> float:
        f = route.distance_m

        if not route.complete:
            last = self.g.nodes[route.node_ids[-1]]
            dest = self.g.nodes[self.dest]
            remaining = haversine_m(last.pos, dest.pos)
            f += self.M + self.cfg.incomplete_dist_factor * remaining

        if route.complete:
            # Nós de corredor REAL efetivamente percorridos pela rota
            used = {nid for e in route.edges if not e.synthetic
                    for nid in (e.source, e.target)}

            # Regra V1: existe corredor na saída/chegada? então é obrigatório usá-lo.
            if self.dep_corridor_nodes and not (self.dep_corridor_nodes & used):
                f += self.mu
            
            if self.arr_corridor_nodes and not (self.arr_corridor_nodes & used):
                f += self.mu

        return f

    # ----------------------------------------------------------------- core
    def run(self) -> GWOResult:
        cfg = self.cfg
        N = self.g.n

        P = self.rng.random((cfg.n_wolves, N))

        history: list[float] = []
        alpha_p = beta_p = delta_p = None
        alpha_f = beta_f = delta_f = float("inf")
        best_route: Optional[DecodedRoute] = None
        stale = 0

        best_by_route: dict[tuple, DecodedRoute] = {}

        def _archive(route: DecodedRoute, f: float) -> None:
            if not route.complete:
                return
            key = tuple(route.node_ids)
            prev = best_by_route.get(key)
            if prev is None or f < prev.fitness:
                route.fitness = f
                best_by_route[key] = route

        for it in range(cfg.n_iterations):
            for i in range(cfg.n_wolves):
                route = self.decode(P[i])
                f = self.fitness(route)
                _archive(route, f)
                if f < alpha_f:
                    delta_f, delta_p = beta_f, beta_p
                    beta_f, beta_p = alpha_f, alpha_p
                    alpha_f, alpha_p = f, P[i].copy()
                    route.fitness = f
                    best_route = route
                    stale = -1  
                elif f < beta_f:
                    delta_f, delta_p = beta_f, beta_p
                    beta_f, beta_p = f, P[i].copy()
                elif f < delta_f:
                    delta_f, delta_p = f, P[i].copy()

            history.append(alpha_f)
            stale += 1
            if cfg.patience is not None and stale >= cfg.patience:
                return self._finish(best_route, best_by_route, history, it + 1)

            if beta_p is None:
                beta_p, beta_f = alpha_p, alpha_f
            if delta_p is None:
                delta_p, delta_f = beta_p, beta_f

            a = 2.0 - 2.0 * it / cfg.n_iterations 

            X1 = self._hunt_step(P, alpha_p, a)
            X2 = self._hunt_step(P, beta_p, a)
            X3 = self._hunt_step(P, delta_p, a)
            P = (X1 + X2 + X3) / 3.0
            np.clip(P, 0.0, 1.0, out=P)

        return self._finish(best_route, best_by_route, history,
                            cfg.n_iterations)

    def _finish(self, best_route, best_by_route, history, iters) -> GWOResult:
        ordered = sorted(best_by_route.values(), key=lambda r: r.fitness)
        alternatives: list[DecodedRoute] = []
        if best_route is not None:
            best_key = tuple(best_route.node_ids)
            for r in ordered:
                if tuple(r.node_ids) != best_key:
                    alternatives.append(r)
        else:
            best_route = ordered[0] if ordered else None
            alternatives = ordered[1:]
        return GWOResult(best=best_route, alternatives=alternatives[:4],
                         history=history, iterations_run=iters)

    def _hunt_step(self, P: np.ndarray, leader: np.ndarray, a: float) -> np.ndarray:
        r1 = self.rng.random(P.shape)
        r2 = self.rng.random(P.shape)
        A = 2.0 * a * r1 - a          
        C = 2.0 * r2
        D = np.abs(C * leader - P)
        return leader - A * D