#!/usr/bin/env python3
"""
Análise de comportamento vertical — matriz de AERONAVES × ROTAS.

Roda a mesma cadeia da CLI (conexão -> V1 -> V3) para várias aeronaves (tetos/
desempenhos diferentes) e várias rotas (distâncias diferentes), e resume tudo
numa tabela no terminal + um CSV, para você analisar como a altitude de cruzeiro
e os tempos de subida/cruzeiro/descida se comportam.

USO:
    source .env.sh          # credenciais do jetstream (não versionado)
    python3 analisar_voos.py

Edite AERONAVES e ROTAS abaixo. Requer pygeomag instalado (V3) e acesso ao
jetstream + CDN de terreno (igual à CLI).
"""
from __future__ import annotations
import datetime as dt
import os
import sys
import csv
import time

# ============================ CONFIGURAÇÃO (edite aqui) ============================

# Aeronaves a testar: ICAO/designator ou id. Deixe VAZIO ([]) para o script
# escolher automaticamente um "leque" de tetos (baixo -> alto) do catálogo.
AERONAVES: list[str] = []
N_AUTO = 4                      # nº de aeronaves no leque automático

# Rotas a testar (origem, destino) — escolhidas para variar a DISTÂNCIA.
# Os exemplos abaixo são os casos de referência da CLI; troque/adicione à vontade.
ROTAS: list[tuple[str, str]] = [
    ("SBMO", "SBRF"),   # ~105 NM  — curta (REA só na chegada)
    ("SBLO", "SBTG"),   # ~165 NM  — média-curta (REA só na saída)
    ("SBPA", "SBFL"),   # ~215 NM  — média (serra no caminho)
    ("SBBH", "SBMT"),   # ~285 NM  — média-longa (REA saída + chegada)
    ("SBHT", "SBPJ"),   # ~480 NM  — longa (sem REA, direto)
    # ---- variação de contexto (região, densidade de corredor, "diretice") ----
    ("SWDT", "SBBR"),   # ~107 NM  — curta, corredores nas duas pontas
    ("SNCL", "SBVT"),   # ~423 NM  — quase direto (overhead de corredor mínimo)
    ("SBGR", "SBGL"),   # ~246 NM  — SP↔RJ, corredores DENSOS (7) nas duas pontas
    ("SBKP", "SBJR"),   # ~228 NM  — SP↔RJ, corredores densos (6)
    ("SIG6", "SBRF"),   # ~1715 NM — rota longuíssima, origem em aeródromo externo
    ("SBCY", "SDPH"),   # ~378 NM  — destino em aeródromo externo, quase direto
    ("SBPA", "SSIQ"),   # ~292 NM  — região Sul, destino em aeródromo externo
    ("SBGO", "SBBE"),   # ~955 NM  — Centro-Oeste → Norte (Amazônia)
    ("SIVJ", "SBMQ"),   # ~1288 NM — rota muito longa, região Norte
    ("SBBR", "SBNT"),   # ~968 NM  — Centro-Oeste → Nordeste litorâneo
]

SALVAR_GRAFICOS = True          # salva o PNG do perfil de cada caso
PASTA_SAIDA = "analise_voos"    # onde salvar os PNGs
CSV_SAIDA = "analise_voos.csv"  # tabela completa

# Hora de partida (UTC) usada em TODOS os casos desta rodada — fixa, pra
# bateria ficar reprodutível (mesmo padrão de AERONAVES/ROTAS: edite e rode).
# None = usa "agora" (UTC), resolvido UMA VEZ no início do main() (não teria
# sentido cada caso pegar um "agora" diferente enquanto a bateria roda).
# Aceita (ver parse_hora_utc em wind.py): data BR "15/08/2026 14:30" ou
# "15/08/2026" (00:00), só hora "14:30" (assume hoje, UTC), ISO-8601
# "2026-08-15T14:30:00", ou unix (int/float).
HORA_PARTIDA_UTC: str | float | None = None

# ==================================================================================

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg2  # noqa: E402
from nexatlas_router.db import PostgisLoader                       # noqa: E402
from nexatlas_router.gwo import GWOConfig                          # noqa: E402
from nexatlas_router.v1 import plan_v1_route                       # noqa: E402
from nexatlas_router.vertical import (                             # noqa: E402
    Terrain, Wind, parse_hora_utc, load_from_db, find, plan_from_v1,
    plot_vertical_profile)

