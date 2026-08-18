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

from dataclasses import dataclass, field, replace
from typing import Any, Optional

from .geo import m_to_nm, haversine_m, initial_bearing, progresso_nm as _progresso_nm
from .graphmodel import Edge, RouteGraph, DEDUP_DEST_RADIUS_M, DecodedRoute
from .dijkstra import shortest_route, k_shortest_routes

# TAREFA_coerencia_geometrica.md (II)/(II.b): limiares de coerência geométrica
# e fator relativo pra preferir a rota direta. Pesos expostos pra calibrar
# depois com o Vinícius/Cristiano.
RETROCESSO_LIMIAR_NM = 3.0       # retrocesso (afastamento do destino) tolerado por trecho
CURVA_LIMIAR_DEG = 120.0         # mudança de rumo tolerada — mesmo limiar pra rota principal E alternativas
                                 # (ajuste do Ivan 17/08: um limiar frouxo só pra alternativas deixava
                                 # passar desvios pouco viáveis, ex. caso 020 via Ponta da Fruta — 138°)
# Sugestão inicial da tarefa era 1.3x; ajuste do Ivan 17/08: 1.7x é a "aposta
# otimista" a favor do corredor (mais permissivo que 1.3x, mais rígido que os
# 2.0x que eu tinha calibrado). Nessa faixa, os casos 014 (1.34x) e 020 (1.87x)
# ficam em lados OPOSTOS do limiar — o Ivan confirmou que isso é o esperado:
# ambos são casos de proximidade em que o voo direto TAMBÉM é permitido, não
# só a cadeia (ver _mesma_malha, que garante a direta apareça como alternativa
# quando a malha vence por estar tudo na mesma carta).
FATOR_RELATIVO_DIRETO = 1.7


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


def _remove_duplicidade_destino(graph: RouteGraph, route: DecodedRoute,
                                dest_id: str) -> DecodedRoute:
    """Funde o penúltimo ponto no destino quando ele É o destino na prática:
    waypoint REA com o MESMO NOME do aeródromo, a poucos metros dele (casos
    018/020 do gabarito). Preserva o corredor/obrigatoriedade da aresta
    anterior, só reapontando o alvo pro destino."""
    if len(route.node_ids) < 3:
        return route
    dest = graph.nodes[dest_id]
    penult = graph.nodes[route.node_ids[-2]]
    if penult.kind != "waypoint":
        return route
    if penult.name.strip().upper() != dest.name.strip().upper():
        return route
    if haversine_m(penult.pos, dest.pos) > DEDUP_DEST_RADIUS_M:
        return route
    new_node_ids = route.node_ids[:-2] + [dest_id]
    new_edges = route.edges[:-2] + [replace(route.edges[-2], target=dest_id)]
    new_distance = route.distance_m - route.edges[-1].weight_m
    return DecodedRoute(new_node_ids, new_edges, new_distance, route.complete, new_distance)


def _turn_deg(bearing_in: float, bearing_out: float) -> float:
    """Mudança de rumo entre dois trechos consecutivos, em graus [0, 180]."""
    return abs((bearing_out - bearing_in + 540) % 360 - 180)


def _eventos_incoerentes(graph: RouteGraph, route: DecodedRoute, dest_pos,
                         retro_limiar_nm: float = RETROCESSO_LIMIAR_NM,
                         curva_limiar_deg: float = CURVA_LIMIAR_DEG) -> list:
    """Eventos de incoerência da rota: ('retrocesso', i) — a perna i se afasta
    do destino além do limiar — ou ('curva', i) — a virada entre a perna i e
    i+1 passa do limiar. Base de _rota_incoerente (bool) e do cruzamento
    portão×coerência (TAREFA_portoes.md), que precisa saber SE a violação
    está colada numa perna de entrada/saída protegida por portão obrigatório."""
    eventos: list = []
    prev_bearing = None
    for i in range(len(route.node_ids) - 1):
        a = graph.nodes[route.node_ids[i]].pos
        b = graph.nodes[route.node_ids[i + 1]].pos
        if _progresso_nm(a, b, dest_pos) < -retro_limiar_nm:
            eventos.append(("retrocesso", i))
        bearing = initial_bearing(a, b)
        if prev_bearing is not None and _turn_deg(prev_bearing, bearing) > curva_limiar_deg:
            eventos.append(("curva", i - 1))
        prev_bearing = bearing
    return eventos


def _evento_toca_perna(tipo: str, i: int, perna: int) -> bool:
    """O evento (retrocesso na perna i, ou curva entre as pernas i e i+1)
    envolve a perna `perna`?"""
    return i == perna or (tipo == "curva" and i + 1 == perna)


