"""Motor compartilhado do gabarito de equivalência TS (TAREFA_gabarito_TS_e_faxina.md).

Usado por `gerar_gabarito.py` (congela o snapshot) e `verifica_gabarito.py`
(recalcula ao vivo e compara com o snapshot). Mantido num só lugar para os
dois nunca divergirem na forma de calcular — só na forma de COMPARAR, que é
o que o time do TS precisa replicar.

NÃO faz parte do algoritmo (V1/V3): só adapta a saída de `plan_v1_route` +
`plan_from_v1` para o formato plano do gabarito (mesmos campos que
`nexatlas_cli.py`/`testes_voos.py` já leem, sem tocar em nada deles).
"""
from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg2  # noqa: E402

from nexatlas_router.db import PostgisLoader  # noqa: E402
from nexatlas_router.portoes import PistaObrigatoriaError  # noqa: E402
from nexatlas_router.v1 import plan_v1_route  # noqa: E402
from nexatlas_router.vertical import (  # noqa: E402
    Terrain, Wind, find as find_aircraft, load_from_db as load_aircraft_catalog,
    plan_from_v1, lateral_route_from_v1)
from nexatlas_router.vertical.magnetic import declination, WMM_EDITION  # noqa: E402,F401


def connect():
    required = ["NEXATLAS_DB_PASSWORD"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        sys.exit(f"Variáveis de ambiente ausentes: {', '.join(missing)}. Execute: source .env.sh")
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


def resolver_aeronave(catalog, caso: dict):
    """Resolve a aeronave do caso. `sem_combustivel: true` zera os 3 campos de
    combustível de uma aeronave REAL do catálogo (performance intacta) só para
    exercitar o caminho `None` do profile.py — não existe hoje nenhuma aeronave
    no banco com performance completa e combustível incompleto (as 130
    utilizáveis têm as duas coisas); é uma aeronave-dublê, claramente marcada
    na `entrada` do gabarito, nunca uma rota/posição inventada."""
    ac = find_aircraft(catalog, caso["aeronave"])
    if ac is None:
        raise LookupError(f"aeronave '{caso['aeronave']}' não encontrada no catálogo")
    if caso.get("sem_combustivel"):
        ac = dataclasses.replace(ac, fuel_ac=None, fuel_cruise=None, fuel_dc=None,
                                 fuel_unit=None, fuel_type=None)
    return ac


def montar_entrada(caso: dict, hora_partida_utc_str: Optional[str]) -> dict:
    """`hora_partida_utc_str` é None quando o caso não pede vento (TAREFA_
    gabarito_v2_contrato.md, 26/08/26: default agora é SEM hora/SEM vento —
    só os casos com `vento_modo: "relativo"` recebem uma string aqui, já
    resolvida pelo chamador). Quando `vento_modo` está presente no caso,
    ele e `vento_offset_h` são gravados na entrada — são a REGRA que
    `verifica_gabarito.py` reaplica (com `time.time()` fresco) em vez de
    reler o valor congelado, que é só informativo/reprodutível-se-logo."""
    entrada = {
        "origem": caso["origem"],
        "destino": caso["destino"],
        "aeronave": caso["aeronave"],
        "pista_origem": caso.get("pista_origem"),
        "pista_destino": caso.get("pista_destino"),
        "hora_partida_utc": hora_partida_utc_str,
    }
    if caso.get("vento_modo"):
        entrada["vento_modo"] = caso["vento_modo"]
        entrada["vento_offset_h"] = caso["vento_offset_h"]
        if caso.get("vento_par_reciproco"):
            entrada["vento_par_reciproco"] = caso["vento_par_reciproco"]
    if caso.get("sem_combustivel"):
        entrada["aeronave_observacao"] = (
            "aeronave real do catálogo com os 3 campos de combustível zerados "
            "artificialmente (nenhuma aeronave do banco hoje tem performance "
            "completa e combustível incompleto ao mesmo tempo) — cobre o "
            "caminho combustivel=None do profile.py")
    return entrada


def computar_caso(loader: PostgisLoader, catalog, terreno: Terrain, wind: Wind,
                  hora_partida_utc: Optional[float], caso: dict) -> dict:
    """Roda o pipeline real (build_subgraph -> plan_v1_route -> plan_from_v1)
    e devolve o dict `esperado` no formato do gabarito (lateral/vertical/
    vento/magnetico). É a MESMA função usada para congelar o snapshot e para
    verificar ao vivo — só o comparador (verifica_gabarito.py) muda.

    TAREFA_gabarito_v2_contrato.md (26/08/26): quando o input é incompleto
    (hoje só pista obrigatória faltando), o MOTOR devolve um retorno
    estruturado em vez de uma rota (`PistaObrigatoriaError.to_dict()`) — essa
    função repassa esse dict tal qual (`{"status": "faltou_input", ...}`) no
    lugar do dict lateral/vertical/vento/magnetico normal. `verifica_gabarito.
    py`/`gerar_gabarito.py` distinguem os dois formatos pela chave "status"
    (sinal) vs "lateral" (rota) — nunca uma exceção não tratada subindo pro
    chamador nesse caso específico; qualquer OUTRA exceção (ex.: ICAO
    inexistente, erro de banco) continua subindo normalmente, como falha
    real do caso."""
    try:
        graph, meta = loader.build_subgraph(
            caso["origem"], caso["destino"], chart_radius_nm=60.0, link_radius_nm=30.0,
            pista_origem=caso.get("pista_origem"), pista_destino=caso.get("pista_destino"))
    except PistaObrigatoriaError as e:
        return e.to_dict()
    result = plan_v1_route(graph, meta["origin_id"], meta["dest_id"],
                           origin_gate_ids=meta.get("origin_gate_ids"),
                           dest_gate_ids=meta.get("dest_gate_ids"))
    ac = resolver_aeronave(catalog, caso)
    perfil = plan_from_v1(graph, result, ac, terreno, wind, hora_partida_utc=hora_partida_utc)
    lateral = lateral_route_from_v1(graph, result.route)

    lateral_esperado = {
        "rota": [{"id": p["id"], "name": p["name"]} for p in result.points],
        "distancia_total_nm": round(result.total_distance_nm, 2),
        "distancia_direta_nm": round(result.direct_distance_nm, 2),
        "corredores_usados": [dict(c) for c in result.corridors_used],
    }

    vertical_esperado = {
        "vertices": [
            {"x_nm": round(v.x_nm, 2), "alt_ft": round(v.alt_ft, 1),
             "tipo": v.tipo, "nome": v.nome, "real": v.real}
            for v in perfil.vertices
        ],
        "toc_nm": round(perfil.toc_nm, 2),
        "tod_nm": round(perfil.tod_nm, 2),
        "tempo_min": {
            "subida": round(perfil.subida.tempo_min, 2),
            "cruzeiro": round(perfil.cruzeiro.tempo_min, 2),
            "descida": round(perfil.descida.tempo_min, 2),
            "total": round(perfil.tempo_total_min, 2),
        },
        "combustivel": {
            "subida": _r(perfil.comb_subida), "cruzeiro": _r(perfil.comb_cruzeiro),
            "descida": _r(perfil.comb_descida), "total": _r(perfil.comb_total),
            "unidade": perfil.comb_unit,
        },
        "avisos": list(perfil.avisos),
        "descida_ingreme_nm": [[round(a, 2), round(b, 2)] for a, b in perfil.descida_ingreme_nm],
    }

    tt_vento = perfil.tempo_total_vento_min
    vento_esperado = {
        "segmentos": [
            {"x0_nm": round(s.x0_nm, 2), "x1_nm": round(s.x1_nm, 2), "fase": s.fase,
             "gs_kt": round(s.gs_kt, 2), "componente_cauda_kt": round(s.comp_cauda_kt, 2),
             "deriva_deg": round(s.deriva_deg, 3)}
            for s in perfil.segmentos_vento
        ],
        "tempo_min_vento": (round(tt_vento, 2) if tt_vento is not None else None),
        "combustivel_vento": _r(perfil.comb_total_vento),
    }

    magnetico_esperado = []
    for leg in lateral.legs:
        if leg.is_corridor and leg.corridor_heading_mag is not None:
            latm = (leg.from_pos.lat + leg.to_pos.lat) / 2.0
            lonm = (leg.from_pos.lon + leg.to_pos.lon) / 2.0
            d = declination(latm, lonm)
            magnetico_esperado.append({
                "corredor": leg.corridor,
                "declinacao_deg": round(d, 4),
                "rumo_magnetico_deg": round(leg.corridor_heading_mag, 4),
                "rumo_verdadeiro_deg": round((leg.corridor_heading_mag + d) % 360.0, 4),
            })

    return {
        "lateral": lateral_esperado,
        "vertical": vertical_esperado,
        "vento": vento_esperado,
        "magnetico": magnetico_esperado,
    }


def _r(v: Optional[float]) -> Optional[float]:
    return round(v, 2) if v is not None else None
