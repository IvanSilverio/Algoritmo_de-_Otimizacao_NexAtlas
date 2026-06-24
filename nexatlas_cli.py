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
from nexatlas_router.plot_route import plot_v1_route

# ── ANSI ──────────────────────────────────────────────────────────────────────
RST = "\033[0m"; BLD = "\033[1m"; DIM = "\033[2m"
GRN = "\033[32m"; CYN = "\033[36m"; YLW = "\033[33m"; RED = "\033[31m"; MGN = "\033[35m"


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
    iters = result.meta.get("iterations_run", "?")
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
    print(f"  {DIM}Convergência: {iters} iterações GWO{RST}")
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

    # motivo
    print(DIM + textwrap.fill(result.reason, width=62,
                              initial_indent="  ", subsequent_indent="  ") + RST)
    print()

    # alternativas
    alternatives = result.meta.get("alternatives", [])
    if alternatives:
        print(f"  {BLD}Alternativas avaliadas (descartadas):{RST}")
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
            result = plan_v1_route(graph, meta["origin_id"], meta["dest_id"], gwo_cfg)
        except Exception as e:
            print(f"\n  {RED}✗ Erro no otimizador:{RST} {e}\n"); continue

        _print_route(origin, dest, result)

        plot_path = f"rota_{origin}_{dest}.png"
        try:
            plot_v1_route(graph, result, plot_path,
                          title=f"Malha Aérea VFR — {origin} → {dest}")
            print(f"  {GRN}✓ Mapa salvo:{RST} {os.path.abspath(plot_path)}")
            _open_image(plot_path)
        except Exception as e:
            print(f"  {RED}✗ Erro na plotagem:{RST} {e}")
        print()

    conn.close()


if __name__ == "__main__":
    main()