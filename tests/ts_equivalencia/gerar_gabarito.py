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
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from motor_gabarito import (  # noqa: E402
    ROOT, WMM_EDITION, computar_caso, connect, montar_entrada)
from nexatlas_router.db import PostgisLoader  # noqa: E402
from nexatlas_router.vertical import Terrain, Wind, load_from_db, parse_hora_utc  # noqa: E402

# Fixa para TODOS os casos (1.1: "Fixar uma hora_partida_utc explícita por
# caso para o vento ser reprodutível") — usar a MESMA hora em pares
# recíprocos (ida/volta) é o que garante, com o mesmo campo de vento, que uma
# direção sai com cauda e a outra com proa (não é escolha arbitrária por
# caso, é uma propriedade do triângulo do vento).
HORA_PARTIDA_UTC = "24/08/2026 15:00"

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
     "aeronave": AC_C172, "pista_origem": None,
     "cobertura": ["lateral:portao_obrigatorio_sem_pista_informada"]},

    {"id": "lateral_portao_obrigatorio_com_pista", "origem": "SBBH", "destino": "SBFZ",
     "aeronave": AC_C172, "pista_origem": "13",
     "cobertura": ["lateral:portao_por_cabeceira_pista_informada"]},

    {"id": "lateral_portao_excecao_unica_x_ou_y", "origem": "SBJR", "destino": "SBKP",
     "aeronave": AC_C172, "pista_origem": "03",
     "cobertura": ["lateral:excecao_portao_unico_x_ou_y"]},

    {"id": "lateral_malha_de_passagem_deve_ser_direto", "origem": "SWME", "destino": "SBAQ",
     "aeronave": AC_C172,
     "cobertura": ["lateral:malha_de_passagem_vira_direto"]},

    {"id": "lateral_k_shortest_referencia_sp_mg", "origem": "SBBH", "destino": "SBMT",
     "aeronave": AC_C172,
     "cobertura": ["lateral:k_shortest_alternativas", "vertical:subida_teto_corredor",
                   "vertical:combustivel_por_fase", "magnetico:mg_sp"]},

    {"id": "lateral_portao_colisao_sp_rj", "origem": "SBRJ", "destino": "SBMT",
     "aeronave": AC_C172,
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
     "aeronave": AC_C172,
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
     "aeronave": AC_SR22,
     "cobertura": ["vertical:descida_start_entre_corredores", "vertical:combustivel_us_gal"]},

    {"id": "vertical_trecho_final_ingreme_sr22", "origem": "SBPA", "destino": "SBFL",
     "aeronave": AC_SR22,
     "cobertura": ["vertical:trecho_final_ingreme_com_aviso"]},

    {"id": "vertical_aeronave_sem_combustivel", "origem": "SBLO", "destino": "SBTG",
     "aeronave": AC_C172, "sem_combustivel": True,
     "cobertura": ["vertical:aeronave_sem_dados_combustivel"]},

    # -------------------------------------------------------------------- vento
    {"id": "vento_cauda_predominante_ida", "origem": "SBGO", "destino": "SBBE",
     "aeronave": AC_C172,
     "cobertura": ["vento:par_reciproco_ida"]},

    {"id": "vento_proa_predominante_volta", "origem": "SBBE", "destino": "SBGO",
     "aeronave": AC_C172,
     "cobertura": ["vento:par_reciproco_volta"]},

    {"id": "vento_regiao_nordeste_litoral", "origem": "SBBR", "destino": "SBNT",
     "aeronave": AC_C172,
     "cobertura": ["vento:rota_longa_diversidade", "magnetico:centro_oeste_nordeste"]},
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
    hora_partida = parse_hora_utc(HORA_PARTIDA_UTC)

    casos_out = []
    erros = []
    for i, caso in enumerate(CASOS, 1):
        print(f"  [{i:02d}/{len(CASOS)}] {caso['id']} ({caso['origem']} -> {caso['destino']})...", end=" ")
        try:
            esperado = computar_caso(loader, catalog, terreno, wind, hora_partida, caso)
            casos_out.append({
                "id": caso["id"],
                "cobertura": caso["cobertura"],
                "entrada": montar_entrada(caso, HORA_PARTIDA_UTC),
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
            "hora_partida_utc_usada": HORA_PARTIDA_UTC,
            "nota": (
                "Snapshot CONGELADO gerado a partir do banco jetstream (schema published) e do "
                "CDN de terreno/vento na data acima. Regenerar (python3 gerar_gabarito.py) quando "
                "o banco mudar de forma relevante para algum destes pares origem/destino. "
                "Tolerâncias de comparação em README_equivalencia.md. O bloco `magnetico` (e a "
                "deriva do vento) usa a declinação WMM calculada na DATA DO VOO de cada caso "
                "(`entrada.hora_partida_utc`), não na data de execução do runner — reproduzível "
                "independente de quando se roda; ver README_equivalencia.md."
            ),
        },
        "casos": casos_out,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(gabarito, f, ensure_ascii=False, indent=2)
    print(f"\n{len(casos_out)} casos congelados em {OUT_PATH}")


if __name__ == "__main__":
    main()
