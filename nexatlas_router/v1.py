"""Orquestração da V1 — Rota lateral VFR com corredores visuais.

Produz exatamente a saída exigida pelo documento de escopo:
  • Lista ordenada de pontos da rota.
  • Indicação de corredores visuais utilizados.
  • Distância direta entre origem e destino.
  • Distância total da rota sugerida.
  • Indicação simples do motivo da escolha da rota.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .geo import m_to_nm
from .graphmodel import RouteGraph
from .gwo import GWOConfig, GWORouter


def _v1_rule_satisfied(corridors_used, dep_nodes, arr_nodes, route) -> bool:
    """A regra V1 foi cumprida?

    Regra: se havia corredor aplicável na saída (dep_nodes não vazio), a rota
    deve ter tocado ALGUM nó de corredor; idem para a chegada. Não importa
    QUAL corredor — o algoritmo escolhe o melhor. Se não havia corredor
    aplicável, a regra é trivialmente satisfeita (rota direta permitida).
    """
    used_nodes = {nid for e in route.edges if not e.synthetic
                  for nid in (e.source, e.target)}
    dep_ok = (not dep_nodes) or bool(dep_nodes & used_nodes)
    arr_ok = (not arr_nodes) or bool(arr_nodes & used_nodes)
    return dep_ok and arr_ok


def _is_gateway(name: str) -> bool:
    n = (name or "").upper()
    return "PORTÃO" in n or "PORTAO" in n


def _route_points(graph: RouteGraph, route) -> list[dict]:
    pts = []
    for nid in route.node_ids:
        node = graph.nodes[nid]
        pts.append({
            "id": node.id, "name": node.name, "kind": node.kind,
            "lon": node.pos.lon, "lat": node.pos.lat, "chart": node.chart,
        })
    return pts


def _gateways_of(graph: RouteGraph, route) -> dict:
    """Identifica o portão de ENTRADA (primeiro portão após a origem) e o de
    SAÍDA (último portão antes do destino) efetivamente usados pela rota."""
    gate_pts = [graph.nodes[nid] for nid in route.node_ids
                if _is_gateway(graph.nodes[nid].name)]
    entry = gate_pts[0].name if gate_pts else None
    exit_ = gate_pts[-1].name if len(gate_pts) >= 2 else None
    return {"entry_gateway": entry, "exit_gateway": exit_,
            "all_gateways": [g.name for g in gate_pts]}


@dataclass
class V1RouteResult:
    points: list[dict]                  # [{id, name, kind, lon, lat}]
    corridors_used: list[str]
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
    router = GWORouter(graph, origin_id, dest_id, config)
    result = router.run()
    route = result.best

    if route is None or not route.complete:
        raise RuntimeError(
            "GWO não encontrou rota completa — a aresta direta sintética "
            "deveria garantir factibilidade; verifique a montagem do grafo."
        )

    points = _route_points(graph, route)

    corridors = []
    for e in route.edges:
        if e.corridor and e.corridor not in corridors:
            corridors.append(e.corridor)

    direct_nm = m_to_nm(router.direct_m)
    total_nm = m_to_nm(route.distance_m)

    dep_required = bool(router.dep_corridor_nodes)
    arr_required = bool(router.arr_corridor_nodes)

    if corridors:
        overhead = total_nm - direct_nm
        partes = []
        if dep_required:
            partes.append("saída")
        if arr_required:
            partes.append("chegada")
        ctx = (f" Havia corredor visual aplicável na {' e na '.join(partes)}, "
               f"e a rota usa corredor conforme exigido pela regra VFR."
               if partes else "")
        reason = (
            f"Rota usa o(s) corredor(es) visual(is) {', '.join(corridors)}, "
            f"com acréscimo de {overhead:.1f} NM sobre a rota direta "
            f"({direct_nm:.1f} NM) — menor distância total entre as "
            f"alternativas de corredor disponíveis.{ctx}"
        )
    elif dep_required or arr_required:
        reason = (
            "ALERTA: existem corredores visuais aplicáveis, mas a rota gerada "
            "não os utilizou — verifique a convergência do otimizador ou os "
            "parâmetros de penalidade."
        )
    else:
        reason = (
            f"Nenhum corredor visual aplicável à saída ou à chegada; "
            f"rota direta de {direct_nm:.1f} NM autorizada pela regra V1."
        )

    # Corredores DISPONÍVEIS na região (têm nós no raio da origem/destino).
    # NÃO são individualmente obrigatórios — são as ALTERNATIVAS entre as
    # quais o algoritmo escolhe. A regra V1 exige usar ALGUM deles quando o
    # conjunto não é vazio, não todos. (dep_required/arr_required guardam o
    # "havia alternativa?"; corridors_used guarda "qual foi escolhido".)
    dep_available: list[str] = []
    arr_available: list[str] = []
    seen_dep: set[str] = set()
    seen_arr: set[str] = set()
    for edges in graph.adj.values():
        for e in edges:
            if e.synthetic or not e.corridor:
                continue
            if (e.source in router.dep_corridor_nodes or
                    e.target in router.dep_corridor_nodes):
                if e.corridor not in seen_dep:
                    seen_dep.add(e.corridor)
                    dep_available.append(e.corridor)
            if (e.source in router.arr_corridor_nodes or
                    e.target in router.arr_corridor_nodes):
                if e.corridor not in seen_arr:
                    seen_arr.add(e.corridor)
                    arr_available.append(e.corridor)

    # Portões de entrada/saída efetivamente usados pela MELHOR rota
    gw = _gateways_of(graph, route)

    # Alternativas: as próximas melhores rotas distintas que o GWO descartou
    alternatives = []
    for alt in result.alternatives:
        alt_corridors = []
        for e in alt.edges:
            if e.corridor and e.corridor not in alt_corridors:
                alt_corridors.append(e.corridor)
        alt_gw = _gateways_of(graph, alt)
        alternatives.append({
            "points": _route_points(graph, alt),
            "total_distance_nm": round(m_to_nm(alt.distance_m), 1),
            "overhead_nm": round(m_to_nm(alt.distance_m) - direct_nm, 1),
            "corridors_used": alt_corridors,
            "entry_gateway": alt_gw["entry_gateway"],
            "exit_gateway": alt_gw["exit_gateway"],
            "n_points": len(alt.node_ids),
        })

    return V1RouteResult(
        points=points,
        corridors_used=corridors,
        direct_distance_nm=direct_nm,
        total_distance_nm=total_nm,
        reason=reason,
        meta={
            "entry_gateway": gw["entry_gateway"],
            "exit_gateway": gw["exit_gateway"],
            "all_gateways": gw["all_gateways"],
            "alternatives": alternatives,
            "departure_corridor_required": dep_required,
            "arrival_corridor_required": arr_required,
            "departure_corridors_available": dep_available,
            "arrival_corridors_available": arr_available,
            "rule_satisfied": _v1_rule_satisfied(
                corridors, router.dep_corridor_nodes, router.arr_corridor_nodes,
                route),
            "iterations_run": result.iterations_run,
            "final_fitness_m": route.fitness,
            "fitness_history": result.history,
        },
    )