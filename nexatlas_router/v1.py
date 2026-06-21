"""Orquestração da V1 — Rota lateral VFR por corredores REA.

Saída:
  • Lista ordenada de pontos da rota.
  • Corredores REA utilizados, classificados [Obrigatório] / [Opcional].
  • Distância direta entre origem e destino.
  • Distância total da rota sugerida.
  • Motivo simples da escolha.

A antiga regra de "portão obrigatório" (string PORTÃO) e as penalidades de
corredor foram removidas. A entrada na malha REA agora se dá por qualquer nó
válido, e a obrigatoriedade é uma propriedade por-corredor (is_mandatory),
garantida topologicamente pela Trava de Continuidade.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .geo import m_to_nm, haversine_m
from .graphmodel import RouteGraph
from .gwo import GWOConfig, GWORouter
from .dijkstra import dijkstra, shortest_route


def _real_distance_m(graph: RouteGraph, route) -> float:
    """Distância REAL (geográfica) da rota.

    Os trechos sintéticos "DIRETO" carregam um peso de OTIMIZAÇÃO levemente
    inflado (preferência por corredor); aqui recomputamos a distância
    geográfica verdadeira para o relatório, sem a penalidade.
    """
    tot = 0.0
    for e in route.edges:
        if e.synthetic:
            tot += haversine_m(graph.nodes[e.source].pos, graph.nodes[e.target].pos)
        else:
            tot += e.weight_m
    return tot


def _route_points(graph: RouteGraph, route) -> list[dict]:
    pts = []
    for nid in route.node_ids:
        node = graph.nodes[nid]
        pts.append({
            "id": node.id, "name": node.name, "kind": node.kind,
            "lon": node.pos.lon, "lat": node.pos.lat, "chart": node.chart,
        })
    return pts


def _corridors_used(route) -> list[dict]:
    """Corredores REA reais percorridos, na ordem, com flag de obrigatoriedade.

    Um corredor é [Obrigatório] se QUALQUER aresta real usada nele tiver
    is_mandatory=True; caso contrário, [Opcional]. Trechos sintéticos "DIRETO"
    são ignorados (não são corredores REA).
    """
    order: list[str] = []
    mandatory: dict[str, bool] = {}
    for e in route.edges:
        if e.synthetic or not e.corridor or e.corridor == "DIRETO":
            continue
        if e.corridor not in mandatory:
            order.append(e.corridor)
            mandatory[e.corridor] = False
        if e.is_mandatory:
            mandatory[e.corridor] = True
    return [{"name": c, "is_mandatory": mandatory[c]} for c in order]


@dataclass
class V1RouteResult:
    points: list[dict]
    corridors_used: list[dict]          # [{name, is_mandatory}]
    direct_distance_nm: float
    total_distance_nm: float
    reason: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "points": self.points,
            "corridors_used": self.corridors_used,
            "direct_distance_nm": round(self.direct_distance_nm, 1),
            "total_distance_nm": round(self.total_distance_nm, 1),
            "reason": self.reason,
            "meta": self.meta,
        }


def plan_v1_route(graph: RouteGraph, origin_id: str, dest_id: str,
                  config: Optional[GWOConfig] = None) -> V1RouteResult:
    # Regra REA: se alguma ponta está em TMA REA, a rota é OBRIGADA a usar
    # >=1 corredor real (graph.requires_corridor, definido em add_synthetic_edges).
    require = bool(getattr(graph, "requires_corridor", False))

    # FASE 1 — MALHA PRIMEIRO. shortest_route é EXATO/DETERMINÍSTICO e codifica
    # a regra de fase das TMAs (voar os corredores de cada TMA antes de saltar).
    # O GWO roda só para popular ALTERNATIVAS — ele não conhece a regra de fase,
    # então NÃO pode sobrepor a rota exata quando uma ponta está em TMA.
    mesh = shortest_route(graph, origin_id, dest_id, require_real_edge=require)

    result = GWORouter(graph, origin_id, dest_id, config).run()
    gwo_route = result.best

    used_direct_fallback = False

    if require:
        # Uma ponta está em TMA: a rota com fase é a autoridade (ótima e correta).
        route = mesh
        route_source = "dijkstra-fase"
    else:
        # Nenhuma ponta em TMA: distância pura, direto permitido; GWO pode ajudar.
        done = [r for r in (gwo_route, mesh) if r is not None and r.complete]
        route = min(done, key=lambda r: r.distance_m) if done else None
        route_source = "dijkstra" if route is mesh else "gwo"

    # FASE 2 — FALLBACK DIRETO: a malha REA realmente não conecta com corredor
    # (mesmo com a válvula de ponte). Só então liberamos o atalho direto.
    if route is None:
        if graph.add_direct_fallback(origin_id, dest_id):
            used_direct_fallback = True
        mesh = shortest_route(graph, origin_id, dest_id, require_real_edge=False)
        gwo_route = GWORouter(graph, origin_id, dest_id, config).run().best
        done = [r for r in (gwo_route, mesh) if r is not None and r.complete]
        route = min(done, key=lambda r: r.distance_m) if done else None
        route_source = "dijkstra" if route is mesh else "gwo"

    if route is None or not route.complete:
        raise RuntimeError(
            "Sem rota completa mesmo com o fallback direto; "
            "verifique a montagem do grafo (origem/destino válidos?)."
        )

    points = _route_points(graph, route)
    corridors = _corridors_used(route)

    direct_nm = m_to_nm(graph.direct_distance_m(origin_id, dest_id))
    total_nm = m_to_nm(_real_distance_m(graph, route))   # distância REAL (sem penalidade)
    overhead = total_nm - direct_nm

    if corridors:
        names = ", ".join(
            f"{c['name']} [{'Obrigatório' if c['is_mandatory'] else 'Opcional'}]"
            for c in corridors
        )
        reason = (
            f"Rota usa o(s) corredor(es) REA {names}, com acréscimo de "
            f"{overhead:.1f} NM sobre a rota direta ({direct_nm:.1f} NM) — "
            f"menor distância total entre as alternativas disponíveis."
        )
    else:
        reason = (
            f"Nenhum corredor REA relevante no caminho; rota direta de "
            f"{direct_nm:.1f} NM autorizada."
        )

    # Alternativas: próximas melhores rotas distintas que o GWO arquivou.
    alternatives = []
    for alt in result.alternatives:
        alt_real_nm = m_to_nm(_real_distance_m(graph, alt))
        alternatives.append({
            "points": _route_points(graph, alt),
            "total_distance_nm": round(alt_real_nm, 1),
            "overhead_nm": round(alt_real_nm - direct_nm, 1),
            "corridors_used": _corridors_used(alt),
            "n_points": len(alt.node_ids),
        })

    return V1RouteResult(
        points=points,
        corridors_used=corridors,
        direct_distance_nm=direct_nm,
        total_distance_nm=total_nm,
        reason=reason,
        meta={
            "alternatives": alternatives,
            "iterations_run": result.iterations_run,
            "final_fitness_m": route.fitness,
            "used_direct_fallback": used_direct_fallback,
            "route_source": route_source,
            "fitness_history": result.history,
        },
    )