# cores (degrada para vazio se não for TTY)
_C = sys.stdout.isatty()
RST = "\033[0m" if _C else ""; BLD = "\033[1m" if _C else ""; DIM = "\033[2m" if _C else ""
GRN = "\033[32m" if _C else ""; RED = "\033[31m" if _C else ""; CYN = "\033[36m" if _C else ""


def connect():
    if not os.environ.get("NEXATLAS_DB_PASSWORD"):
        sys.exit(f"{RED}Faltam credenciais. Rode: source .env.sh{RST}")
    conn = psycopg2.connect(
        host=os.environ.get("NEXATLAS_DB_HOST", "jetstream.nexatlas.com"),
        port=os.environ.get("NEXATLAS_DB_PORT", "5433"),
        dbname=os.environ.get("NEXATLAS_DB_NAME", "jetstream"),
        user=os.environ.get("NEXATLAS_DB_USER", "ivansilverio"),
        password=os.environ["NEXATLAS_DB_PASSWORD"])
    with conn.cursor() as cur:
        cur.execute("SET search_path TO published, public;")
    conn.commit()
    return conn


def escolher_aeronaves(catalog):
    """Resolve a lista AERONAVES, ou escolhe um leque por teto se estiver vazia."""
    if AERONAVES:
        out = []
        for sel in AERONAVES:
            ac = find(catalog, sel)
            if ac:
                out.append(ac)
            else:
                print(f"  {RED}[aviso]{RST} aeronave '{sel}' não encontrada no catálogo")
        return out
    ordenado = sorted(catalog, key=lambda a: a.teto_ft)
    if len(ordenado) <= N_AUTO:
        return ordenado
    idxs = sorted({round(i * (len(ordenado) - 1) / (N_AUTO - 1)) for i in range(N_AUTO)})
    return [ordenado[i] for i in idxs]


def imprimir_vertices(perfil, ac) -> None:
    """Vértices do perfil com razão (fpm) e velocidade (kt) por trecho — só para
    verificação (igual ao nexatlas_cli.py::_print_vertical). Num trecho comprimido
    a velocidade cai bem abaixo da do banco. Se o perfil tiver vento calculado
    (perfil.segmentos_vento), mostra também GS/TAS e a componente cauda/proa."""
    print(f"    {DIM}[por trecho: razão fpm · velocidade kt"
          f"{' · vento GS/TAS/deriva' if perfil.segmentos_vento else ''} — p/ verificação]{RST}")
    vento_by_x0 = {s.x0_nm: s for s in perfil.segmentos_vento}
    prev_alt = None
    prev_x = None
    for v in perfil.vertices:
        seta = "  "
        vel = ""
        vento_txt = ""
        if prev_alt is not None:
            dalt = v.alt_ft - prev_alt
            dx = v.x_nm - prev_x
            if dalt > 1:
                seta = f"{GRN}↑{RST}"
                rate = ac.rate_ac_fpm
                spd = (dx * 60.0 * rate / dalt) if (rate > 0 and dx > 0) else 0.0
                vel = f"   {GRN}↑ {rate:.0f} fpm · {spd:.0f} kt{RST}"
            elif dalt < -1:
                seta = f"{DIM}↓{RST}"
                rate = ac.rate_dc_fpm
                spd = (dx * 60.0 * rate / (-dalt)) if (rate > 0 and dx > 0) else 0.0
                vel = f"   {DIM}↓ {rate:.0f} fpm · {spd:.0f} kt{RST}"
            else:
                seta = f"{DIM}={RST}"
                if v.x_nm - prev_x > 0.05:
                    vel = f"   {DIM}= {ac.speed_cruise_kt:.0f} kt{RST}"
            if dx > 1e-9:              # só busca vento p/ trechos com distância real
                seg = vento_by_x0.get(prev_x)
                if seg is not None:
                    vento_txt = (f"   {DIM}GS {seg.gs_kt:.0f} kt (TAS {seg.tas_kt:.0f}, "
                                 f"cauda {seg.comp_cauda_kt:+.0f}, deriva {seg.deriva_deg:+.1f}°){RST}")
        nome = v.nome or ""
        print(f"      {v.x_nm:6.1f} NM  {seta} {v.alt_ft:6.0f} ft  [{v.tipo:<8}] {nome}{vel}{vento_txt}")
        prev_alt = v.alt_ft
        prev_x = v.x_nm


