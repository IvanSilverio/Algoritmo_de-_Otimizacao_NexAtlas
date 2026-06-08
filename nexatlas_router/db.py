"""Extração do subgrafo regional — esquema v2 (BD_Nex_2_0).

Tabelas:
  - special_routes_waypoints_v2(id, name, type, chart, geom Point)
  - special_routes_connections_v2(id, source_id, target_id, name, heading,
        lower_limit, higher_limit, dimensions_nm, class, is_mandatory,
        atc, frequency[], geom LineString)
  - adhps(id, icao, type)  -> SEM geometria (tabela "cabeçalho")

Pontos de arquitetura validados pela auditoria do banco:
  * Peso da aresta: ST_Length(c.{geom_col}::geography) mede o corredor REAL
    (inclusive curvas). Fallback para ST_DistanceSphere entre os nós quando
    a LineString for nula.
  * Digrafo assimétrico: cada sentido é uma linha própria com piso/teto/
    classe diferentes (caso PORTÃO RESTINGA). Nunca espelhar arestas.
  * adhps NÃO possui coordenadas. A query de coordenadas é CONFIGURÁVEL
    (aerodrome_coord_sql) até identificarmos a tabela que as armazena.
    Alternativa: passar lon/lat manualmente em fetch_aerodrome().
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from .geo import LonLat
from .graphmodel import Edge, Node, RouteGraph

# ---------------------------------------------------------------------------
# SQL — malha VFR v2
# ---------------------------------------------------------------------------

SQL_DISCOVER_CHARTS = """
SELECT DISTINCT w.chart
FROM {wp_table} w
WHERE ST_DWithin(
        w.{geom_col}::geography,
        ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography,
        %(radius_m)s
      );
"""

SQL_WAYPOINTS_BY_CHARTS = """
SELECT w.id,
       w.name,
       w.chart,
       ST_X(w.{geom_col}) AS lon,
       ST_Y(w.{geom_col}) AS lat
FROM {wp_table} w
WHERE w.chart = ANY(%(charts)s);
"""

SQL_CONNECTIONS_BY_CHARTS = """
SELECT c.id,
       c.source_id,
       c.target_id,
       c.name                       AS corridor,
       c.is_mandatory,
       c.lower_limit,
       c.higher_limit,
       c.heading,
       c.class                      AS airspace_class,
       COALESCE(
           ST_Length(c.{geom_col}::geography),          -- corredor real (curvas)
           ST_DistanceSphere(o.{geom_col}, d.{geom_col})      -- fallback: reta entre nós
       )                            AS weight_m
FROM {conn_table} c
JOIN {wp_table} o ON o.id = c.source_id
JOIN {wp_table} d ON d.id = c.target_id
JOIN {wp_table} w ON w.id IN (c.source_id, c.target_id)
WHERE w.chart = ANY(%(charts)s)
GROUP BY c.id, c.source_id, c.target_id, c.name, c.is_mandatory,
         c.lower_limit, c.higher_limit, c.heading, c.class, c.{geom_col},
         o.{geom_col}, d.{geom_col};
"""

# ---------------------------------------------------------------------------
# adhps — ALERTA DE ARQUITETURA (confirmado em BD_Nex_2_0)
#
# A tabela adhps NÃO possui colunas geográficas; é um cabeçalho descritivo
# (id, icao, type). As coordenadas vivem em outra tabela ainda não mapeada
# no dicionário (candidatas: metadata, tabela de pistas, airways_waypoints).
#
# Enquanto a fonte não é confirmada, o SQL abaixo é um TEMPLATE: ajuste o
# JOIN marcado com <<< quando a tabela for identificada, ou injete um SQL
# próprio via PostgisLoader(aerodrome_coord_sql=...). O contrato é retornar
# (icao, name, lon, lat) para um %(code)s.
# ---------------------------------------------------------------------------

SQL_AERODROME_TEMPLATE = """
SELECT a.icao,
       a.icao            AS name,        -- adhps não tem coluna name mapeada
       ST_X(m.geom)      AS lon,         -- <<< ajustar tabela de coordenadas
       ST_Y(m.geom)      AS lat          -- <<< ajustar tabela de coordenadas
