"""Extração do subgrafo regional — esquema **published** (jetstream).

Mudança de arquitetura (v2 DESCONTINUADO):
  - published.adhps(id, icao, type, geom Point)            <- AGORA TEM geom!
  - published.special_routes_waypoints(id, name, type, chart, geom Point)
  - published.special_routes_connections(id, source_id, target_id, name,
        type, is_mandatory, heading, lower_limit, higher_limit,
        dimensions_nm, class, atc, frequency[], geom LineString)

Decisões de arquitetura desta versão:
  * Peso da aresta: ST_Length(geom::geography) mede o corredor REAL (curvas).
    Fallback para ST_DistanceSphere entre os nós quando a LineString for nula.
  * Digrafo assimétrico: cada sentido é uma linha própria. Nunca espelhar.
  * adhps NÃO precisa mais de tabela de coordenadas externa: a coluna geom é
    nativa. A query lê ST_X(geom)/ST_Y(geom) direto de published.adhps.
  * Filtro REA: TODAS as queries de waypoints e connections aplicam
    `WHERE type = 'REA'` — ignoramos aerovias IFR e qualquer outro tipo.

NOTA SOBRE O TIPO DA COLUNA geom
--------------------------------
O dicionário de dados descreve geom como "JSON / GeoJSON". Se no banco a
coluna for de fato GEOMETRY(Point/LineString, 4326) (caso em que ST_X/ST_Y/
ST_Length operam direto, como pede a especificação), use as queries como
estão. Se a coluna for armazenada como JSON/JSONB literal, basta trocar
`geom` por `ST_GeomFromGeoJSON(geom::text)` nas funções ST_* — ver a
constante GEOM_EXPR abaixo, que centraliza essa escolha em um único ponto.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from .geo import LonLat
from .graphmodel import Edge, Node, RouteGraph


def _parse_linestring(geom_json: Optional[str]) -> Optional[tuple]:
    """GeoJSON (LineString) -> tupla ((lon,lat), ...) para o portão geométrico.

    Aceita None/tipo inesperado e devolve None (a aresta funciona sem traçado;
    o portão cai na reta nó->nó). Pontos isolados ou vazios também viram None.
    """
    if not geom_json:
        return None
    try:
        coords = json.loads(geom_json).get("coordinates")
    except (ValueError, AttributeError):
        return None
    if not coords or not isinstance(coords, list):
        return None
    pts = tuple((float(c[0]), float(c[1])) for c in coords if len(c) >= 2)
    return pts if len(pts) >= 2 else None

# ---------------------------------------------------------------------------
# Esquema published (única fonte da verdade; v2 removido)
# ---------------------------------------------------------------------------
SCHEMA = "published"
WP_TABLE = f"{SCHEMA}.special_routes_waypoints"
CONN_TABLE = f"{SCHEMA}.special_routes_connections"
ADHP_TABLE = f"{SCHEMA}.adhps"

# Ponto único para alternar entre coluna geometry nativa e GeoJSON.
# Coluna geometry nativa (especificação):   "{col}"
# Coluna JSON/JSONB GeoJSON (alternativa):   "ST_GeomFromGeoJSON({col}::text)"
def GEOM_EXPR(col: str) -> str:
    return col  # geometry nativa — troque por ST_GeomFromGeoJSON(col+"::text") se for JSONB

# ---------------------------------------------------------------------------
# SQL — malha VFR no esquema published (sempre filtrando type = 'REA')
# ---------------------------------------------------------------------------

# Descobre as cartas REA próximas a um ponto (origem/destino).
SQL_DISCOVER_CHARTS = f"""
SELECT DISTINCT w.chart
FROM {WP_TABLE} w
WHERE w.type = 'REA'                                   -- filtro REA (ignora IFR)
  AND ST_DWithin(
        {GEOM_EXPR('w.geom')}::geography,
        ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography,
        %(radius_m)s
      );
"""

# Waypoints REA das cartas selecionadas.
SQL_WAYPOINTS_BY_CHARTS = f"""
SELECT w.id,
       w.name,
       w.chart,
       ST_X({GEOM_EXPR('w.geom')}) AS lon,
       ST_Y({GEOM_EXPR('w.geom')}) AS lat
FROM {WP_TABLE} w
WHERE w.type = 'REA'                                   -- filtro REA
  AND w.chart = ANY(%(charts)s);
"""

# Conexões (corredores) REA das cartas selecionadas.
# Peso = comprimento real do corredor; fallback = reta geodésica entre nós.
SQL_CONNECTIONS_BY_CHARTS = f"""
SELECT c.id,
       c.source_id,
       c.target_id,
       c.name                       AS corridor,
       c.is_mandatory,
       c.lower_limit,
       c.higher_limit,
       c.heading,
       c.class                      AS airspace_class,
       ST_AsGeoJSON({GEOM_EXPR('c.geom')}) AS geom_json,             -- traçado real do corredor
       COALESCE(
           ST_Length({GEOM_EXPR('c.geom')}::geography),                 -- corredor real (curvas)
           ST_DistanceSphere({GEOM_EXPR('o.geom')}, {GEOM_EXPR('d.geom')})  -- fallback: reta entre nós
       )                            AS weight_m