def _rota_incoerente(graph: RouteGraph, route: DecodedRoute, dest_pos,
                     retro_limiar_nm: float = RETROCESSO_LIMIAR_NM,
                     curva_limiar_deg: float = CURVA_LIMIAR_DEG,
                     pernas_protegidas: frozenset = frozenset()) -> bool:
    """TAREFA_coerencia_geometrica.md (II): a rota tem algum trecho com
    retrocesso além do limiar, ou uma curva acentuada entre dois trechos
    consecutivos além do limiar?

    Checa a rota já decidida pelo Dijkstra (não um custo por aresta dentro
    dele): a curva depende do trecho ANTERIOR, o que exigiria expandir o
    estado de fase (nó, owes, used) para incluir a proa de chegada — mudança
    arriscada no motor autoritativo (dijkstra.py). Como a decisão final é
    sempre binária ("mantém a malha ou troca pela direta", nunca escolher um
    meio-termo entre waypoints candidatos), avaliar a rota inteira já
    escolhida é suficiente e não toca no motor de fase.

    `pernas_protegidas` (TAREFA_portoes.md): índices de perna (0=entrada,
    len(edges)-1=saída) que usam um portão OBRIGATÓRIO do documento — o
    portão PREVALECE sobre a coerência (autoridade aeronáutica > heurística),
    então uma violação restrita a essas pernas é IGNORADA aqui (nunca derruba
    a rota pra direta por causa disso). A colisão ainda é sinalizada à parte,
    ver _colisao_portao."""
    eventos = _eventos_incoerentes(graph, route, dest_pos, retro_limiar_nm, curva_limiar_deg)
    return any(not any(_evento_toca_perna(tipo, i, p) for p in pernas_protegidas)
              for tipo, i in eventos)


def _pernas_de_portao(route: DecodedRoute, origin_gate_ids: Optional[set],
                      dest_gate_ids: Optional[set]) -> frozenset:
    """Índices de perna (0=entrada, len(edges)-1=saída) que usam um portão
    obrigatório do documento NESTA rota (TAREFA_portoes.md) — a entrada usa
    portão se o 2º nó da rota está entre os pontos obrigatórios de partida da
    origem; a saída, espelhado, se o penúltimo nó está entre os de destino."""
    n = len(route.edges)
    pernas = set()
    if origin_gate_ids and n >= 1 and route.node_ids[1] in origin_gate_ids:
        pernas.add(0)
    if dest_gate_ids and n >= 1 and route.node_ids[-2] in dest_gate_ids:
        pernas.add(n - 1)
    return frozenset(pernas)


def _colisao_portao(graph: RouteGraph, route: DecodedRoute, dest_pos,
                    pernas_portao: frozenset) -> Optional[dict]:
    """TAREFA_portoes.md: sinaliza quando a perna de entrada e/ou saída por um
    portão OBRIGATÓRIO do documento colide com a coerência (retrocesso/curva
    acima do limiar ali). O portão prevalece — a rota é mantida como está —
    mas a colisão é reportada pra avaliação humana (dado mal cadastrado?
    interpretação errada do documento? caso legítimo a tolerar?). None quando
    não há portão nesta rota, ou quando há mas não colide com nada."""
    if not pernas_portao:
        return None
    eventos = _eventos_incoerentes(graph, route, dest_pos)
    entrada = 0 in pernas_portao and any(_evento_toca_perna(t, i, 0) for t, i in eventos)
    n = len(route.edges)
    saida = (n - 1) in pernas_portao and any(_evento_toca_perna(t, i, n - 1) for t, i in eventos)
    if not entrada and not saida:
        return None
    return {"entrada": entrada, "saida": saida}


