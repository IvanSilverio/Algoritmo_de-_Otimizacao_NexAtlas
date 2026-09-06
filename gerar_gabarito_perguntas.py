#!/usr/bin/env python3
"""Gera gabarito_perguntas_orquestrador.{md,json} — TAREFA_gabarito_perguntas_orquestrador.md.

Harness AO VIVO (banco jetstream + CDN) para o Caio verificar se o
orquestrador responde certo com base no que o MOTOR de rotas (V1 lateral +
V3 vertical, `nexatlas_router/`) entrega. Roda o pipeline real (build_subgraph
-> plan_v1_route -> plan_from_v1) para um conjunto pequeno de rotas e emite,
por rota, a resposta esperada a cada pergunta da Seção A de
`PERGUNTAS_ORQUESTRADOR_CAIO.md` (a Seção B não é testável pelo motor — ver
`tests/ts_equivalencia/CONTRATO_ERROS.md`).

Não congela número de vento: o vento varia com a hora e com a atualização do
CDN, então este script deve ser rodado de novo pelo Caio, NA MESMA JANELA em
que ele testa o orquestrador (mesmo bloco de previsão de 3h do CDN).

NÃO altera nada em `nexatlas_router/` — só importa e lê o resultado (mesmo
padrão de `nexatlas_cli.py`/`testes_voos.py`/`tests/ts_equivalencia/
motor_gabarito.py`).

Uso:
    source .env.sh
    python3 gerar_gabarito_perguntas.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nexatlas_router.db import PostgisLoader                        # noqa: E402
from nexatlas_router.portoes import PistaObrigatoriaError, EntradaAusenteError  # noqa: E402
from nexatlas_router.v1 import plan_v1_route                         # noqa: E402
from nexatlas_router.vertical import (                               # noqa: E402
    Terrain, Wind, find, load_from_db, plan_from_v1, lateral_route_from_v1)
from nexatlas_router.vertical.magnetic import magnetic_bearing       # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "tests" / "ts_equivalencia"))
from motor_gabarito import connect  # noqa: E402  (mesma conexão padrão do gabarito TS)

OUT_MD = Path(__file__).resolve().parent / "gabarito_perguntas_orquestrador.md"
OUT_JSON = Path(__file__).resolve().parent / "gabarito_perguntas_orquestrador.json"

AERONAVE = "C182"   # Cessna 182Q Skylane — teto 14000, combustível completo


def proxima_hora_cheia_utc() -> float:
    agora = dt.datetime.now(dt.timezone.utc)
    prox = agora.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    return prox.timestamp()


HORA_PARTIDA_UTC = proxima_hora_cheia_utc()

# --------------------------------------------------------------------- casos
# Ver TAREFA_gabarito_perguntas_orquestrador.md §"Conjunto de rotas".
CASOS: list[dict] = [
    {
        "id": "rota_principal_com_vento",
        "origem": "SIOZ", "destino": "SBSL", "aeronave": AERONAVE,
        "hora_partida_utc": HORA_PARTIDA_UTC,
        "motivo": (
            "SIOZ→SBSL (~634 NM): a malha vence a direta com folga (fator 1.01, "
            "2 corredores REAIS — FOXTROT/ECHO), tem_ingreme=True (trecho final "
            "BATATÃ CAEMA→SBSL excede a razão do banco, exercita a pergunta de "
            "aviso de descida, SEM aviso de terreno junto atrapalhando a leitura) "
            "e o vento no C182 muda tempo/combustível de forma clara (~17% na hora "
            "escolhida, não-trivial). Comprimento médio: nem os ~1700 NM extremos "
            "de casos como SIG6→SBRF, nem tão curto que o vento quase zere como em "
            "SBGO→SBBE (citado no TAREFA como exemplo a evitar). NOTA: a primeira "
            "escolha (SBCT→SBVT, caso 040 da bateria de 100 — tem_ingreme=True no "
            "resultados_testes_REA_vertical_index.csv gerado dias atrás) deixou de "
            "servir: há um fix AINDA NÃO COMMITADO em vertical/profile.py "
            "(TAREFA_V3_correcao_pico_e_descida.md, git status) que já resolveu o "
            "trecho íngreme daquela rota (tempo total caiu de 331.4 para 309.3 min "
            "rodando ao vivo agora) — o harness é agnóstico ao fix por design (ver "
            "TAREFA_gabarito_perguntas_orquestrador.md), então ele reflete o "
            "código ATUAL, não o snapshot antigo. Troquei para SIOZ→SBSL depois de "
            "checar ao vivo que ainda mostra tem_ingreme=True hoje; não está entre "
            "os casos que o Ivan pediu para evitar (96, 98, 025/030/033/035/038/"
            "053/065 — esses são os que o PRÓPRIO fix mudou; SIOZ→SBSL não é um "
            "deles nem se comporta como um)."
        ),
    },
    {
        "id": "rota_principal_sem_vento",
        "origem": "SIOZ", "destino": "SBSL", "aeronave": AERONAVE,
        "hora_partida_utc": None,
        "motivo": "Mesma rota principal, SEM hora_partida_utc — cobre o contrato "
                  "'sem hora → sem vento' (§1.2 do CONTRATO_ERROS.md).",
    },
    {
        "id": "pista_obrigatoria_faltando",
        "origem": "SBRJ", "destino": "SBMT", "aeronave": AERONAVE,
        "pista_origem": None, "pista_destino": None,
        "motivo": "SBRJ e SBMT têm TODAS as regras de cabeceira da direção relevante "
                  "condicionadas a pista, e nenhuma pista foi informada — dispara o "
                  "sinal faltou_input nos dois lados (§1.1 do CONTRATO_ERROS.md).",
    },
    {
        "id": "pista_nao_casa",
        "origem": "SBRJ", "destino": "SBMT", "aeronave": AERONAVE,
        "pista_origem": "99", "pista_destino": "99",
        "motivo": "Mesmo par, com uma pista que NÃO existe em nenhuma regra de SBRJ "
                  "(02/20) nem de SBMT (12/30) — o motor não deve travar nem forçar "
                  "portão, só devolver a rota normal.",
    },
    {
        "id": "origem_ausente",
        "origem": "", "destino": "SBRF", "aeronave": AERONAVE,
        "motivo": "Origem vazia (campo em branco) — dispara o sinal faltou_input do "
                  "§1.3 do CONTRATO_ERROS.md (TAREFA_faltou_input_origem_destino.md, "
                  "05/09/26), distinto do LookupError genérico que um ICAO "
                  "inexistente ainda daria. O orquestrador deve perguntar o "
                  "aeródromo de origem, não travar.",
    },
]


# ------------------------------------------------------------------- motor
def rumos_por_perna(lateral, data_voo: dt.date) -> list[dict]:
    """Rumo MAGNÉTICO de cada perna (corredor E direto) — não só as de corredor
    (diferente do bloco `magnetico` de tests/ts_equivalencia/motor_gabarito.py,
    que só lista corredores; aqui a pergunta é sobre TODA perna)."""
    out = []
    for leg in lateral.legs:
        if leg.is_corridor and leg.corridor_heading_mag is not None:
            rumo_mag = leg.corridor_heading_mag
            fonte = "corredor (proa do banco, já magnética)"
        else:
            rumo_mag = magnetic_bearing(leg.from_pos, leg.to_pos, data_voo)[0]
            fonte = "geometria (initial_bearing) + declinação WMM na data do voo"
        out.append({
            "de": leg.from_name, "para": leg.to_name,
            "corredor": leg.corridor, "rumo_magnetico_deg": round(rumo_mag, 1),
            "fonte": fonte,
        })
    return out


def frames_cdn_usados(wind: Wind) -> list[str]:
    """Timestamps dos frames do CDN de vento efetivamente lidos até agora
    (introspecção do cache de tiles do Wind — só diagnóstico, não influencia
    o cálculo)."""
    tiles = getattr(wind, "_tiles", {})
    ts_vistos = sorted({key[0] for key in tiles.keys()})
    return [dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat() for ts in ts_vistos]


def computar_rota(loader: PostgisLoader, catalog, terreno: Terrain, wind: Wind,
                  caso: dict) -> dict[str, Any]:
    try:
        graph, meta = loader.build_subgraph(
            caso["origem"], caso["destino"], chart_radius_nm=60.0, link_radius_nm=30.0,
            pista_origem=caso.get("pista_origem"), pista_destino=caso.get("pista_destino"))
    except (PistaObrigatoriaError, EntradaAusenteError) as e:
        return {"sinal": e.to_dict()}

    result = plan_v1_route(graph, meta["origin_id"], meta["dest_id"],
                           origin_gate_ids=meta.get("origin_gate_ids"),
                           dest_gate_ids=meta.get("dest_gate_ids"))
    ac = find(catalog, caso["aeronave"])
    if ac is None:
        raise LookupError(f"aeronave {caso['aeronave']!r} não encontrada no catálogo")

    hora = caso.get("hora_partida_utc")
    perfil = plan_from_v1(graph, result, ac, terreno, wind, hora_partida_utc=hora)
    lateral = lateral_route_from_v1(graph, result.route)

    hora_ref = hora if hora is not None else time.time()
    data_voo = dt.datetime.fromtimestamp(hora_ref, dt.timezone.utc).date()

    return {
        "result": result,
        "perfil": perfil,
        "lateral": lateral,
        "aeronave": ac,
        "rumos": rumos_por_perna(lateral, data_voo),
        "meta_portao": {
            "origin_gate_ids": (sorted(meta["origin_gate_ids"])
                               if meta.get("origin_gate_ids") else None),
            "dest_gate_ids": (sorted(meta["dest_gate_ids"])
                             if meta.get("dest_gate_ids") else None),
        },
    }


def _r(v: Optional[float], nd: int = 2) -> Optional[float]:
    return round(v, nd) if v is not None else None


def montar_bruto(dado: dict[str, Any]) -> dict[str, Any]:
    if "sinal" in dado:
        return {"sinal": dado["sinal"]}

    result, perfil, lateral = dado["result"], dado["perfil"], dado["lateral"]
    ac = dado["aeronave"]
    tt_vento = perfil.tempo_total_vento_min
    vento = None
    if perfil.hora_partida_utc is not None:
        vento = {
            "hora_partida_utc": dt.datetime.fromtimestamp(
                perfil.hora_partida_utc, dt.timezone.utc).isoformat(),
            "tempo_min_total_vento": _r(tt_vento),
            "combustivel_total_vento": _r(perfil.comb_total_vento),
            "segmentos": [
                {"x0_nm": round(s.x0_nm, 2), "x1_nm": round(s.x1_nm, 2), "fase": s.fase,
                 "gs_kt": round(s.gs_kt, 2), "tas_kt": round(s.tas_kt, 2),
                 "componente_cauda_kt": round(s.comp_cauda_kt, 2),
                 "deriva_deg": round(s.deriva_deg, 3)}
                for s in perfil.segmentos_vento
            ],
        }

    return {
        "lateral": {
            "rota": [{"id": p["id"], "name": p["name"]} for p in result.points],
            "distancia_total_nm": round(result.total_distance_nm, 2),
            "distancia_direta_nm": round(result.direct_distance_nm, 2),
            "corredores_usados": [dict(c) for c in result.corridors_used],
            "legs": [dict(l) for l in result.legs],
        },
        "vertical": {
            "cruzeiro_ft": round(perfil.cruzeiro_ft, 1),
            "alcancou_cruzeiro": perfil.alcancou_cruzeiro,
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
                "unidade": perfil.comb_unit, "tipo": perfil.fuel_type,
            },
            "tem_ingreme": bool(perfil.descida_ingreme_nm),
            "descida_ingreme_nm": [[round(a, 2), round(b, 2)] for a, b in perfil.descida_ingreme_nm],
            "avisos": list(perfil.avisos),
        },
        "vento": vento,
        "rumos_por_perna": dado["rumos"],
        "meta_portao": dado["meta_portao"],
        "aeronave_label": ac.label,
    }


# ------------------------------------------------------------ perguntas (Seção A)
def perguntas_sinal(bruto: dict) -> list[dict]:
    """`faltando` distingue os dois sinais de faltou_input hoje: pista_origem/
    pista_destino (§1.1 CONTRATO_ERROS.md) vs origem/destino (§1.3, TAREFA_
    faltou_input_origem_destino.md) — cada um com sua pergunta ao piloto."""
    sinal = bruto["sinal"]
    if any(c in sinal["faltando"] for c in ("pista_origem", "pista_destino")):
        pergunta = "Rota de aeródromo com regra de cabeceira sem informar a pista."
    else:
        pergunta = "Não informei o aeródromo de origem e/ou destino (campo vazio)."
    return [{
        "pergunta": pergunta,
        "campo_fonte": "status / faltando / aerodromos",
        "valor": sinal,
        "estabilidade": "ESTÁVEL",
    }]


def perguntas_rota(caso: dict, bruto: dict) -> list[dict]:
    lat, ver = bruto["lateral"], bruto["vertical"]
    perguntas = [
        {"pergunta": f"Qual a rota de {caso['origem']} para {caso['destino']}?",
         "campo_fonte": "lateral.rota", "valor": [p["name"] for p in lat["rota"]],
         "estabilidade": "ESTÁVEL"},
        {"pergunta": "Quantas milhas tem a rota? E em linha reta?",
         "campo_fonte": "lateral.distancia_total_nm / lateral.distancia_direta_nm",
         "valor": {"total_nm": lat["distancia_total_nm"], "direta_nm": lat["distancia_direta_nm"]},
         "estabilidade": "ESTÁVEL"},
        {"pergunta": "Por quais corredores REA a rota passa? Passou por corredor ou foi direto?",
         "campo_fonte": "lateral.corredores_usados",
         "valor": lat["corredores_usados"] if lat["corredores_usados"] else "rota direta (nenhum corredor)",
         "estabilidade": "ESTÁVEL"},
        {"pergunta": "Quais são as pernas, trecho a trecho?",
         "campo_fonte": "lateral.legs", "valor": lat["legs"], "estabilidade": "ESTÁVEL"},
        {"pergunta": f"Qual a altitude de cruzeiro para {caso['aeronave']}? Chegou a nivelar?",
         "campo_fonte": "vertical.cruzeiro_ft / vertical.alcancou_cruzeiro",
         "valor": {"cruzeiro_ft": ver["cruzeiro_ft"], "alcancou_cruzeiro": ver["alcancou_cruzeiro"]},
         "estabilidade": "ESTÁVEL"},
        {"pergunta": "Onde começa a descida (e onde termina a subida)?",
         "campo_fonte": "vertical.toc_nm / vertical.tod_nm",
         "valor": {"toc_nm": ver["toc_nm"], "tod_nm": ver["tod_nm"]},
         "estabilidade": "ESTÁVEL"},
        {"pergunta": "Quanto tempo em cada fase (subida / cruzeiro / descida)?",
         "campo_fonte": "vertical.tempo_min", "valor": ver["tempo_min"],
         "estabilidade": "ESTÁVEL"},
        {"pergunta": f"Quanto de combustível {caso['aeronave']} gasta nessa rota? Por fase?",
         "campo_fonte": "vertical.combustivel", "valor": ver["combustivel"],
         "estabilidade": "ESTÁVEL"},
        {"pergunta": "Tem trecho de descida fora do limite da aeronave? Onde?",
         "campo_fonte": "vertical.tem_ingreme / vertical.descida_ingreme_nm / vertical.avisos",
         "valor": {"tem_ingreme": ver["tem_ingreme"], "posicoes_nm": ver["descida_ingreme_nm"],
                   "avisos": ver["avisos"]},
         "estabilidade": "ESTÁVEL"},
        {"pergunta": "Qual o rumo de cada perna?",
         "campo_fonte": "rumos_por_perna", "valor": bruto["rumos_por_perna"],
         "estabilidade": "ESTÁVEL"},
    ]

    if bruto["vento"] is not None:
        v = bruto["vento"]
        hora_z = v["hora_partida_utc"]
        perguntas.append({
            "pergunta": f"Com decolagem às {hora_z}, o vento muda o tempo de voo? Quanto?",
            "campo_fonte": "vertical.tempo_min.total (sem vento) vs vento.tempo_min_total_vento",
            "valor": {"sem_vento_min": ver["tempo_min"]["total"], "com_vento_min": v["tempo_min_total_vento"],
                      "diferenca_min": round(v["tempo_min_total_vento"] - ver["tempo_min"]["total"], 2)},
            "estabilidade": "VENTO",
        })
        perguntas.append({
            "pergunta": "E o combustível, muda com o vento?",
            "campo_fonte": "vertical.combustivel.total (sem vento) vs vento.combustivel_total_vento",
            "valor": {"sem_vento": ver["combustivel"]["total"], "com_vento": v["combustivel_total_vento"],
                      "diferenca": round(v["combustivel_total_vento"] - ver["combustivel"]["total"], 2),
                      "unidade": ver["combustivel"]["unidade"]},
            "estabilidade": "VENTO",
        })
    else:
        perguntas.append({
            "pergunta": "Não informei hora — dá pra considerar o vento?",
            "campo_fonte": "vento (ausente: vertical não leva hora_partida_utc)",
            "valor": "NÃO — sem hora de partida o bloco de vento sai vazio "
                     "(sem segmentos; tempo/combustível de vento nulos); a rota "
                     "sai completa mesmo assim. Nunca inventar vento.",
            "estabilidade": "ESTÁVEL",
        })

    if caso["id"] == "pista_nao_casa":
        mp = bruto["meta_portao"]
        forcou = bool(mp["origin_gate_ids"] or mp["dest_gate_ids"])
        perguntas.append({
            "pergunta": "Informei uma pista que não existe naquele aeródromo — trava?",
            "campo_fonte": "meta_portao.origin_gate_ids / meta_portao.dest_gate_ids",
            "valor": {"portao_forcado": forcou, "meta_portao": mp,
                      "explicacao": ("NÃO deveria forçar (pista '99' não casa nenhuma "
                                     "regra de SBRJ nem SBMT) — rota normal abaixo."
                                     if not forcou else
                                     "ATENÇÃO: portão foi forçado mesmo com pista "
                                     "que não deveria casar — investigar.")},
            "estabilidade": "ESTÁVEL",
        })

    return perguntas


# --------------------------------------------------------------------- saída
def montar_md(header: dict, casos_out: list[dict]) -> str:
    linhas = [
        "# Gabarito de perguntas do orquestrador (para o Caio)",
        "",
        f"- `gerado_em`: {header['gerado_em']}",
        f"- `hora_partida_utc` (rota principal, com vento): {header['hora_partida_utc']}",
        f"- Frames do CDN de vento usados: {', '.join(header['frames_cdn']) or '(nenhum lido)'}",
        f"- Rota principal escolhida: **{header['rota_principal']}**",
        "",
        "> **Como usar:** rode `python3 gerar_gabarito_perguntas.py` de novo NA MESMA "
        "JANELA (mesmo bloco de 3h de previsão do CDN) em que for testar o "
        "orquestrador. Campos marcados **ESTÁVEL** têm que bater exato; campos "
        "**VENTO** têm tolerância pequena (±1-2%) e só valem para esta execução. "
        "As perguntas da Seção B de `PERGUNTAS_ORQUESTRADOR_CAIO.md` (combustível "
        "utilizável, reserva, parada, alternativa) **não têm esperado aqui** — não "
        "vêm do motor.",
        "",
    ]
    for c in casos_out:
        linhas.append(f"## {c['id']} — {c['origem']} → {c['destino']}")
        if c.get("motivo"):
            linhas.append(f"\n_{c['motivo']}_\n")
        if c.get("erro"):
            linhas.append(f"**FALHOU:** {c['erro']}\n")
            continue
        linhas.append("| Pergunta | Campo-fonte | Valor esperado (desta execução) | Estabilidade |")
        linhas.append("|---|---|---|---|")
        for p in c["perguntas"]:
            valor_str = json.dumps(p["valor"], ensure_ascii=False, default=str)
            if len(valor_str) > 300:
                valor_str = valor_str[:297] + "..."
            valor_str = valor_str.replace("|", "\\|")
            linhas.append(f"| {p['pergunta']} | `{p['campo_fonte']}` | {valor_str} | {p['estabilidade']} |")
        linhas.append("")
    return "\n".join(linhas)


def main() -> None:
    print(f"Conectando ao jetstream (rota principal: SIOZ→SBSL, {AERONAVE})...")
    conn = connect()
    loader = PostgisLoader(conn)
    catalog = load_from_db(conn)
    terreno = Terrain()
    wind = Wind()
    if not wind.disponivel():
        print(f"  [aviso] vento indisponível ({wind.erro}) — perguntas de vento ficam com GS=TAS (vento 0,0).")

    casos_out = []
    for caso in CASOS:
        print(f"  [{caso['id']}] {caso['origem']} -> {caso['destino']}...", end=" ")
        try:
            dado = computar_rota(loader, catalog, terreno, wind, caso)
            bruto = montar_bruto(dado)
            perguntas = perguntas_sinal(bruto) if "sinal" in bruto else perguntas_rota(caso, bruto)
            casos_out.append({
                "id": caso["id"], "origem": caso["origem"], "destino": caso["destino"],
                "aeronave": caso.get("aeronave"), "motivo": caso.get("motivo"),
                "hora_partida_utc": (dt.datetime.fromtimestamp(caso["hora_partida_utc"], dt.timezone.utc).isoformat()
                                     if caso.get("hora_partida_utc") else None),
                "perguntas": perguntas,
                "bruto": bruto,
            })
            print("ok")
        except Exception as e:
            print(f"FALHOU: {e}")
            casos_out.append({"id": caso["id"], "origem": caso["origem"], "destino": caso["destino"],
                              "erro": str(e)})
    conn.close()

    header = {
        "gerado_em": dt.datetime.now(dt.timezone.utc).isoformat(),
        "hora_partida_utc": dt.datetime.fromtimestamp(HORA_PARTIDA_UTC, dt.timezone.utc).isoformat(),
        "frames_cdn": frames_cdn_usados(wind),
        "rota_principal": f"SIOZ → SBSL ({AERONAVE})",
    }

    OUT_JSON.write_text(json.dumps({"header": header, "casos": casos_out}, ensure_ascii=False, indent=2,
                                   default=str), encoding="utf-8")
    OUT_MD.write_text(montar_md(header, casos_out), encoding="utf-8")
    print(f"\nGerado: {OUT_MD}\nGerado: {OUT_JSON}")


if __name__ == "__main__":
    main()