FROM {CONN_TABLE} c
JOIN {WP_TABLE} o ON o.id = c.source_id
JOIN {WP_TABLE} d ON d.id = c.target_id
JOIN {WP_TABLE} w ON w.id IN (c.source_id, c.target_id)
WHERE c.type = 'REA'                                   -- filtro REA na conexão
  AND w.type = 'REA'                                   -- filtro REA no waypoint
  AND w.chart = ANY(%(charts)s)
GROUP BY c.id, c.source_id, c.target_id, c.name, c.is_mandatory,
         c.lower_limit, c.higher_limit, c.heading, c.class, c.geom,
         o.geom, d.geom;
"""

# Aeródromo direto de published.adhps — sem JOIN externo, geom é nativo.
SQL_AERODROME = f"""
SELECT a.icao,
       a.icao            AS name,
       ST_X({GEOM_EXPR('a.geom')}) AS lon,
       ST_Y({GEOM_EXPR('a.geom')}) AS lat
FROM {ADHP_TABLE} a
WHERE a.icao = %(code)s
LIMIT 1;
"""

# Lista de ICAOs (para autocomplete da CLI).
SQL_LIST_ICAOS = f"SELECT icao FROM {ADHP_TABLE} ORDER BY icao;"


class PostgisLoader:
    """Loader do esquema published.

    Não há mais ``schema`` configurável nem ``aerodrome_coord_sql``: a coluna
    geom de adhps tornou o resolver externo desnecessário (ver resolver.py,
    agora depreciado). Os aeródromos são resolvidos aqui mesmo.
    """

    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def _rows(self, sql: str, params: dict) -> list[tuple]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    # ------------------------------------------------------------- aerodrome
    def fetch_aerodrome(self, icao_code: str) -> Node:
        """Resolve um aeródromo lendo ST_X/ST_Y direto de published.adhps.geom.

        Substitui toda a antiga lógica de aerodrome_coord_sql / JOIN externo.
        """
        rows = self._rows(SQL_AERODROME, {"code": icao_code})
        if not rows:
            raise LookupError(f"Aeródromo '{icao_code}' não encontrado em {ADHP_TABLE}.")
        code, name, lon, lat = rows[0]
        if lon is None or lat is None:
            raise LookupError(
                f"'{icao_code}' existe em {ADHP_TABLE} mas sem coordenada (geom nula)."
            )
        return Node(id=f"ADHP:{code}", name=name or code,
                    pos=LonLat(lon, lat), kind="aerodrome")

    def list_icaos(self) -> list[str]:
        return [r[0] for r in self._rows(SQL_LIST_ICAOS, {})]

    # ----------------------------------------------------------------- charts
    def discover_charts(self, points: Iterable[LonLat],
                        radius_nm: float = 60.0) -> list[str]:
        charts: set[str] = set()
        for p in points:
            rows = self._rows(SQL_DISCOVER_CHARTS,
                              {"lon": p.lon, "lat": p.lat,
                               "radius_m": radius_nm * 1852.0})
            charts.update(r[0] for r in rows)
        return sorted(charts)

    # ------------------------------------------------------------------ graph
    def build_subgraph(self, origin_icao: str, dest_icao: str,
                       chart_radius_nm: float = 60.0,
                       link_radius_nm: float = 30.0) -> tuple[RouteGraph, dict]:
        # link_radius_nm é mantido por compatibilidade da assinatura, mas o
        # modelo de arestas sintéticas agora decide entrada/saída por "está em
        # TMA REA?" (k-vizinhos) em vez de um raio fixo — ver add_synthetic_edges.
        g = RouteGraph()

        # Aeródromos resolvidos diretamente do banco (geom nativo).
        origin = self.fetch_aerodrome(origin_icao)
        dest = self.fetch_aerodrome(dest_icao)
        g.add_node(origin)
        g.add_node(dest)

        charts = self.discover_charts([origin.pos, dest.pos], chart_radius_nm)

        if charts:
            for _id, name, chart, lon, lat in self._rows(
                SQL_WAYPOINTS_BY_CHARTS, {"charts": charts}
            ):
                g.add_node(Node(id=_id, name=name, pos=LonLat(lon, lat),
                                kind="waypoint", chart=chart))

            for (_id, src, tgt, corridor, mandatory, lo, hi,
                 heading, cls, geom_json, w) in self._rows(
                SQL_CONNECTIONS_BY_CHARTS, {"charts": charts}
            ):
                if src in g.nodes and tgt in g.nodes:
                    # Edge recebe corridor (name), is_mandatory e o traçado real
                    # (geom) — usados pela classificação visual e pelo portão
                    # geométrico (anti-cruzamento) em add_synthetic_edges.
                    geom = _parse_linestring(geom_json)
                    g.add_edge(Edge(src, tgt, float(w), corridor=corridor,
                                    connection_id=_id,
                                    is_mandatory=bool(mandatory),
                                    lower_limit=lo, higher_limit=hi,
                                    geom=geom))

        diag = g.add_synthetic_edges(origin.id, dest.id)

        meta = {"charts": charts, "origin_id": origin.id, "dest_id": dest.id,
                "synthetic_diagnostics": diag}
        return g, meta