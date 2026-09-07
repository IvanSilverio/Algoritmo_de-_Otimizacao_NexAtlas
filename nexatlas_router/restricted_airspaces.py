"""Detecção pós-rota de áreas SUA cruzadas pela rota principal.

Esta etapa é deliberadamente posterior ao otimizador: ela não altera a rota,
apenas consulta ``published.airspaces`` e enriquece o resultado para alertas e
plotagem. A interseção é horizontal; limites verticais e ativação são reportados
para avaliação operacional.
"""
from __future__ import annotations

import json
from typing import Any


SUA_TYPES = ("sua_dangerous", "sua_prohibited", "sua_restricted")

SQL_ROUTE_INTERSECTIONS = """
WITH route AS (
    SELECT ST_SetSRID(ST_GeomFromGeoJSON(%(route_geojson)s), 4326) AS geom
)
SELECT a.id, a.type, a.code, a.name,
       a.lower_limit_text, a.upper_limit_text,
       a.operational_hours, a.remarks, a.comms_text,
       ST_AsGeoJSON(a.geom)
FROM published.airspaces a
CROSS JOIN route r
WHERE a.type = ANY(%(types)s)
  AND a.geom IS NOT NULL
  AND ST_Intersects(a.geom, r.geom)
ORDER BY a.type, a.code, a.name;
"""


def _route_lines(result: Any) -> list[list[list[float]]]:
    """Representa cada perna como LineString, preservando curvas cadastradas."""
    lines: list[list[list[float]]] = []
    route = getattr(result, "route", None)
    edges = getattr(route, "edges", None)
    points = result.points
    for i in range(len(points) - 1):
        edge = edges[i] if edges is not None and i < len(edges) else None
        geom = getattr(edge, "geom", None)
        if geom and len(geom) >= 2:
            coords = [[float(lon), float(lat)] for lon, lat in geom]
            start = [float(points[i]["lon"]), float(points[i]["lat"])]
            if ((coords[-1][0] - start[0]) ** 2 + (coords[-1][1] - start[1]) ** 2
                    < (coords[0][0] - start[0]) ** 2 + (coords[0][1] - start[1]) ** 2):
                coords.reverse()
        else:
            coords = [
                [float(points[i]["lon"]), float(points[i]["lat"])],
                [float(points[i + 1]["lon"]), float(points[i + 1]["lat"])],
            ]
        lines.append(coords)
    return lines


def find_route_airspace_intersections(conn: Any, result: Any) -> list[dict]:
    """Retorna áreas perigosas, proibidas e restritas cruzadas pela rota."""
    lines = _route_lines(result)
    if not lines:
        return []
    route_geojson = json.dumps({"type": "MultiLineString", "coordinates": lines})
    try:
        with conn.cursor() as cur:
            cur.execute(SQL_ROUTE_INTERSECTIONS,
                        {"route_geojson": route_geojson, "types": list(SUA_TYPES)})
            rows = cur.fetchall()
    except Exception:
        conn.rollback()
        raise

    fields = ("id", "type", "code", "name", "lower_limit", "upper_limit",
              "operational_hours", "remarks", "comms_text", "geometry")
    areas = []
    for row in rows:
        area = dict(zip(fields, row))
        area["geometry"] = json.loads(area["geometry"])
        areas.append(area)
    return areas

