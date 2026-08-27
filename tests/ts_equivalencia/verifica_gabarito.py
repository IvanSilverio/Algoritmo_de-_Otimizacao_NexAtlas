#!/usr/bin/env python3
"""Runner de referência do gabarito de equivalência TS
(TAREFA_gabarito_TS_e_faxina.md, 1.4).

Roda o NOSSO Python ao vivo contra `gabarito_rotas.json` e compara com as
TOLERÂNCIAS documentadas em README_equivalencia.md — não é bit-a-bit
(Python/pygeomag x TS/geomagnetism nunca vão bater exato). Serve de dupla
finalidade:
  1. Prova que o gabarito está correto (deve dar 100% no nosso próprio lado).
  2. É o EXEMPLO de como comparar para o time do TS escrever o runner deles
     (mesmo JSON, mesmas tolerâncias, campo a campo).

Uso:
    source .env.sh
    python3 tests/ts_equivalencia/verifica_gabarito.py
    python3 tests/ts_equivalencia/verifica_gabarito.py --caso lateral_portao_obrigatorio_com_pista
    python3 tests/ts_equivalencia/verifica_gabarito.py -v      # mostra os diffs de cada FAIL
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from motor_gabarito import computar_caso, connect  # noqa: E402
from nexatlas_router.db import PostgisLoader  # noqa: E402
from nexatlas_router.vertical import Terrain, Wind, load_from_db, parse_hora_utc  # noqa: E402

GABARITO_PATH = Path(__file__).resolve().parent / "gabarito_rotas.json"

# ----------------------------------------------------------------- tolerâncias
TOL_DIST_NM = 0.05          # distância total / direta
TOL_X_NM = 0.1              # x dos vértices, toc_nm, tod_nm, descida_ingreme_nm
TOL_ALT_FT = 1.0            # altitude dos vértices
TOL_ANGULO_DEG = 0.01       # declinação / rumo verdadeiro / rumo magnético / deriva
TOL_TEMPO_MIN = 0.1
TOL_COMBUSTIVEL = 0.1       # na unidade nativa da aeronave
TOL_VENTO_KT = 0.1          # groundspeed / componente de vento


class Diffs(list):
    def add(self, campo: str, esperado, obtido):
        self.append(f"{campo}: esperado={esperado!r} obtido={obtido!r}")


def _num_ok(a, b, tol) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol


def compara_lateral(esperado: dict, obtido: dict, diffs: Diffs) -> bool:
    ok = True
    ids_e = [p["id"] for p in esperado["rota"]]
    ids_o = [p["id"] for p in obtido["rota"]]
    if ids_e != ids_o:
        diffs.add("lateral.rota (sequência de IDs)", ids_e, ids_o)
        ok = False
    if not _num_ok(esperado["distancia_total_nm"], obtido["distancia_total_nm"], TOL_DIST_NM):
        diffs.add("lateral.distancia_total_nm", esperado["distancia_total_nm"], obtido["distancia_total_nm"])
        ok = False
    if not _num_ok(esperado["distancia_direta_nm"], obtido["distancia_direta_nm"], TOL_DIST_NM):
        diffs.add("lateral.distancia_direta_nm", esperado["distancia_direta_nm"], obtido["distancia_direta_nm"])
        ok = False
    if esperado["corredores_usados"] != obtido["corredores_usados"]:
        diffs.add("lateral.corredores_usados", esperado["corredores_usados"], obtido["corredores_usados"])
        ok = False
    return ok


def compara_vertical(esperado: dict, obtido: dict, diffs: Diffs) -> bool:
    ok = True
    ve, vo = esperado["vertices"], obtido["vertices"]
    if len(ve) != len(vo):
        diffs.add("vertical.vertices (quantidade)", len(ve), len(vo))
        ok = False
    else:
        for i, (a, b) in enumerate(zip(ve, vo)):
            if not _num_ok(a["x_nm"], b["x_nm"], TOL_X_NM):
                diffs.add(f"vertical.vertices[{i}].x_nm", a["x_nm"], b["x_nm"]); ok = False
            if not _num_ok(a["alt_ft"], b["alt_ft"], TOL_ALT_FT):
                diffs.add(f"vertical.vertices[{i}].alt_ft", a["alt_ft"], b["alt_ft"]); ok = False
            if a["tipo"] != b["tipo"]:
                diffs.add(f"vertical.vertices[{i}].tipo", a["tipo"], b["tipo"]); ok = False
            if a["nome"] != b["nome"]:
                diffs.add(f"vertical.vertices[{i}].nome", a["nome"], b["nome"]); ok = False
            if a["real"] != b["real"]:
                diffs.add(f"vertical.vertices[{i}].real", a["real"], b["real"]); ok = False
    if not _num_ok(esperado["toc_nm"], obtido["toc_nm"], TOL_X_NM):
        diffs.add("vertical.toc_nm", esperado["toc_nm"], obtido["toc_nm"]); ok = False
    if not _num_ok(esperado["tod_nm"], obtido["tod_nm"], TOL_X_NM):
        diffs.add("vertical.tod_nm", esperado["tod_nm"], obtido["tod_nm"]); ok = False
    for fase in ("subida", "cruzeiro", "descida", "total"):
        a, b = esperado["tempo_min"][fase], obtido["tempo_min"][fase]
        if not _num_ok(a, b, TOL_TEMPO_MIN):
            diffs.add(f"vertical.tempo_min.{fase}", a, b); ok = False
    for fase in ("subida", "cruzeiro", "descida", "total"):
        a, b = esperado["combustivel"][fase], obtido["combustivel"][fase]
        if not _num_ok(a, b, TOL_COMBUSTIVEL):
            diffs.add(f"vertical.combustivel.{fase}", a, b); ok = False
    if esperado["combustivel"]["unidade"] != obtido["combustivel"]["unidade"]:
        diffs.add("vertical.combustivel.unidade", esperado["combustivel"]["unidade"], obtido["combustivel"]["unidade"])
        ok = False
    de, do = esperado["descida_ingreme_nm"], obtido["descida_ingreme_nm"]
    if len(de) != len(do):
        diffs.add("vertical.descida_ingreme_nm (quantidade de trechos)", len(de), len(do)); ok = False
    else:
        for i, (a, b) in enumerate(zip(de, do)):
            if not (_num_ok(a[0], b[0], TOL_X_NM) and _num_ok(a[1], b[1], TOL_X_NM)):
                diffs.add(f"vertical.descida_ingreme_nm[{i}]", a, b); ok = False
    return ok


def compara_vento(esperado: dict, obtido: dict, diffs: Diffs) -> bool:
    ok = True
    se, so = esperado["segmentos"], obtido["segmentos"]
    if len(se) != len(so):
        diffs.add("vento.segmentos (quantidade)", len(se), len(so))
        ok = False
    else:
        for i, (a, b) in enumerate(zip(se, so)):
            if not _num_ok(a["gs_kt"], b["gs_kt"], TOL_VENTO_KT):
                diffs.add(f"vento.segmentos[{i}].gs_kt", a["gs_kt"], b["gs_kt"]); ok = False
            if not _num_ok(a["componente_cauda_kt"], b["componente_cauda_kt"], TOL_VENTO_KT):
                diffs.add(f"vento.segmentos[{i}].componente_cauda_kt",
                         a["componente_cauda_kt"], b["componente_cauda_kt"]); ok = False
            if not _num_ok(a["deriva_deg"], b["deriva_deg"], TOL_ANGULO_DEG):
                diffs.add(f"vento.segmentos[{i}].deriva_deg", a["deriva_deg"], b["deriva_deg"]); ok = False
    if not _num_ok(esperado["tempo_min_vento"], obtido["tempo_min_vento"], TOL_TEMPO_MIN):
        diffs.add("vento.tempo_min_vento", esperado["tempo_min_vento"], obtido["tempo_min_vento"]); ok = False
    if not _num_ok(esperado["combustivel_vento"], obtido["combustivel_vento"], TOL_COMBUSTIVEL):
        diffs.add("vento.combustivel_vento", esperado["combustivel_vento"], obtido["combustivel_vento"]); ok = False
    return ok


def compara_vento_relativo(esperado: dict, obtido: dict, diffs: Diffs) -> bool:
    """TAREFA_gabarito_v2_contrato.md, Parte B: casos `vento_modo: 'relativo'`
    usam uma hora recalculada a cada execução (não a congelada), porque o
    vento REAL muda a cada previsão — comparar `gs_kt`/`componente_cauda_kt`
    por igualdade exata contra o congelado falharia sempre, sem ser
    regressão. Comparação ESTRUTURAL: mesma contagem de segmentos (isso NÃO
    depende do vento, só da geometria — pode ser exato) e groundspeed
    positiva/plausível em cada um. NÃO afirma qual sentido leva cauda/proa —
    achado ao vivo (26/08): isso muda com o forecast, não é propriedade fixa
    da rota (ver `vento_par_reciproco` em main() pra invariante que de fato
    não muda)."""
    ok = True
    se, so = esperado["segmentos"], obtido["segmentos"]
    if len(se) != len(so):
        diffs.add("vento.segmentos (quantidade)", len(se), len(so))
        return False
    for i, s in enumerate(so):
        if s["gs_kt"] <= 0:
            diffs.add(f"vento.segmentos[{i}].gs_kt (deveria ser > 0)", ">0", s["gs_kt"])
            ok = False
    if obtido["tempo_min_vento"] is None:
        diffs.add("vento.tempo_min_vento (deveria existir — vento_modo=relativo)", "not None", None)
        ok = False
    return ok


def compara_sinal(esperado: dict, obtido: dict, diffs: Diffs) -> bool:
    """Casos `tipo_caso: 'contrato_input'` cujo motor recusa a rota — o
    `esperado`/`obtido` aqui é `PistaObrigatoriaError.to_dict()`
    (TAREFA_gabarito_v2_contrato.md), não o dict lateral/vertical/vento/
    magnetico. Compara só os campos ESTRUTURADOS (`status`, `faltando`,
    `aerodromos`) — `mensagem` é texto livre, como `vertical.avisos`, não é
    comparado (ver README_equivalencia.md §3)."""
    ok = True
    if esperado.get("status") != obtido.get("status"):
        diffs.add("sinal.status", esperado.get("status"), obtido.get("status")); ok = False
    if sorted(esperado.get("faltando") or []) != sorted(obtido.get("faltando") or []):
        diffs.add("sinal.faltando", esperado.get("faltando"), obtido.get("faltando")); ok = False
    if (esperado.get("aerodromos") or {}) != (obtido.get("aerodromos") or {}):
        diffs.add("sinal.aerodromos", esperado.get("aerodromos"), obtido.get("aerodromos")); ok = False
    return ok


def compara_magnetico(esperado: list, obtido: list, diffs: Diffs) -> bool:
    ok = True
    if len(esperado) != len(obtido):
        diffs.add("magnetico (quantidade de pernas de corredor)", len(esperado), len(obtido))
        return False
    for i, (a, b) in enumerate(zip(esperado, obtido)):
        if a["corredor"] != b["corredor"]:
            diffs.add(f"magnetico[{i}].corredor", a["corredor"], b["corredor"]); ok = False
        if not _num_ok(a["declinacao_deg"], b["declinacao_deg"], TOL_ANGULO_DEG):
            diffs.add(f"magnetico[{i}].declinacao_deg", a["declinacao_deg"], b["declinacao_deg"]); ok = False
        if not _num_ok(a["rumo_magnetico_deg"], b["rumo_magnetico_deg"], TOL_ANGULO_DEG):
            diffs.add(f"magnetico[{i}].rumo_magnetico_deg", a["rumo_magnetico_deg"], b["rumo_magnetico_deg"]); ok = False
        if not _num_ok(a["rumo_verdadeiro_deg"], b["rumo_verdadeiro_deg"], TOL_ANGULO_DEG):
            diffs.add(f"magnetico[{i}].rumo_verdadeiro_deg", a["rumo_verdadeiro_deg"], b["rumo_verdadeiro_deg"]); ok = False
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--caso", default=None, help="roda só um caso pelo id")
    ap.add_argument("-v", "--verbose", action="store_true", help="mostra os diffs de cada FAIL")
    args = ap.parse_args()

    with open(GABARITO_PATH, encoding="utf-8") as f:
        gabarito = json.load(f)

    casos = gabarito["casos"]
    if args.caso:
        casos = [c for c in casos if c["id"] == args.caso]
        if not casos:
            sys.exit(f"Caso '{args.caso}' não encontrado no gabarito.")

    print(f"Gabarito: {GABARITO_PATH.name}  (gerado em {gabarito['meta']['gerado_em']}, "
          f"commit {gabarito['meta']['commit']}, {gabarito['meta']['wmm_edition']})")
    print(f"Rodando {len(casos)} caso(s) contra o banco/CDN ao vivo...\n")

    conn = connect()
    loader = PostgisLoader(conn)
    catalog = load_from_db(conn)
    terreno = Terrain()
    wind = Wind()
    if not wind.disponivel():
        print(f"[AVISO] Vento indisponível ({wind.erro}) — os casos com `vento_modo: relativo` "
              f"vão comparar contra (0,0) e provavelmente vão FALHAR (não é regressão, é o CDN fora do ar).")

    # TAREFA_gabarito_v2_contrato.md, Parte B: resolvida UMA VEZ pra toda a
    # rodada (não por caso) — mesma razão do AGORA_RELATIVA em gerar_gabarito.
    # py: garante que pares recíprocos vejam o MESMO campo de vento.
    agora = time.time()

    # TAREFA_gabarito_v2_contrato.md, Parte B: grupo -> [(id, delta_min), ...]
    # pra checagem cruzada DEPOIS do loop (ver abaixo) — a invariante que não
    # muda com o forecast é "as duas pernas de um par recíproco têm efeito
    # OPOSTO" (uma mais rápida, a outra mais lenta que sem vento), não qual
    # delas especificamente leva cauda.
    pares_reciprocos: dict[str, list] = {}

    resultados = []
    for caso in casos:
        entrada = caso["entrada"]
        vento_modo = entrada.get("vento_modo")
        if entrada.get("hora_partida_utc") is None:
            hora_partida = None
        elif vento_modo == "relativo":
            # NÃO relê o valor congelado — recalcula com o `agora` de HOJE,
            # pra cair sempre dentro da janela de previsão do CDN (é isso que
            # faz o caso nunca expirar; ver compara_vento_relativo).
            hora_partida = agora + entrada["vento_offset_h"] * 3600.0
        else:
            hora_partida = parse_hora_utc(entrada["hora_partida_utc"])
        caso_input = {
            "origem": entrada["origem"], "destino": entrada["destino"],
            "aeronave": entrada["aeronave"],
            "pista_origem": entrada.get("pista_origem"),
            "pista_destino": entrada.get("pista_destino"),
            "sem_combustivel": "aeronave_observacao" in entrada,
        }
        is_sinal_esperado = "status" in caso["esperado"]

        try:
            obtido = computar_caso(loader, catalog, terreno, wind, hora_partida, caso_input)
            erro = None
        except Exception as e:
            obtido = None
            erro = str(e)

        is_sinal_obtido = obtido is not None and "status" in obtido

        if erro is not None:
            msg = f"EXCEÇÃO: {erro}"
            blocos = ({"sinal": (False, Diffs([msg]))} if is_sinal_esperado else
                     {b: (False, Diffs([msg])) for b in ("lateral", "vertical", "vento", "magnetico")})
        elif is_sinal_esperado != is_sinal_obtido:
            # formato incompatível: o motor devolveu rota onde se esperava um
            # sinal estruturado, ou vice-versa — sempre uma falha real, nunca
            # um KeyError tentando ler blocos que não existem no outro lado.
            msg = (f"esperava {'SINAL' if is_sinal_esperado else 'ROTA'}, obteve "
                  f"{'SINAL' if is_sinal_obtido else 'ROTA'}"
                  + (f" ({obtido.get('mensagem')})" if is_sinal_obtido else ""))
            blocos = ({"sinal": (False, Diffs([msg]))} if is_sinal_esperado else
                     {b: (False, Diffs([msg])) for b in ("lateral", "vertical", "vento", "magnetico")})
        elif is_sinal_esperado:
            diffs = Diffs()
            ok = compara_sinal(caso["esperado"], obtido, diffs)
            blocos = {"sinal": (ok, diffs)}
        else:
            blocos = {}
            for bloco in ("lateral", "vertical", "vento", "magnetico"):
                diffs = Diffs()
                if bloco == "vento" and vento_modo == "relativo":
                    ok = compara_vento_relativo(caso["esperado"]["vento"], obtido["vento"], diffs)
                    blocos[bloco] = (ok, diffs)
                    par = entrada.get("vento_par_reciproco")
                    tempo_com_vento = obtido["vento"]["tempo_min_vento"]
                    if par and ok and tempo_com_vento is not None:
                        tempo_sem_vento = obtido["vertical"]["tempo_min"]["total"]
                        pares_reciprocos.setdefault(par, []).append(
                            (caso["id"], tempo_com_vento - tempo_sem_vento, blocos, diffs))
                    continue
                cmp_fn = {"lateral": compara_lateral, "vertical": compara_vertical,
                         "vento": compara_vento, "magnetico": compara_magnetico}[bloco]
                ok = cmp_fn(caso["esperado"][bloco], obtido[bloco], diffs)
                blocos[bloco] = (ok, diffs)
        resultados.append((caso["id"], blocos))

    conn.close()

    # --------------------------------------------- checagem cruzada (Parte B)
    # As duas pernas de um par recíproco voam o MESMO campo de vento (mesma
    # AGORA_RELATIVA) em rumos opostos — uma tem que sair mais rápida e a
    # outra mais lenta que sem vento, nunca as duas iguais (a física do
    # triângulo do vento garante isso; não importa qual delas é qual, isso
    # muda com o forecast). Limiar de 0,5 min evita falso-positivo no caso
    # raro de vento quase perpendicular às duas pernas (través puro).
    for par, membros in pares_reciprocos.items():
        if len(membros) != 2:
            continue
        (id_a, delta_a, blocos_a, diffs_a), (id_b, delta_b, blocos_b, diffs_b) = membros
        if (delta_a > 0) == (delta_b > 0) and abs(delta_a) > 0.5 and abs(delta_b) > 0.5:
            sentido = "MAIS LENTAS" if delta_a > 0 else "MAIS RÁPIDAS"
            msg = (f"par recíproco {par!r} ({id_a} / {id_b}): as duas pernas ficaram "
                  f"{sentido} que sem vento (δ={delta_a:+.1f} e {delta_b:+.1f} min) — "
                  f"esperava sentidos OPOSTOS (mesmo campo de vento, rumos opostos).")
            diffs_a.add("vento.par_reciproco", "sentidos opostos", msg)
            diffs_b.add("vento.par_reciproco", "sentidos opostos", msg)
            blocos_a["vento"] = (False, diffs_a)
            blocos_b["vento"] = (False, diffs_b)

    # ------------------------------------------------------------- relatório
    # "—" marca bloco N/A pro tipo do caso: um caso-sinal só tem "sinal"; um
    # caso-rota só tem os outros 4 (nunca "sinal") — só conta pro total quem
    # de fato existe em `blocos`, N/A nunca pesa contra o PASS/FAIL.
    total_blocos = 0
    ok_blocos = 0
    print(f"{'caso':<48} {'lateral':<9} {'vertical':<9} {'vento':<9} {'magnetico':<9} {'sinal':<9}")
    print("-" * 97)
    for cid, blocos in resultados:
        linha = [cid[:47]]
        for b in ("lateral", "vertical", "vento", "magnetico", "sinal"):
            if b not in blocos:
                linha.append("—")
                continue
            ok, _ = blocos[b]
            total_blocos += 1
            ok_blocos += 1 if ok else 0
            linha.append("PASS" if ok else "FAIL")
        print(f"{linha[0]:<48} {linha[1]:<9} {linha[2]:<9} {linha[3]:<9} {linha[4]:<9} {linha[5]:<9}")
        if args.verbose:
            for b in ("lateral", "vertical", "vento", "magnetico", "sinal"):
                if b not in blocos:
                    continue
                ok, diffs = blocos[b]
                if not ok:
                    for d in diffs:
                        print(f"    [{b}] {d}")

    casos_ok = sum(1 for _, blocos in resultados if all(ok for ok, _ in blocos.values()))
    print("-" * 97)
    print(f"Casos: {casos_ok}/{len(resultados)} 100% PASS   "
          f"Blocos: {ok_blocos}/{total_blocos} PASS "
          f"({100 * ok_blocos / total_blocos:.1f}%)")

    if casos_ok < len(resultados):
        print("\nRode com -v para ver os diffs de cada FAIL.")
        sys.exit(1)
    print("\nGabarito 100% verificado.")


if __name__ == "__main__":
    main()