def metricas(perfil, aeronave, ncorr):
    v = perfil.vertices
    total = v[-1].x_nm if v else 0.0
    tt = perfil.tempo_total_min or 1e-9
    piso = any(("cruzeiro elevado" in a) or ("terreno NÃO liberado" in a)
               for a in perfil.avisos)
    tt_v = perfil.tempo_total_vento_min
    return {
        "aeronave": aeronave.label,
        "teto_ft": round(aeronave.teto_ft),
        "razao_subida_fpm": round(aeronave.rate_ac_fpm),
        "dist_total_nm": round(total, 1),
        "cruzeiro_ft": round(perfil.cruzeiro_ft),
        "cruzeiro_nivelado": "sim" if perfil.alcancou_cruzeiro else "nao",
        "toc_nm": round(perfil.toc_nm, 1),
        "tod_nm": round(perfil.tod_nm, 1),
        "dist_subida_nm": round(perfil.subida.dist_nm, 1),
        "dist_cruzeiro_nm": round(perfil.cruzeiro.dist_nm, 1),
        "dist_descida_nm": round(perfil.descida.dist_nm, 1),
        "t_subida_min": round(perfil.subida.tempo_min, 1),
        "t_cruzeiro_min": round(perfil.cruzeiro.tempo_min, 1),
        "t_descida_min": round(perfil.descida.tempo_min, 1),
        "t_total_min": round(tt, 1),
        "pct_tempo_subindo": round(100 * perfil.subida.tempo_min / tt),
        "comb_total": (round(perfil.comb_total, 1) if perfil.comb_total is not None else None),
        "comb_unit": perfil.comb_unit or "",
        "n_corredores": ncorr,
        "piso_terreno_acionado": "sim" if piso else "nao",
        # -- com vento (TAREFA_vento.md passo 1) — None se Wind() indisponível --
        "t_total_vento_min": (round(tt_v, 1) if tt_v is not None else None),
        "diff_tempo_min": (round(tt_v - perfil.tempo_total_min, 1) if tt_v is not None else None),
        "comb_total_vento": (round(perfil.comb_total_vento, 1)
                             if perfil.comb_total_vento is not None else None),
        "diff_comb": (round(perfil.comb_total_vento - perfil.comb_total, 1)
                     if (perfil.comb_total_vento is not None and perfil.comb_total is not None)
                     else None),
        "avisos": " | ".join(perfil.avisos) if perfil.avisos else "",
    }


