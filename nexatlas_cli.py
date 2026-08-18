#!/usr/bin/env python3
"""CLI interativo do Motor de Rotas V1 — NexAtlas (esquema published).

Credenciais via variáveis de ambiente (source .env.sh):
    NEXATLAS_DB_HOST=jetstream.nexatlas.com
    NEXATLAS_DB_PORT=5433
    NEXATLAS_DB_NAME=jetstream
    NEXATLAS_DB_USER=ivansilverio
    NEXATLAS_DB_PASSWORD=********
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import textwrap

try:
    import readline as _rl
    _HAS_READLINE = True
except ImportError:
    _HAS_READLINE = False

try:
    import psycopg2
except ImportError:
    sys.exit("Instale o driver: pip install psycopg2-binary")

from nexatlas_router.db import PostgisLoader
from nexatlas_router.gwo import GWOConfig
from nexatlas_router.v1 import plan_v1_route
try:
    from nexatlas_router.plot_route import plot_v1_combined
    _HAS_LATERAL_PLOT = True
except Exception:
    _HAS_LATERAL_PLOT = False

# Camada V3 (vertical) — opcional: se pygeomag não estiver instalado, a V1 segue.
try:
    from nexatlas_router.vertical import (Terrain, Wind, parse_hora_utc,
                                          load_from_db as load_aircraft,
                                          find as find_aircraft, plan_from_v1,
                                          plot_vertical_profile)
    _HAS_V3 = True
except Exception:
    _HAS_V3 = False

# ── ANSI ──────────────────────────────────────────────────────────────────────
RST = "\033[0m"; BLD = "\033[1m"; DIM = "\033[2m"
GRN = "\033[32m"; CYN = "\033[36m"; RED = "\033[31m"


def _hr(ch: str = "─", width: int = 66) -> str:
    return DIM + ch * width + RST


def _open_image(path: str) -> None:
    try:
        win = subprocess.check_output(
            ["wslpath", "-w", os.path.abspath(path)],
            stderr=subprocess.DEVNULL).decode().strip()
        subprocess.Popen(["explorer.exe", win],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    except Exception:
        pass
    try:
        subprocess.Popen(["xdg-open", path],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _connect() -> "psycopg2.extensions.connection":
    # Defaults do novo banco published (jetstream); ainda sobrescrevíveis por env.
    required = ["NEXATLAS_DB_PASSWORD"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"\n{RED}Variáveis de ambiente ausentes: {', '.join(missing)}{RST}")
        print(f"{DIM}Execute: source .env.sh{RST}\n")
        sys.exit(1)
    conn = psycopg2.connect(
        host=os.environ.get("NEXATLAS_DB_HOST", "jetstream.nexatlas.com"),
        port=os.environ.get("NEXATLAS_DB_PORT", "5433"),
        dbname=os.environ.get("NEXATLAS_DB_NAME", "jetstream"),
        user=os.environ.get("NEXATLAS_DB_USER", "ivansilverio"),
        password=os.environ["NEXATLAS_DB_PASSWORD"],
    )
    # Garante que objetos não-qualificados também resolvam no esquema published.
    with conn.cursor() as cur:
        cur.execute("SET search_path TO published, public;")
    conn.commit()
    return conn


def _print_route(origin: str, dest: str, result) -> None:
    points = result.points
    corridors = result.corridors_used          # [{name, is_mandatory}]
    src = result.meta.get("route_source", "dijkstra")
    direct_nm = result.direct_distance_nm
    total_nm = result.total_distance_nm
    delta = total_nm - direct_nm

    print(); print(_hr("═"))
    print(f"{BLD}  ROTA VFR  {CYN}{origin}{RST}{BLD} → {CYN}{dest}{RST}")
    print(_hr("═")); print()

    # rota trecho a trecho — formato dos casos de referência:
    #   ORIGEM -> DESTINO: CORREDOR   (ou DIRETO quando não há corredor REA)
    print(f"  {BLD}Rota (trecho a trecho):{RST}")
    for lg in result.legs:
        if lg["corridor"] == "DIRETO":
            label = f"{DIM}DIRETO{RST}"
        else:
            color = GRN if lg["is_mandatory"] else CYN
            label = f"{color}{lg['corridor']}{RST}"
        print(f"    {lg['from']} {DIM}->{RST} {lg['to']}: {label}")
    print()

    # array JSON
    arr = [{"seq": i, "id": p["id"], "name": p["name"], "kind": p["kind"],
            "lat": p["lat"], "lon": p["lon"], "chart": p.get("chart")}
           for i, p in enumerate(points, 1)]
    raw = json.dumps(arr, ensure_ascii=False, indent=2)
    print(DIM + "  Pontos (JSON):" + RST)
    print(DIM + "\n".join("    " + ln for ln in raw.splitlines()) + RST)
    print()

    # distâncias
    sign = "+" if delta >= 0 else ""
    print(f"  {BLD}Distância direta :{RST} {direct_nm:.1f} NM")
    print(f"  {BLD}Distância da rota:{RST} {total_nm:.1f} NM  "
          f"{DIM}({sign}{delta:.1f} NM sobre a direta){RST}")
    if src == "dijkstra-fase":
        metodo = "Dijkstra com estado de fase (exato)"
    elif src == "direto-preferido":
        metodo = "Direta preferida sobre a malha (coerência geométrica)"
    else:
        metodo = "Dijkstra (caminho mínimo exato)"
    print(f"  {DIM}Método: {metodo}{RST}")
    print()

    # corredores REA usados, com obrigatoriedade [Obrigatório]/[Opcional]
    if corridors:
        print(f"  {GRN}{BLD}✓ Corredores REA utilizados:{RST}")
        for c in corridors:
            if c["is_mandatory"]:
                tag = f"{RED}[Obrigatório]{RST}"
            else:
                tag = f"{CYN}[Opcional]{RST}"
            print(f"    • {BLD}{c['name']}{RST}  {tag}")
    else:
        print(f"  {DIM}ℹ  Nenhum corredor REA relevante — rota direta autorizada.{RST}")
    print()

    # TAREFA_portoes.md: portão obrigatório do documento prevalece sobre a
    # coerência (nunca derruba a rota), mas a colisão é sinalizada aqui.
    colisao = result.meta.get("colisao_portao_coerencia")
    if colisao:
        lados = "/".join(l for l in ("entrada", "saida") if colisao.get(l))
        print(f"  {RED}{BLD}⚠ Colisão portão × coerência ({lados}):{RST} "
              f"{DIM}o portão obrigatório do documento exige aqui um trecho com "
              f"retrocesso ou curva acentuada — mantido (o portão prevalece), "
              f"sinalizado para revisão.{RST}")
        print()

    # motivo
    print(DIM + textwrap.fill(result.reason, width=62,
                              initial_indent="  ", subsequent_indent="  ") + RST)
    print()

    # alternativas
    alternatives = result.meta.get("alternatives", [])
    if alternatives:
        print(f"  {BLD}Próximas melhores rotas (alternativas):{RST}")
        for i, alt in enumerate(alternatives, 1):
            ov = alt["overhead_nm"]
            ov_str = f"+{ov:.1f}" if ov >= 0 else f"{ov:.1f}"
            cors = ", ".join(
                f"{c['name']}{'*' if c['is_mandatory'] else ''}"
                for c in alt["corridors_used"]) or "DIRETO"
            print(f"    {i}. {BLD}{alt['total_distance_nm']:.1f} NM{RST} "
                  f"{DIM}({ov_str}){RST}  {DIM}[{cors}] "
                  f"({alt['n_points']} pontos){RST}")
            seq = " → ".join(p["name"] for p in alt["points"])
            print(DIM + textwrap.fill(seq, width=58, initial_indent="       ",
                                      subsequent_indent="       ") + RST)
        print()


def _select_aircraft(catalog):
    """Lista as aeronaves utilizáveis e lê a escolha (índice ou ICAO). Enter = pular V3."""
    if not catalog:
        print(f"  {DIM}Nenhuma aeronave com performance completa em aircraft_models — V3 desativada.{RST}")
        return None
    print(f"\n  {BLD}Aeronaves disponíveis (performance completa):{RST}")
    for i, a in enumerate(catalog, 1):
        print(f"    {CYN}{i:>2}{RST} {a.icao:<6} {DIM}{a.model}{RST}  "
              f"{DIM}(teto {a.teto_ft:.0f}ft, cruz {a.speed_cruise_kt:.0f}kt){RST}")
    try:
        sel = input(f"  {BLD}Aeronave [nº ou ICAO, Enter p/ pular]:{RST} ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not sel:
        return None
    if sel.isdigit() and 1 <= int(sel) <= len(catalog):
        return catalog[int(sel) - 1]
    return find_aircraft(catalog, sel)


def _print_vertical(perfil, ac=None) -> None:
    print(); print(_hr())
    print(f"  {BLD}Perfil vertical (V3) — {CYN}{perfil.aeronave}{RST}")
    print(_hr())
    print(f"  {BLD}Cruzeiro:{RST} {perfil.cruzeiro_ft:.0f} ft"
          f"{'' if perfil.alcancou_cruzeiro else DIM + '  (rota curta: sem cruzeiro nivelado)' + RST}")
    print(f"  {DIM}Elevação origem/destino: {perfil.origem_elev_ft:.0f} / {perfil.destino_elev_ft:.0f} ft{RST}")
    print(f"  {DIM}{perfil.diag.get('fonte_cruzeiro','')}{RST}")
    print(f"  {BLD}Tempo:{RST} subida {perfil.subida.tempo_min:.0f} min + "
          f"cruzeiro {perfil.cruzeiro.tempo_min:.0f} min + descida {perfil.descida.tempo_min:.0f} min "
          f"= {BLD}{perfil.tempo_total_min:.0f} min{RST}")
    if perfil.comb_total is not None:
        tipo = f"  {DIM}[{perfil.fuel_type}]{RST}" if perfil.fuel_type else ""
        print(f"  {BLD}Combustível:{RST} {perfil.comb_total:.1f} {perfil.comb_unit} "
              f"{DIM}(subida {perfil.comb_subida:.1f} + cruzeiro {perfil.comb_cruzeiro:.1f} + "
              f"descida {perfil.comb_descida:.1f} {perfil.comb_unit}){RST}{tipo}")
    else:
        print(f"  {BLD}Combustível:{RST} {DIM}— (dados de combustível indisponíveis){RST}")
    if perfil.subida_vento is not None:
        quando = dt.datetime.fromtimestamp(perfil.hora_partida_utc, dt.timezone.utc)
        tt_v, tt_s = perfil.tempo_total_vento_min, perfil.tempo_total_min
        diff_t = tt_v - tt_s
        print(f"  {DIM}Partida assumida: {quando:%Y-%m-%d %H:%M} UTC{RST}")
        print(f"  {BLD}Tempo com vento:{RST} {tt_v:.0f} min  "
              f"{DIM}({diff_t:+.0f} min vs sem vento){RST}")
        if perfil.comb_total_vento is not None:
            diff_c = perfil.comb_total_vento - perfil.comb_total
            print(f"  {BLD}Combustível com vento:{RST} {perfil.comb_total_vento:.1f} {perfil.comb_unit}  "
                  f"{DIM}({diff_c:+.1f} {perfil.comb_unit} vs sem vento){RST}")
    if perfil.avisos:
        for a in perfil.avisos:
            print(f"  {RED}⚠{RST}  {DIM}{a}{RST}")
    print(f"  {DIM}TOC {perfil.toc_nm:.1f} NM  ·  TOD {perfil.tod_nm:.1f} NM  "
          f"@ {perfil.cruzeiro_ft:.0f} ft{RST}\n")
    print(f"  {BLD}Perfil (pontos reais + virtuais):{RST}"
          f"   {DIM}[por trecho: razão fpm · velocidade kt"
          f"{' · vento GS/TAS/deriva' if perfil.segmentos_vento else ''} — p/ verificação]{RST}")
    vento_by_x0 = {s.x0_nm: s for s in perfil.segmentos_vento}
    ingreme = perfil.descida_ingreme_nm

    def _e_ingreme(x0, x1):
        mid = (x0 + x1) / 2.0
        return any(a - 1e-6 <= mid <= b + 1e-6 for a, b in ingreme)

    prev_alt = None
    prev_x = None
    for v in perfil.vertices:
        seta = "  "
        vel = ""
        vento_txt = ""
        if prev_alt is not None:
            dalt = v.alt_ft - prev_alt
            dx = v.x_nm - prev_x
            # razão (fpm) DERIVADA do trecho real (dx/dalt), com a velocidade do
            # banco FIXA — nunca o contrário (razão fixa/velocidade derivada):
            # em trechos íngremes (não cabe na razão do banco), a geometria foi
            # construída com a velocidade do banco e a distância disponível da
            # perna, então a razão implícita é a REAL (necessária), acima da
            # razão nominal da aeronave — mostrar a razão nominal ali estaria
            # errado (mesmo bug do aviso antigo, mas no terminal).
            cor_ingreme = RED if _e_ingreme(prev_x, v.x_nm) else None
            if dalt > 1:
                seta = f"{GRN}↑{RST}"
                spd = ac.speed_ac_kt if ac else 0.0
                rate = (dalt * spd / (dx * 60.0)) if (spd > 0 and dx > 0) else 0.0
                cor = cor_ingreme or GRN
                vel = f"   {cor}↑ {rate:.0f} fpm · {spd:.0f} kt{RST}"
            elif dalt < -1:
                seta = f"{DIM}↓{RST}"
                spd = ac.speed_dc_kt if ac else 0.0
                rate = (-dalt * spd / (dx * 60.0)) if (spd > 0 and dx > 0) else 0.0
                cor = cor_ingreme or DIM
                vel = f"   {cor}↓ {rate:.0f} fpm · {spd:.0f} kt{RST}"
            else:
                seta = f"{DIM}={RST}"
                if ac and v.x_nm - prev_x > 0.05:
                    vel = f"   {DIM}= {ac.speed_cruise_kt:.0f} kt{RST}"
            if dx > 1e-9:              # só busca vento p/ trechos com distância real
                seg = vento_by_x0.get(prev_x)
                if seg is not None:
                    vento_txt = (f"   {DIM}GS {seg.gs_kt:.0f} kt (TAS {seg.tas_kt:.0f}, "
                                 f"cauda {seg.comp_cauda_kt:+.0f}, deriva {seg.deriva_deg:+.1f}°){RST}")
        if v.real:
            tag = GRN if v.tipo == "corredor" else CYN
            nome = v.nome or ""
        else:
            tag = DIM
            nome = (f"{DIM}(TOC/TOD){RST}" if v.tipo in ("toc", "tod")
                    else f"{DIM}(ponto virtual — atinge a altitude aqui){RST}")
        print(f"    {v.x_nm:6.1f} NM  {seta} {v.alt_ft:6.0f} ft  [{tag}{v.tipo:<8}{RST}] {nome}{vel}{vento_txt}")
        prev_alt = v.alt_ft
        prev_x = v.x_nm
    print()


def _setup_autocomplete(icao_list: list[str]) -> None:
    if not _HAS_READLINE:
        return
    sorted_list = sorted(icao_list)
    _matches: list[str] = []
    def _completer(text: str, state: int):
        nonlocal _matches
        if state == 0:
            prefix = text.upper()
            _matches = [ic for ic in sorted_list if ic.startswith(prefix)]
        return _matches[state] if state < len(_matches) else None
    def _show_matches(substitution, matches, longest):
        print()
        for m in sorted(matches)[:20]:
            print(f"    {CYN}{m}{RST}")
        if len(matches) > 20:
            print(f"    {DIM}... e mais {len(matches) - 20} aeródromos{RST}")
        print()
    _rl.set_completer(_completer)
    _rl.set_completer_delims("")
    _rl.set_completion_display_matches_hook(_show_matches)
    _rl.parse_and_bind("tab: complete")


def main() -> None:
    print(); print(_hr("═"))
    print(f"{BLD}  NexAtlas · Motor de Rotas V1 — CLI Interativo (published){RST}")
    print(_hr("═")); print()

    print("  Conectando ao banco de dados...")
    try:
        conn = _connect()
        host = os.environ.get("NEXATLAS_DB_HOST", "jetstream.nexatlas.com")
        port = os.environ.get("NEXATLAS_DB_PORT", "5433")
        print(f"  {GRN}✓ Banco:{RST} {host}:{port} (schema published)")
    except Exception as e:
        print(f"  {RED}✗ Erro de conexão: {e}{RST}")
        sys.exit(1)

    # Loader resolve aeródromos diretamente de published.adhps.geom (sem resolver externo).
    loader = PostgisLoader(conn)
    try:
        _icaos = loader.list_icaos()
        _setup_autocomplete(_icaos)
        print(f"  {GRN}✓ Autocomplete:{RST} {len(_icaos)} aeródromos (published.adhps)")
    except Exception:
        pass

    print(f"  {DIM}Coordenadas: published.adhps.geom (resolvido pelo loader){RST}")
    hint = "Tab = sugestões de ICAO  |  " if _HAS_READLINE else ""
    print(f"  {DIM}{hint}Digite 'q' ou Ctrl+C para sair.{RST}")
    print()
    print(f"  {DIM}Exemplos de rotas (casos de referência REA):{RST}")
    for orig, dest, desc in [
        ("SBBH", "SBMT", "Belo Horizonte → Campo de Marte (REA saída+chegada)"),
        ("SBPA", "SBFL", "Porto Alegre → Florianópolis"),
        ("SBLO", "SBTG", "Londrina → Três Lagoas (REA só na saída)"),
        ("SBMO", "SBRF", "Maceió → Recife (REA só na chegada)"),
        ("SBHT", "SBPJ", "Altamira → Palmas (nenhuma REA — direto)"),
    ]:
        print(f"    {CYN}{orig} → {dest}{RST}  {DIM}{desc}{RST}")

    # V3 (opcional): escolhe a aeronave da sessão (aircraft_models).
    aircraft = None
    if _HAS_V3:
        try:
            aircraft = _select_aircraft(load_aircraft(conn))
            if aircraft:
                print(f"  {GRN}✓ Aeronave:{RST} {aircraft.label}")
        except Exception as e:
            print(f"  {DIM}V3 indisponível ({e}); seguindo só com a rota lateral.{RST}")

    # max_hops=80 para rotas longas com múltiplas TMAs encadeadas.
    gwo_cfg = GWOConfig(seed=42, n_iterations=200, n_wolves=30, max_hops=80)

    while True:
        print(); print(_hr())
        try:
            origin = input(f"  {BLD}Origem  [ICAO]:{RST} ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {DIM}Encerrando.{RST}\n"); break
        if origin in ("Q", "SAIR", "EXIT", ""):
            print(f"\n  {DIM}Encerrando.{RST}\n"); break
        try:
            dest = input(f"  {BLD}Destino [ICAO]:{RST} ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {DIM}Encerrando.{RST}\n"); break
        if dest in ("Q", "SAIR", "EXIT", ""):
            print(f"\n  {DIM}Encerrando.{RST}\n"); break

        print(f"\n  {DIM}Calculando rota {origin} → {dest}...{RST}")
        try:
            graph, meta = loader.build_subgraph(
                origin, dest, chart_radius_nm=60.0, link_radius_nm=30.0)
        except LookupError as e:
            print(f"\n  {RED}✗ Aeródromo não encontrado:{RST} {e}\n"); continue
        except Exception as e:
            print(f"\n  {RED}✗ Erro ao construir o subgrafo:{RST} {e}\n"); continue

        n_real = sum(1 for es in graph.adj.values() for e in es if not e.synthetic)
        print(f"  {DIM}Cartas: {meta['charts']} | {graph.n} nós | "
              f"{n_real} arestas de corredor REA{RST}")

        try:
            result = plan_v1_route(graph, meta["origin_id"], meta["dest_id"], gwo_cfg,
                                   origin_gate_ids=meta.get("origin_gate_ids"),
                                   dest_gate_ids=meta.get("dest_gate_ids"))
        except Exception as e:
            print(f"\n  {RED}✗ Erro no otimizador:{RST} {e}\n"); continue

        _print_route(origin, dest, result)

        # V3: perfil vertical sobre a rota lateral (terreno/vento do CDN, injetados).
        if _HAS_V3 and aircraft is not None:
            hora_partida = None
            try:
                hora_raw = input(f"  {BLD}Hora de partida (UTC) "
                                 f"[ex.: 15/08/2026 14:30 · 14:30 (hoje) · Enter = agora]:{RST} ").strip()
            except (EOFError, KeyboardInterrupt):
                hora_raw = ""
            if hora_raw:
                try:
                    hora_partida = parse_hora_utc(hora_raw)
                except Exception as e:
                    print(f"  {RED}✗ Hora inválida ({e}); usando agora.{RST}")
            try:
                perfil = plan_from_v1(graph, result, aircraft, Terrain(), Wind(),
                                      hora_partida_utc=hora_partida)
                _print_vertical(perfil, aircraft)
                perfil_png = f"{origin}_{dest}_perfil.png"
                try:
                    plot_vertical_profile(perfil, perfil_png,
                                          titulo=f"Perfil vertical  {origin} → {dest}")
                    print(f"  {GRN}✓ Perfil salvo:{RST} {os.path.abspath(perfil_png)}")
                    _open_image(perfil_png)
                except Exception as e:
                    print(f"  {DIM}(gráfico do perfil não gerado: {e}){RST}")
            except Exception as e:
                print(f"  {RED}✗ Perfil vertical indisponível:{RST} {e} "
                      f"{DIM}(terreno/CDN?){RST}")

        plot_path = f"{origin}_{dest}.png"
        if _HAS_LATERAL_PLOT:
            try:
                plot_v1_combined(graph, result, plot_path,
                                 title=f"Malha Aérea VFR — {origin} → {dest}")
                print(f"  {GRN}✓ Mapa salvo:{RST} {os.path.abspath(plot_path)}")
                _open_image(plot_path)
            except Exception as e:
                print(f"  {RED}✗ Erro na plotagem:{RST} {e}")
        else:
            print(f"  {DIM}(mapa lateral indisponível: plot_v1_combined não encontrado "
                  f"em plot_route.py){RST}")
        print()

    conn.close()


if __name__ == "__main__":
    main()
