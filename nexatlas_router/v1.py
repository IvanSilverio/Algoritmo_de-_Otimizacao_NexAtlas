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
from .dijkstra import shortest_route, k_shortest_routes


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


def _route_legs(graph: RouteGraph, route) -> list[dict]:
    """Rota trecho a trecho, no formato dos casos de referência.

    Para cada aresta da rota (alinhada a node_ids[i] -> node_ids[i+1]), devolve
    de onde vem, para onde vai e por qual corredor REA passa — ou "DIRETO"
    quando o trecho é sintético (entrada/saída da malha ou salto entre TMAs).
    """
    legs: list[dict] = []
    for e in route.edges:
        is_direto = e.synthetic or not e.corridor or e.corridor == "DIRETO"
        legs.append({
            "from": graph.nodes[e.source].name,
            "to": graph.nodes[e.target].name,
            "corridor": "DIRETO" if is_direto else e.corridor,
            "is_mandatory": (not is_direto) and e.is_mandatory,
        })
    return legs


@dataclass
class V1RouteResult:
    points: list[dict]
    corridors_used: list[dict]          # [{name, is_mandatory}]
    legs: list[dict]                    # [{from, to, corridor, is_mandatory}]
    direct_distance_nm: float
    total_distance_nm: float
    reason: str
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "points": self.points,
            "corridors_used": self.corridors_used,
            "legs": self.legs,
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

    # AUTORIDADE: shortest_route (Dijkstra com ESTADO DE FASE) — exato e
    # determinístico, codifica a regra das TMAs. As alternativas também vêm do
    # Dijkstra (k-shortest/Yen, mais abaixo), não do GWO: assim TODAS as rotas
    # exibidas respeitam a mesma regra de validade. O GWO segue no código,
    # reservado ao V2/V3 multiobjetivo (ele nunca superava o Dijkstra em distância).
    used_direct_fallback = False
    mesh = shortest_route(graph, origin_id, dest_id, require_real_edge=require)

    if mesh is not None and mesh.complete:
        route = mesh
        eff_require = require
        route_source = "dijkstra-fase" if require else "dijkstra"
    else:
        # FALLBACK DIRETO: a malha REA realmente não conecta com corredor
        # (mesmo com a válvula de ponte/escala longa). Só então liberamos o direto.
        if graph.add_direct_fallback(origin_id, dest_id):
            used_direct_fallback = True
        route = shortest_route(graph, origin_id, dest_id, require_real_edge=False)
        eff_require = False
        route_source = "dijkstra"

    if route is None or not route.complete:
        raise RuntimeError(
            "Sem rota completa mesmo com o fallback direto; "
            "verifique a montagem do grafo (origem/destino válidos?)."
        )

    points = _route_points(graph, route)
    corridors = _corridors_used(route)
    legs = _route_legs(graph, route)

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

    # Alternativas: as próximas melhores rotas DISTINTAS e VÁLIDAS, geradas pelo
    # k-shortest (algoritmo de Yen) sobre o mesmo grafo com fase — não pelo GWO.
    # Todas respeitam owes/used. Pego k=5 e descarto a 1ª (== rota principal).
    k_routes = k_shortest_routes(graph, origin_id, dest_id, k=5,
                                 require_real_edge=eff_require)
    main_seq = list(route.node_ids)
    alternatives = []
    for alt in k_routes:
        if list(alt.node_ids) == main_seq:
            continue
        alt_real_nm = m_to_nm(_real_distance_m(graph, alt))
        alternatives.append({
            "points": _route_points(graph, alt),
            "total_distance_nm": round(alt_real_nm, 1),
            "overhead_nm": round(alt_real_nm - direct_nm, 1),
            "corridors_used": _corridors_used(alt),
            "n_points": len(alt.node_ids),
        })
        if len(alternatives) >= 4:
            break

    return V1RouteResult(
        points=points,
        corridors_used=corridors,
        legs=legs,
        direct_distance_nm=direct_nm,
        total_distance_nm=total_nm,
        reason=reason,
        meta={
            "alternatives": alternatives,
            "n_alternatives": len(alternatives),
            "final_fitness_m": route.fitness,
            "used_direct_fallback": used_direct_fallback,
            "route_source": route_source,
        },
    )