FROM adhps a
JOIN __TABELA_DE_COORDENADAS__ m ON m.adhp_id = a.id   -- <<< ajustar JOIN
WHERE a.icao = %(code)s
LIMIT 1;
"""

# Diagnóstico: rode isto no banco para localizar a tabela de coordenadas
SQL_FIND_COORD_TABLE = """
SELECT f_table_name, f_geometry_column, type
FROM geometry_columns
ORDER BY f_table_name;
"""


class PostgisLoader:
    """Loader com esquema configurável.

    schema="v2"     -> special_routes_*_v2, coluna geom (BD_Nex_2_0)
    schema="legacy" -> special_routes_*,     coluna geometry (dicionário v1,
                       o mesmo usado no script de plotagem nacional)
    """

    SCHEMAS = {
        "v2": {"wp_table": "special_routes_waypoints_v2",
               "conn_table": "special_routes_connections_v2",
               "geom_col": "geom"},
        "legacy": {"wp_table": "special_routes_waypoints",
                   "conn_table": "special_routes_connections",
                   "geom_col": "geometry"},
    }

    def __init__(self, conn: Any,
                 aerodrome_coord_sql: Optional[str] = None,
                 schema: str = "v2") -> None:
        self.conn = conn
        self.aerodrome_coord_sql = aerodrome_coord_sql
        cols = self.SCHEMAS[schema]
        self.sql_discover_charts = SQL_DISCOVER_CHARTS.format(**cols)
        self.sql_waypoints = SQL_WAYPOINTS_BY_CHARTS.format(**cols)
        self.sql_connections = SQL_CONNECTIONS_BY_CHARTS.format(**cols)

    def _rows(self, sql: str, params: dict) -> list[tuple]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    # ------------------------------------------------------------- aerodrome
    def fetch_aerodrome(self, icao_code: str,
                        lonlat: Optional[tuple[float, float]] = None) -> Node:
        """Resolve um aeródromo em Node.

        Caminho 1 (preferido): aerodrome_coord_sql configurado no loader.
        Caminho 2 (contorno):  lonlat=(lon, lat) informado manualmente —
                               útil até a tabela de coordenadas ser mapeada.
        """
        if lonlat is not None:
            return Node(id=f"ADHP:{icao_code}", name=icao_code,
                        pos=LonLat(*lonlat), kind="aerodrome")

        if not self.aerodrome_coord_sql:
            raise NotImplementedError(
                "adhps não possui geometria (confirmado em BD_Nex_2_0) e a "
                "tabela de coordenadas ainda não foi configurada. Opções: "
                "(1) passe lonlat=(lon, lat) manualmente; (2) identifique a "
                "tabela rodando SQL_FIND_COORD_TABLE no banco e configure "
                "PostgisLoader(aerodrome_coord_sql=...)."
            )

        rows = self._rows(self.aerodrome_coord_sql, {"code": icao_code})
        if not rows:
            raise LookupError(f"Aeródromo '{icao_code}' não encontrado.")
        code, name, lon, lat = rows[0]
        return Node(id=f"ADHP:{code}", name=name or code,
                    pos=LonLat(lon, lat), kind="aerodrome")

    # ----------------------------------------------------------------- charts
    def discover_charts(self, points: Iterable[LonLat],
                        radius_nm: float = 60.0) -> list[str]:
        charts: set[str] = set()
        for p in points:
            rows = self._rows(self.sql_discover_charts,
                              {"lon": p.lon, "lat": p.lat,
                               "radius_m": radius_nm * 1852.0})
            charts.update(r[0] for r in rows)
        return sorted(charts)

    # ------------------------------------------------------------------ graph
    def build_subgraph(self, origin_icao: str, dest_icao: str,
                       origin_lonlat: Optional[tuple[float, float]] = None,
                       dest_lonlat: Optional[tuple[float, float]] = None,
                       resolver: Optional[Any] = None,
                       chart_radius_nm: float = 60.0,
                       link_radius_nm: float = 30.0) -> tuple[RouteGraph, dict]:
        g = RouteGraph()

        # Prioridade: resolver (CSV/adhps/tabela própria) > lon/lat manual > SQL.
        if resolver is not None:
            origin = resolver.resolve(origin_icao)
            dest = resolver.resolve(dest_icao)
        else:
            origin = self.fetch_aerodrome(origin_icao, origin_lonlat)
            dest = self.fetch_aerodrome(dest_icao, dest_lonlat)
        g.add_node(origin)
        g.add_node(dest)

        charts = self.discover_charts([origin.pos, dest.pos], chart_radius_nm)

        if charts:
            for _id, name, chart, lon, lat in self._rows(
                self.sql_waypoints, {"charts": charts}
            ):
                g.add_node(Node(id=_id, name=name, pos=LonLat(lon, lat),
                                kind="waypoint", chart=chart))

            for (_id, src, tgt, corridor, mandatory, lo, hi,
                 heading, cls, w) in self._rows(
                self.sql_connections, {"charts": charts}
            ):
                if src in g.nodes and tgt in g.nodes:
                    g.add_edge(Edge(src, tgt, float(w), corridor=corridor,
                                    connection_id=_id,
                                    is_mandatory=bool(mandatory),
                                    lower_limit=lo, higher_limit=hi))

        g.add_synthetic_edges(origin.id, dest.id, max_link_nm=link_radius_nm)

        meta = {"charts": charts, "origin_id": origin.id, "dest_id": dest.id}
        return g, meta