def _mesma_malha(points: list[dict]) -> bool:
    """Todos os waypoints da rota pertencem à MESMA carta REA? Proxy de
    "mesma região / pontos próximos" (feedback do Ivan 17/08): quando a malha
    vence a direta mas a rota inteira é local (não atravessa cartas por
    ponte), a direta continua sendo uma opção viável — mostrada como
    alternativa mesmo com a malha como principal."""
    charts = {p["chart"] for p in points if p["kind"] == "waypoint" and p.get("chart")}
    return len(charts) <= 1


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
    route: Any = None                    # DecodedRoute (edges+node_ids) p/ a V3; NÃO serializado

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
                  origin_gate_ids: Optional[set] = None,
                  dest_gate_ids: Optional[set] = None) -> V1RouteResult:
    # origin_gate_ids/dest_gate_ids (TAREFA_portoes.md): IDs de nó do portão
    # obrigatório do documento aplicado nesta rota (db.build_subgraph meta
    # "origin_gate_ids"/"dest_gate_ids") — None quando o aeródromo não tem
    # regra. Usado só pra saber QUAIS pernas ficam isentas da checagem de
    # coerência (o portão prevalece) e pra sinalizar colisão; não influencia
    # a decisão malha×direta em si.
    # Regra REA: se alguma ponta está em TMA REA, a rota é OBRIGADA a usar
    # >=1 corredor real (graph.requires_corridor, definido em add_synthetic_edges).
    require = bool(getattr(graph, "requires_corridor", False))

    # AUTORIDADE: shortest_route (Dijkstra com ESTADO DE FASE) — exato e
    # determinístico, codifica a regra das TMAs. As alternativas também vêm do
    # Dijkstra (k-shortest/Yen, mais abaixo): assim TODAS as rotas exibidas
    # respeitam a mesma regra de validade (o Grey Wolf Optimizer, usado antes
    # pra gerar alternativas, foi removido — nunca superava o Dijkstra em
    # distância e não conhecia a regra de fase; ver git history/gwo.py).
    used_direct_fallback = False
    mesh = shortest_route(graph, origin_id, dest_id, require_real_edge=require)

    # ANTI-ESPORÃO OBRIGATÓRIO: exigir corredor (require) pode produzir um
    # vai-e-volta quando a origem/destino está numa TMA REA mas o destino fica no
    # próprio PORTÃO de entrada, e o corredor obrigatório desse portão AFASTA do
    # destino. A rota então entra no corredor e volta pelo MESMO portão só para
    # cumprir a cota "usou >=1 corredor real" (ex.: SBGO->SBNV:
    # ...TRINDADE->ABADIA->TRINDADE...; SBBH->SBCF: ...CEASA->FLORES->CEASA...).
    # A exigência não faz sentido para esse par. Detecção robusta: a rota com
    # require repete um nó (laço por ID). Nesse caso relaxamos o require e ficamos
    # com a rota curta, DESDE QUE ela ainda ENTRE na REA (toque >=1 waypoint) e
    # não tenha laço. Corredores obrigatórios que PROGRIDEM não são afetados: com
    # eles a rota-com-require é monotônica (sem laço), então a condição não
    # dispara e a travessia é preservada (ex.: SBCG->SDTS mantém ZULU/BRAVO).
    # A margem do owes_synth (ver dijkstra._make_owes) é o outro lado desta
    # correção: sem ela o owes forçaria o mesmo esporão mesmo com require=False.
    if (require and mesh is not None and mesh.complete
            and len(set(mesh.node_ids)) != len(mesh.node_ids)):
        relaxed = shortest_route(graph, origin_id, dest_id,
                                 require_real_edge=False)
        if (relaxed is not None and relaxed.complete
                and len(set(relaxed.node_ids)) == len(relaxed.node_ids)
                and any(graph.nodes[n].kind == "waypoint"
                        for n in relaxed.node_ids)):
            mesh = relaxed
            require = False        # alternativas (Yen) seguem o mesmo critério

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
    route = _remove_duplicidade_destino(graph, route, dest_id)

    points = _route_points(graph, route)
    corridors = _corridors_used(route)
    legs = _route_legs(graph, route)

    direct_nm = m_to_nm(graph.direct_distance_m(origin_id, dest_id))
    total_nm = m_to_nm(_real_distance_m(graph, route))   # distância REAL (sem penalidade)

    # TAREFA_coerencia_geometrica.md (II.b): a malha só vence a rota direta se
    # tiver cadeia de corredor REAL (senão é "ponto isolado" — sempre perde,
    # ver II.c: também cobre o portão único dos casos 011/012, que não usam
    # corredor real nenhum), for geometricamente coerente (sem retrocesso/
    # curva acentuada) e não custar muito mais que a direta — fator RELATIVO,
    # não raio fixo (se adapta a rotas curtas e longas).
    tem_cadeia_real = bool(corridors)
    pernas_portao = _pernas_de_portao(route, origin_gate_ids, dest_gate_ids)
    incoerente = _rota_incoerente(graph, route, graph.nodes[dest_id].pos,
                                  pernas_protegidas=pernas_portao)
    colisao_portao = _colisao_portao(graph, route, graph.nodes[dest_id].pos, pernas_portao)
    fator = (total_nm / direct_nm) if direct_nm > 1e-6 else float("inf")
    preferir_direto = (not tem_cadeia_real) or incoerente or (fator > FATOR_RELATIVO_DIRETO)

    route_source_final = route_source
    direto_extra = None
    if preferir_direto:
        direct_dist_m = graph.direct_distance_m(origin_id, dest_id)
        direct_edge = Edge(origin_id, dest_id, direct_dist_m, corridor="DIRETO", synthetic=True)
        route = DecodedRoute([origin_id, dest_id], [direct_edge], direct_dist_m,
                             complete=True, fitness=direct_dist_m)
        points = _route_points(graph, route)
        corridors = []
        legs = _route_legs(graph, route)
        total_nm = direct_nm
        route_source_final = "direto-preferido"
        if not tem_cadeia_real:
            motivo = ("a malha REA não oferece cadeia de corredor com progresso "
                     "contínuo até o destino (ponto isolado)")
        elif incoerente:
            motivo = "a rota por corredor tem um trecho com retrocesso ou curva acentuada"
        else:
            motivo = (f"a rota por corredor é {fator:.2f}x mais longa que a direta "
                     f"(acima do fator relativo {FATOR_RELATIVO_DIRETO:.1f}x)")
        reason = f"Rota direta preferida ({direct_nm:.1f} NM): {motivo}."
    else:
        overhead = total_nm - direct_nm
        names = ", ".join(
            f"{c['name']} [{'Obrigatório' if c['is_mandatory'] else 'Opcional'}]"
            for c in corridors
        )
        reason = (
            f"Rota usa o(s) corredor(es) REA {names}, com acréscimo de "
            f"{overhead:.1f} NM sobre a rota direta ({direct_nm:.1f} NM) — "
            f"menor distância total entre as alternativas disponíveis."
        )
        # Feedback do Ivan 17/08: em rotas "de proximidade" — toda a malha
        # numa ÚNICA carta REA — a direta continua uma opção viável mesmo com
        # o corredor como principal (ex.: caso 020, SBVT->SIVU). Mostra a
        # direta como alternativa extra, fora do cap de 4 do k-shortest.
        if _mesma_malha(points):
            direct_dist_m = graph.direct_distance_m(origin_id, dest_id)
            direct_route = DecodedRoute(
                [origin_id, dest_id],
                [Edge(origin_id, dest_id, direct_dist_m, corridor="DIRETO", synthetic=True)],
                direct_dist_m, complete=True, fitness=direct_dist_m)
            direto_extra = {
                "points": _route_points(graph, direct_route),
                "total_distance_nm": round(direct_nm, 1),
                "overhead_nm": 0.0,
                "corridors_used": [],
                "n_points": 2,
            }

    # Alternativas: as próximas melhores rotas DISTINTAS e VÁLIDAS, geradas pelo
    # k-shortest (algoritmo de Yen) sobre o mesmo grafo com fase — não pelo GWO.
    # Todas respeitam owes/used. Pego k=5 e descarto a 1ª (== rota principal).
    k_routes = k_shortest_routes(graph, origin_id, dest_id, k=5,
                                 require_real_edge=eff_require)
    main_seq = list(route.node_ids)
    alternatives = []
    for alt in k_routes:
        alt = _remove_duplicidade_destino(graph, alt, dest_id)
        if list(alt.node_ids) == main_seq:
            continue
        # TAREFA_coerencia_geometrica.md (II).2 — filtro/rede de segurança:
        # descarta alternativa que começa no sentido oposto ou com desvio
        # relevante (retrocesso/curva), mesmo que a rota principal já esteja
        # coerente (casos 016-alt2, 017-alts, 020-alt2/alt3, 020-alt-PontaDaFruta).
        # Mesmo limiar da rota principal — um limiar frouxo só pra alternativas
        # deixava passar desvios pouco viáveis (ajuste do Ivan 17/08).
        alt_pernas_portao = _pernas_de_portao(alt, origin_gate_ids, dest_gate_ids)
        if _rota_incoerente(graph, alt, graph.nodes[dest_id].pos,
                            pernas_protegidas=alt_pernas_portao):
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

    if direto_extra is not None:
        alternatives.insert(0, direto_extra)

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
            "route_source": route_source_final,
            "preferiu_direto": preferir_direto,
            "malha_tem_cadeia_real": tem_cadeia_real,
            "malha_incoerente": incoerente,
            "malha_fator_relativo": None if fator == float("inf") else round(fator, 2),
            "colisao_portao_coerencia": colisao_portao,
        },
        route=route,
    )