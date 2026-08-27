#!/usr/bin/env python3
"""Gera tests/ts_equivalencia/gabarito_rotas.json — snapshot congelado do
estado ATUAL do banco `jetstream` (TAREFA_gabarito_TS_e_faxina.md, Parte 1).

Roda o algoritmo REAL (mesma cadeia da CLI: build_subgraph -> plan_v1_route ->
plan_from_v1, com terreno/vento do CDN) para uma lista fixa de casos e
congela entrada+saída esperada. Regenerar só quando o banco mudar de forma
relevante para algum destes casos (ver README_equivalencia.md).

Uso:
    source .env.sh
    python3 tests/ts_equivalencia/gerar_gabarito.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from motor_gabarito import (  # noqa: E402
    ROOT, WMM_EDITION, computar_caso, connect, montar_entrada)
from nexatlas_router.db import PostgisLoader  # noqa: E402
from nexatlas_router.vertical import Terrain, Wind, load_from_db  # noqa: E402

# TAREFA_gabarito_v2_contrato.md (26/08/26), Parte B: data fixa expira (o
# vento real do CDN só cobre ~5 dias de previsão a partir de "agora" — uma
# data fixa como "24/08/2026 15:00" congelada há alguns dias já não bate mais
# com o que o CDN serve hoje). Modelo NOVO, alinhado ao default "sem hora =
# sem vento" da TAREFA_pista_obrigatoria_e_vento_default.md:
#   - Por padrão, um caso NÃO leva hora nenhuma (`vento_modo` ausente) — o
#     motor não calcula vento, e esse bloco fica congelado VAZIO pra sempre
#     (nunca expira, porque nunca depende do CDN).
#   - Só os casos que testam vento DE PROPÓSITO marcam `vento_modo:
#     "relativo"` — usam AGORA_RELATIVA (= "agora" desta geração + algumas
#     horas, dentro da janela de previsão) em vez de uma data fixa.
#     `verifica_gabarito.py` RECALCULA essa hora com o "agora" dele (mesma
#     regra, +VENTO_OFFSET_H), não relê o valor congelado — por isso o valor
#     do vento em si é comparado por ESTRUTURA/sinal, não por igualdade
#     exata (ver README_equivalencia.md). Usar a MESMA AGORA_RELATIVA em
#     TODOS os casos relativos (calculada uma vez só aqui) garante que pares
#     recíprocos (ida/volta) continuem vendo o MESMO campo de vento.
VENTO_OFFSET_H = 6
AGORA_RELATIVA = time.time() + VENTO_OFFSET_H * 3600.0

OUT_PATH = Path(__file__).resolve().parent / "gabarito_rotas.json"

# Aeronaves (ids reais de published.aircraft_models, resolvidos por load_from_db):
AC_C172 = "1n7s5U5J"        # Cessna 172 — unidade l/h, GA leve, teto 13000
AC_SR22 = "01KN08Z9R5B392RGMW7A6D8988"  # Cirrus SR22 — us gal/h, teto 17000
AC_BE20 = "hgS_N8CC"        # Beechcraft Super King Air B200 — lb/h, turboprop, teto 28000

CASOS = [
    # ---------------------------------------------------------------- lateral
    {"id": "lateral_direto_puro_longo_norte_nordeste", "origem": "SIG6", "destino": "SBRF",
     "aeronave": AC_BE20,
     "cobertura": ["lateral:direto_puro_longo", "magnetico:regioes_distantes",
                   "vertical:combustivel_por_fase_turboprop"]},

    {"id": "lateral_direto_puro_sem_rea", "origem": "SBHT", "destino": "SBPJ",
     "aeronave": AC_BE20,
     "cobertura": ["lateral:direto_puro_longo", "vertical:cruzeiro_alto_sem_corredor"]},

    {"id": "lateral_corredor_malha_da_ponta_chegada", "origem": "SBMO", "destino": "SBRF",
     "aeronave": AC_C172,
     "cobertura": ["lateral:corredor_coerente_malha_da_ponta", "vertical:subida_teto_corredor"]},

    {"id": "lateral_portao_obrigatorio_sem_pista", "origem": "SBBH", "destino": "SBFZ",
     "aeronave": AC_C172, "pista_origem": None, "tipo_caso": "contrato_input",
     "cobertura": ["contrato:pista_obrigatoria_faltando"]},

    {"id": "lateral_portao_obrigatorio_com_pista", "origem": "SBBH", "destino": "SBFZ",
     "aeronave": AC_C172, "pista_origem": "13",
     "cobertura": ["lateral:portao_por_cabeceira_pista_informada"]},

    {"id": "contrato_pista_nao_casa", "origem": "SBBH", "destino": "SBFZ",
     "aeronave": AC_C172, "pista_origem": "31", "tipo_caso": "contrato_input",
     "cobertura": ["contrato:pista_informada_nao_casa_regra"]},

    {"id": "contrato_pista_falta_dois_lados", "origem": "SBRJ", "destino": "SBMT",
     "aeronave": AC_C172, "tipo_caso": "contrato_input",
     "cobertura": ["contrato:pista_obrigatoria_faltando_dois_extremos"]},

    {"id": "lateral_portao_excecao_unica_x_ou_y", "origem": "SBJR", "destino": "SBKP",
     "aeronave": AC_C172, "pista_origem": "03",
     "cobertura": ["lateral:excecao_portao_unico_x_ou_y"]},

    {"id": "lateral_malha_de_passagem_deve_ser_direto", "origem": "SWME", "destino": "SBAQ",
     "aeronave": AC_C172,
     "cobertura": ["lateral:malha_de_passagem_vira_direto"]},

    {"id": "lateral_k_shortest_referencia_sp_mg", "origem": "SBBH", "destino": "SBMT",
     "aeronave": AC_C172, "pista_origem": "13", "pista_destino": "12",
     "cobertura": ["lateral:k_shortest_alternativas", "vertical:subida_teto_corredor",
                   "vertical:combustivel_por_fase", "magnetico:mg_sp"]},

    {"id": "lateral_portao_colisao_sp_rj", "origem": "SBRJ", "destino": "SBMT",
     "aeronave": AC_C172, "pista_origem": "02", "pista_destino": "12",
     "cobertura": ["lateral:colisao_portao_coerencia", "magnetico:rj_sp"]},

    {"id": "lateral_duplicidade_waypoint_destino_sivu", "origem": "SBVT", "destino": "SIVU",
     "aeronave": AC_C172,
     "cobertura": ["lateral:duplicidade_ultimo_ponto_dedup_destino"]},

    {"id": "lateral_portao_colisao_ne_sul", "origem": "SBPS", "destino": "SBTV",
     "aeronave": AC_C172,
     "cobertura": ["lateral:colisao_portao_coerencia"]},

    {"id": "lateral_portao_colisao_ne_litoral", "origem": "SBTV", "destino": "SBRF",
     "aeronave": AC_C172,
     "cobertura": ["lateral:colisao_portao_coerencia", "magnetico:nordeste_litoral"]},

    {"id": "lateral_proximidade_ne", "origem": "SBNT", "destino": "SBSG",
     "aeronave": AC_C172, "pista_origem": "16L",
     "cobertura": ["lateral:proximidade_direta_vs_malha"]},

    {"id": "lateral_proximidade_centro_oeste", "origem": "SBCY", "destino": "SIAQ",
     "aeronave": AC_C172,
     "cobertura": ["lateral:proximidade_direta_vs_malha", "magnetico:centro_oeste"]},

    {"id": "lateral_proximidade_norte", "origem": "SISM", "destino": "SBMQ",
     "aeronave": AC_C172,
     "cobertura": ["lateral:proximidade_direta_vs_malha", "magnetico:norte"]},

    {"id": "lateral_regiao_norte_amazonia", "origem": "SBBE", "destino": "SWEQ",
     "aeronave": AC_C172,
     "cobertura": ["magnetico:norte_amazonia"]},

    {"id": "lateral_mesma_regiao_corredores", "origem": "SBBI", "destino": "SBCT",
     "aeronave": AC_C172,
     "cobertura": ["lateral:dentro_da_mesma_regiao"]},

    {"id": "lateral_entre_regioes_sul_sudeste", "origem": "SBCT", "destino": "SBVT",
     "aeronave": AC_C172,
     "cobertura": ["lateral:entre_regioes_diferentes"]},

    # ---------------------------------------------------------------- vertical
    {"id": "vertical_descida_start_multiplos_corredores", "origem": "SBGR", "destino": "SBGL",
     "aeronave": AC_C172,
     "cobertura": ["vertical:descida_start_entre_corredores", "magnetico:sp_rj"]},

    {"id": "vertical_descida_start_corredores_densos", "origem": "SBKP", "destino": "SBJR",
     "aeronave": AC_SR22, "pista_destino": "03",
     "cobertura": ["vertical:descida_start_entre_corredores", "vertical:combustivel_us_gal"]},

    {"id": "vertical_trecho_final_ingreme_sr22", "origem": "SBPA", "destino": "SBFL",
     "aeronave": AC_SR22,
     "cobertura": ["vertical:trecho_final_ingreme_com_aviso"]},

    {"id": "vertical_aeronave_sem_combustivel", "origem": "SBLO", "destino": "SBTG",
     "aeronave": AC_C172, "sem_combustivel": True,
     "cobertura": ["vertical:aeronave_sem_dados_combustivel"]},

    # -------------------------------------------------------------------- vento
    # TAREFA_gabarito_v2_contrato.md, Parte B: os 4 casos abaixo são os ÚNICOS
    # com `vento_modo: "relativo"` — só eles pedem hora/vento de propósito.
    # Todo o resto do gabarito roda SEM hora (default novo, ver VENTO_OFFSET_H
    # acima) e por isso nunca mais expira por causa da janela do CDN.
    # `vento_par_reciproco` (mesmo valor nos dois): usado só por
    # verifica_gabarito.py no modo relativo — NÃO afirma qual perna é cauda e
    # qual é proa (isso depende do forecast do momento, muda a cada
    # previsão; achado ao vivo 26/08: a mesma rota que era cauda na ida no
    # snapshot antigo virou proa na hora relativa de hoje — é o vento
    # mudando, não bug). A invariante que NÃO muda: voar o MESMO campo de
    # vento em rumos opostos produz efeitos OPOSTOS — uma perna mais rápida
    # e a outra mais lenta que sem vento, nunca as duas iguais. É isso que a
    # checagem cruzada confere (ver compara_vento_relativo/main).
    {"id": "vento_par_reciproco_ida", "origem": "SBGO", "destino": "SBBE",
     "aeronave": AC_C172, "vento_modo": "relativo", "vento_offset_h": VENTO_OFFSET_H,
     "vento_par_reciproco": "sbgo_sbbe",
     "cobertura": ["vento:par_reciproco_ida"]},

    {"id": "vento_par_reciproco_volta", "origem": "SBBE", "destino": "SBGO",
     "aeronave": AC_C172, "vento_modo": "relativo", "vento_offset_h": VENTO_OFFSET_H,
     "vento_par_reciproco": "sbgo_sbbe",
     "cobertura": ["vento:par_reciproco_volta"]},

    {"id": "vento_regiao_nordeste_litoral", "origem": "SBBR", "destino": "SBNT",
     "aeronave": AC_C172, "pista_destino": "16L",
     "vento_modo": "relativo", "vento_offset_h": VENTO_OFFSET_H,
     "cobertura": ["vento:rota_longa_diversidade", "magnetico:centro_oeste_nordeste"]},

    # ------------------------------------------------------------- contrato
    # TAREFA_gabarito_v2_contrato.md, Parte A, casos 5/6 — MESMO par/aeronave,
    # só variando hora informada ou não, pra isolar o efeito do default
    # "sem hora = sem vento" (Missão 2 da TAREFA_pista_obrigatoria_e_vento_
    # default.md) num par mínimo.
    {"id": "contrato_sem_hora_sem_vento", "origem": "SBMO", "destino": "SBRF",
     "aeronave": AC_C172, "tipo_caso": "contrato_input",
     "cobertura": ["contrato:sem_hora_sem_vento"]},

    {"id": "contrato_com_hora_com_vento", "origem": "SBMO", "destino": "SBRF",
     "aeronave": AC_C172, "tipo_caso": "contrato_input",
     "vento_modo": "relativo", "vento_offset_h": VENTO_OFFSET_H,
     "cobertura": ["contrato:com_hora_com_vento"]},
]


def main() -> None:
    print(f"Gerando gabarito com {len(CASOS)} casos...")
    conn = connect()
    loader = PostgisLoader(conn)
    catalog = load_from_db(conn)
    terreno = Terrain()
    wind = Wind()
    if not wind.disponivel():
        sys.exit(f"Vento indisponível ({wind.erro}) — aborte e cheque o CDN antes de congelar o gabarito "
                 f"(o gabarito precisa dos números REAIS de vento, não de (0,0)).")
    agora_relativa_iso = datetime.fromtimestamp(AGORA_RELATIVA, timezone.utc).isoformat()
    print(f"  Casos com vento usam AGORA_RELATIVA = {agora_relativa_iso} "
          f"(agora + {VENTO_OFFSET_H}h) — os demais rodam sem hora/sem vento.")

    casos_out = []
    erros = []
    for i, caso in enumerate(CASOS, 1):
        print(f"  [{i:02d}/{len(CASOS)}] {caso['id']} ({caso['origem']} -> {caso['destino']})...", end=" ")
        try:
            relativo = caso.get("vento_modo") == "relativo"
            hora_partida = AGORA_RELATIVA if relativo else None
            hora_str = agora_relativa_iso if relativo else None
            esperado = computar_caso(loader, catalog, terreno, wind, hora_partida, caso)
            casos_out.append({
                "id": caso["id"],
                "cobertura": caso["cobertura"],
                "tipo_caso": caso.get("tipo_caso", "rota"),
                "entrada": montar_entrada(caso, hora_str),
                "esperado": esperado,
            })
            print("ok")
        except Exception as e:
            print(f"FALHOU: {e}")
            erros.append((caso["id"], str(e)))

    conn.close()

    if erros:
        print(f"\n{len(erros)} caso(s) falharam — corrija a lista de CASOS antes de congelar:")
        for cid, msg in erros:
            print(f"  {cid}: {msg}")
        sys.exit(1)

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True, check=False).stdout.strip() or "desconhecido"

    gabarito = {
        "meta": {
            "gerado_em": datetime.now(timezone.utc).isoformat(),
            "commit": commit,
            "wmm_edition": WMM_EDITION,
            "vento_offset_h": VENTO_OFFSET_H,
            "agora_relativa_usada": agora_relativa_iso,
            "nota": (
                "Snapshot CONGELADO gerado a partir do banco jetstream (schema published) e do "
                "CDN de terreno/vento na data acima. Regenerar (python3 gerar_gabarito.py) quando "
                "o banco mudar de forma relevante para algum destes pares origem/destino. "
                "Tolerâncias de comparação em README_equivalencia.md. "
                "TAREFA_gabarito_v2_contrato.md (26/08/26): a maioria dos casos roda SEM "
                "`hora_partida_utc` (default do motor desde a TAREFA_pista_obrigatoria_e_vento_"
                "default.md) — o bloco `vento` fica vazio pra sempre, nunca expira. Só os casos "
                "com `entrada.vento_modo == 'relativo'` pedem vento de propósito: usam "
                "`agora_relativa_usada` (agora da GERAÇÃO + `vento_offset_h`), e "
                "`verifica_gabarito.py` RECALCULA essa hora com o `agora` dele (mesma regra) em "
                "vez de reler o valor congelado — por isso o vento desses casos é comparado por "
                "estrutura/sinal, não por igualdade exata. O bloco `magnetico` usa a declinação "
                "WMM na data de EXECUÇÃO de quem gerou/verificou (não em `hora_partida_utc`) — "
                "a variação secular do WMM (~0,025°/ano) fica bem dentro da tolerância de 0,01° "
                "por vários meses; ver README_equivalencia.md."
            ),
        },
        "casos": casos_out,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(gabarito, f, ensure_ascii=False, indent=2)
    print(f"\n{len(casos_out)} casos congelados em {OUT_PATH}")


if __name__ == "__main__":
    main()