def main():
    print(f"\n{BLD}  Análise de comportamento vertical — matriz aeronaves × rotas{RST}")
    print("  Conectando ao jetstream...")
    conn = connect()
    loader = PostgisLoader(conn)
    catalog = load_from_db(conn)
    aeronaves = escolher_aeronaves(catalog)
    if not aeronaves:
        sys.exit(f"{RED}Nenhuma aeronave selecionada.{RST}")

    print(f"  {GRN}Aeronaves ({len(aeronaves)}):{RST} " +
          ", ".join(f"{a.label} (teto {a.teto_ft:.0f})" for a in aeronaves))
    print(f"  {GRN}Rotas ({len(ROTAS)}):{RST} " + ", ".join(f"{o}->{d}" for o, d in ROTAS))
    if SALVAR_GRAFICOS:
        os.makedirs(PASTA_SAIDA, exist_ok=True)

    gwo_cfg = GWOConfig(seed=42, n_iterations=200, n_wolves=30, max_hops=80)
    terreno = Terrain()          # reaproveitado (cache de tiles) em todos os casos
    vento = Wind()               # idem — cache de tiles de vento em todos os casos
    if vento.disponivel():
        print(f"  {GRN}Vento:{RST} CDN ok ({len(vento.meta.levels)} níveis, "
              f"{len(vento.meta.timestamps)} horários de previsão)")
    else:
        print(f"  {DIM}Vento indisponível ({vento.erro}); rodando com vento=0.{RST}")
    # Resolvida UMA VEZ (não por caso) — bateria reprodutível, ver HORA_PARTIDA_UTC acima.
    hora_partida = (parse_hora_utc(HORA_PARTIDA_UTC) if HORA_PARTIDA_UTC is not None
                    else time.time())
    print(f"  {GRN}Hora de partida (UTC):{RST} "
          f"{dt.datetime.fromtimestamp(hora_partida, dt.timezone.utc):%Y-%m-%d %H:%M}")
    linhas = []

    for (origin, dest) in ROTAS:
        print(f"\n{DIM}── {origin} → {dest} ─────────────────────────────{RST}")
        # V1 uma vez por rota (independe da aeronave)
        try:
            graph, meta = loader.build_subgraph(origin, dest,
                                                chart_radius_nm=60.0, link_radius_nm=30.0)
            result = plan_v1_route(graph, meta["origin_id"], meta["dest_id"], gwo_cfg)
        except Exception as e:
            print(f"  {RED}✗ V1 falhou ({origin}->{dest}): {e}{RST}")
            continue
        ncorr = sum(1 for e in getattr(result, "legs", []) if getattr(e, "is_corridor", False))
        # se a rota V1 não expõe legs com is_corridor, cai para os corredores do perfil
        # V3 para cada aeronave
        for ac in aeronaves:
            try:
                perfil = plan_from_v1(graph, result, ac, terreno, vento,
                                      hora_partida_utc=hora_partida)
            except Exception as e:
                print(f"  {RED}✗ V3 falhou ({ac.label}): {e}{RST}")
                continue
            nc = ncorr or len(perfil.diag.get("corredores_x", []))
            row = metricas(perfil, ac, nc)
            linhas.append(row)
            comb_txt = (f"{row['comb_total']:.1f} {row['comb_unit']}"
                        if row['comb_total'] is not None else "indisponível")
            vento_txt = (f" | vento {row['diff_tempo_min']:+.0f}′"
                        if row['diff_tempo_min'] is not None else "")
            print(f"  {CYN}{ac.label:<22}{RST} teto {row['teto_ft']:>6} | "
                  f"{row['dist_total_nm']:>6} NM | cruzeiro {row['cruzeiro_ft']:>6} ft "
                  f"({row['cruzeiro_nivelado']}) | sub {row['t_subida_min']:>4}′ "
                  f"cru {row['t_cruzeiro_min']:>4}′ des {row['t_descida_min']:>4}′ | "
                  f"{row['pct_tempo_subindo']:>3}% subindo | comb {comb_txt}{vento_txt}"
                  + (f" | {RED}piso terreno{RST}" if row['piso_terreno_acionado'] == 'sim' else ""))
            imprimir_vertices(perfil, ac)
            if SALVAR_GRAFICOS:
                safe = "".join(c if c.isalnum() else "_" for c in ac.label)[:20]
                png = os.path.join(PASTA_SAIDA, f"{origin}_{dest}__{safe}.png")
                try:
                    plot_vertical_profile(perfil, png, titulo=f"{ac.label}  {origin} → {dest}")
                except Exception as e:
                    print(f"    {DIM}(gráfico não gerado: {e}){RST}")

    conn.close()

    if not linhas:
        sys.exit(f"{RED}Nenhum caso rodou com sucesso.{RST}")

    # ---- CSV completo ----
    with open(CSV_SAIDA, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
        w.writeheader()
        w.writerows(linhas)

    # ---- tabela-resumo no terminal (ordenada por distância) ----
    print(f"\n{BLD}  RESUMO (ordenado por distância){RST}")
    hdr = (f"  {'Aeronave':<22}{'teto':>7}{'distNM':>8}{'cruz.ft':>9}{'niv':>5}"
           f"{'t.sub':>7}{'t.cru':>7}{'t.des':>7}{'%sub':>6}{'corr':>5}{'terr':>5}"
           f"{'combustível':>14}{'vento':>10}")
    print(BLD + hdr + RST)
    print("  " + "-" * (len(hdr) - 2))
    for r in sorted(linhas, key=lambda x: (x["dist_total_nm"], x["teto_ft"])):
        comb_str = (f"{r['comb_total']:.1f} {r['comb_unit']}"
                    if r['comb_total'] is not None else "indisponível")
        vento_str = f"{r['diff_tempo_min']:+.0f}′" if r['diff_tempo_min'] is not None else "-"
        print(f"  {r['aeronave']:<22}{r['teto_ft']:>7}{r['dist_total_nm']:>8}"
              f"{r['cruzeiro_ft']:>9}{r['cruzeiro_nivelado']:>5}"
              f"{r['t_subida_min']:>7}{r['t_cruzeiro_min']:>7}{r['t_descida_min']:>7}"
              f"{r['pct_tempo_subindo']:>5}%{r['n_corredores']:>5}"
              f"{('sim' if r['piso_terreno_acionado']=='sim' else '-'):>5}"
              f"{comb_str:>14}{vento_str:>10}")

    print(f"\n  {GRN}✓ {len(linhas)} casos.{RST} CSV: {os.path.abspath(CSV_SAIDA)}"
          + (f" | gráficos em {os.path.abspath(PASTA_SAIDA)}/" if SALVAR_GRAFICOS else ""))
    print(f"  {DIM}Dica: '%sub' alto com 't.cru' baixo = subiu mais do que cruzou "
          f"(candidato a cruzeiro alto demais).{RST}\n")


if __name__ == "__main__":
    main()
