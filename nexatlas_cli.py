#!/usr/bin/env python3
"""CLI interativo do Motor de Rotas V1 — NexAtlas.

Uso:
    cd ~/NexAtlas/Implementacao_Algoritmo/nexatlas_v1/nexatlas_v1
    source .venv/bin/activate
    source .env.sh
    python nexatlas_cli.py
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
from nexatlas_router.resolver import CsvResolver

# ── ANSI ──────────────────────────────────────────────────────────────────────
RST = "\033[0m"
BLD = "\033[1m"
DIM = "\033[2m"
GRN = "\033[32m"
CYN = "\033[36m"
YLW = "\033[33m"
RED = "\033[31m"
MGN = "\033[35m"

# ── caminhos ──────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(_HERE, "data", "aerodromos_br_ourairports.csv")
AERODROME_COORD_SQL = None   # trocar por AdhpsGeomResolver quando geom existir


# ─────────────────────────────────────────────────────────────────────────────
def _hr(ch: str = "─", width: int = 66) -> str:
    return DIM + ch * width + RST


def _open_image(path: str) -> None:
    """Tenta abrir a imagem PNG no visualizador do sistema (WSL/Linux)."""
    try:
        win = subprocess.check_output(
            ["wslpath", "-w", os.path.abspath(path)],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        subprocess.Popen(
            ["explorer.exe", win],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return
    except Exception:
        pass
    try:
        subprocess.Popen(
            ["xdg-open", path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def _connect() -> "psycopg2.extensions.connection":
    required = ["NEXATLAS_DB_HOST", "NEXATLAS_DB_NAME",
                 "NEXATLAS_DB_USER", "NEXATLAS_DB_PASSWORD"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"\n{RED}Variáveis de ambiente ausentes: {', '.join(missing)}{RST}")
        print(f"{DIM}Execute: source .env.sh{RST}\n")
        sys.exit(1)
    return psycopg2.connect(
        host=os.environ["NEXATLAS_DB_HOST"],
        port=os.environ.get("NEXATLAS_DB_PORT", "5433"),
        dbname=os.environ["NEXATLAS_DB_NAME"],
        user=os.environ["NEXATLAS_DB_USER"],
        password=os.environ["NEXATLAS_DB_PASSWORD"],
    )


# ─────────────────────────────────────────────────────────────────────────────
def _print_route(origin: str, dest: str, result) -> None:
    points      = result.points
    corridors   = result.corridors_used
    dep_req     = result.meta.get("departure_corridor_required", False)
    arr_req     = result.meta.get("arrival_corridor_required", False)
    dep_cors    = set(result.meta.get("departure_mandatory_corridors", []))
    arr_cors    = set(result.meta.get("arrival_mandatory_corridors", []))
    iters       = result.meta.get("iterations_run", "?")
    direct_nm   = result.direct_distance_nm
    total_nm    = result.total_distance_nm
    delta       = total_nm - direct_nm

    # ── cabeçalho ─────────────────────────────────────────────────────────
    print()
    print(_hr("═"))
    print(f"{BLD}  ROTA VFR  {CYN}{origin}{RST}{BLD} → {CYN}{dest}{RST}")
    print(_hr("═"))
    print()

    # ── tabela de pontos ──────────────────────────────────────────────────
    W = [4, 28, 11, 12, 12]   # larguras das colunas
    SEP = "  "

    def _row(*cells):
        return SEP + SEP.join(str(c).ljust(w) for c, w in zip(cells, W))

    header = _row("#", "Nome", "Tipo", "Latitude", "Longitude")
    print(BLD + header + RST)
    print(DIM + "  " + "─" * (sum(W) + SEP.__len__() * len(W)) + RST)

    for i, p in enumerate(points, 1):
        tipo = "Aeródromo" if p["kind"] == "aerodrome" else "Waypoint"
        cor  = YLW if p["kind"] == "aerodrome" else RST
        print(cor + _row(i, p["name"][:28], tipo,
                         f"{p['lat']:+.5f}°", f"{p['lon']:+.5f}°") + RST)

    print()

    # ── array JSON ────────────────────────────────────────────────────────
    arr = [
        {
            "seq":   i,
            "id":    p["id"],
            "name":  p["name"],
            "kind":  p["kind"],
            "lat":   p["lat"],
            "lon":   p["lon"],
            "chart": p.get("chart"),
        }
        for i, p in enumerate(points, 1)
    ]
    raw = json.dumps(arr, ensure_ascii=False, indent=2)
    indented = "\n".join("    " + ln for ln in raw.splitlines())
    print(DIM + "  Pontos (JSON):" + RST)
    print(DIM + indented + RST)
    print()

    # ── distâncias ────────────────────────────────────────────────────────
    sign = "+" if delta >= 0 else ""
    print(f"  {BLD}Distância direta :{RST} {direct_nm:.1f} NM")
    print(f"  {BLD}Distância da rota:{RST} {total_nm:.1f} NM  "
          f"{DIM}({sign}{delta:.1f} NM sobre a direta){RST}")
    print(f"  {DIM}Convergência: {iters} iterações GWO{RST}")
    print()

    # ── corredores visuais ────────────────────────────────────────────────
    if corridors:
        print(f"  {GRN}{BLD}✓ Corredores visuais utilizados:{RST}")
        for c in corridors:
            tags: list[str] = []
            if c in dep_cors:
                tags.append(f"{YLW}OBRIGATÓRIO na saída{RST}")
            if c in arr_cors:
                tags.append(f"{YLW}OBRIGATÓRIO na chegada{RST}")
            if not tags:
                tag_str = f"  {DIM}(passagem optativa nesta rota){RST}"
            else:
                tag_str = "  — " + " | ".join(tags)
            print(f"    • {BLD}{c}{RST}{tag_str}")

        # lista todos os corredores que existem no raio, mesmo os não usados
        all_mandatory = dep_cors | arr_cors
        unused_mandatory = all_mandatory - set(corridors)
        if unused_mandatory:
            print()
            print(f"  {RED}⚠  Corredor(es) obrigatório(s) NÃO percorridos: "
                  f"{', '.join(sorted(unused_mandatory))}{RST}")
            print(f"  {DIM}(penalidade μ aplicada no fitness){RST}")

    elif dep_req or arr_req:
        print(f"  {RED}⚠  Existem corredores aplicáveis mas NENHUM foi utilizado.{RST}")
        print(f"  {DIM}Verifique convergência ou parâmetros de penalidade.{RST}")
    else:
        print(f"  {DIM}ℹ  Nenhum corredor visual aplicável — rota direta autorizada.{RST}")

    print()

    # ── motivo ─────────────────────────────────────────────────────────────
    wrapped = textwrap.fill(result.reason, width=62,
                            initial_indent="  ", subsequent_indent="  ")
    print(DIM + wrapped + RST)
    print()


# ─────────────────────────────────────────────────────────────────────────────
def _setup_autocomplete(resolver: CsvResolver) -> None:
    """Configura Tab-completion de ICAO com nome do aeródromo no terminal."""
    if not _HAS_READLINE:
        return

    # Tabela ICAO -> nome (vem do CsvResolver: table[icao] = (name, lon, lat))
    icao_list = sorted(resolver.table.keys())
    names: dict[str, str] = {k: v[0] for k, v in resolver.table.items()}
    _matches: list[str] = []

    def _completer(text: str, state: int) -> str | None:
        nonlocal _matches
        if state == 0:
            prefix = text.upper()
            _matches = [ic for ic in icao_list if ic.startswith(prefix)]
        return _matches[state] if state < len(_matches) else None

    def _show_matches(substitution: str, matches: list[str], longest: int) -> None:
        print()  # sai da linha do prompt
        display = sorted(matches)[:20]
        for m in display:
            print(f"    {CYN}{m:<8}{RST} {DIM}{names.get(m, '')}{RST}")
        if len(matches) > 20:
            print(f"    {DIM}... e mais {len(matches) - 20} aeródromos{RST}")
        print()  # linha em branco antes do prompt ser reimpresso pelo readline

    _rl.set_completer(_completer)
    _rl.set_completer_delims("")          # linha inteira = 1 token
    _rl.set_completion_display_matches_hook(_show_matches)
    _rl.parse_and_bind("tab: complete")


# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    print()
    print(_hr("═"))
    print(f"{BLD}  NexAtlas · Motor de Rotas V1 — CLI Interativo{RST}")
    print(_hr("═"))
    print()

    print(f"  Conectando ao banco de dados...")
    try:
        conn = _connect()
        host = os.environ["NEXATLAS_DB_HOST"]
        port = os.environ.get("NEXATLAS_DB_PORT", "5433")
        print(f"  {GRN}✓ Banco:{RST} {host}:{port}")
    except Exception as e:
        print(f"  {RED}✗ Erro de conexão: {e}{RST}")
        sys.exit(1)

    resolver = CsvResolver(DEFAULT_CSV)
    _setup_autocomplete(resolver)
    print(f"  {DIM}Coordenadas: OurAirports CSV (provisório — fonte pública){RST}")
    hint = "Tab = sugestões de ICAO  |  " if _HAS_READLINE else ""
    print(f"  {DIM}{hint}Digite 'q' ou Ctrl+C para sair.{RST}")
    print()
    print(f"  {DIM}Exemplos de rotas:{RST}")
    _EXEMPLOS = [
        ("SBMT", "SBJD", "Campo de Marte → Jundiaí"),
        ("SBMT", "SBKP", "Campo de Marte → Viracopos"),
        ("SBSP", "SBJD", "Congonhas → Jundiaí"),
        ("SBJD", "SBMT", "Jundiaí → Campo de Marte"),
        ("SBSP", "SBMT", "Congonhas → Campo de Marte"),
        ("SBGR", "SBMT", "Guarulhos → Campo de Marte"),
    ]
    for orig, dest, desc in _EXEMPLOS:
        print(f"    {CYN}{orig} → {dest}{RST}  {DIM}{desc}{RST}")

    loader = PostgisLoader(conn, aerodrome_coord_sql=AERODROME_COORD_SQL, schema="v2")
    gwo_cfg = GWOConfig(seed=42, n_iterations=200, n_wolves=30, max_hops=40)

    while True:
        print()
        print(_hr())
        try:
            origin = input(f"  {BLD}Origem  [ICAO]:{RST} ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {DIM}Encerrando.{RST}\n")
            break

        if origin in ("Q", "SAIR", "EXIT", ""):
            print(f"\n  {DIM}Encerrando.{RST}\n")
            break

        try:
            dest = input(f"  {BLD}Destino [ICAO]:{RST} ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print(f"\n  {DIM}Encerrando.{RST}\n")
            break

        if dest in ("Q", "SAIR", "EXIT", ""):
            print(f"\n  {DIM}Encerrando.{RST}\n")
            break

        print(f"\n  {DIM}Calculando rota {origin} → {dest}...{RST}")

        try:
            graph, meta = loader.build_subgraph(
                origin, dest,
                resolver=resolver,
                chart_radius_nm=60.0,
                link_radius_nm=30.0,
            )
        except LookupError as e:
            print(f"\n  {RED}✗ Aeródromo não encontrado:{RST} {e}\n")
            continue
        except Exception as e:
            print(f"\n  {RED}✗ Erro ao construir o subgrafo:{RST} {e}\n")
            continue

        n_real = sum(1 for es in graph.adj.values()
                     for e in es if not e.synthetic)
        print(f"  {DIM}Cartas: {meta['charts']} | "
              f"{graph.n} nós | {n_real} arestas de corredor{RST}")

        try:
            result = plan_v1_route(graph, meta["origin_id"], meta["dest_id"],
                                   gwo_cfg)
        except Exception as e:
            print(f"\n  {RED}✗ Erro no otimizador:{RST} {e}\n")
            continue

        _print_route(origin, dest, result)

        # ── plotagem ───────────────────────────────────────────────────────
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
