"""Testes unitários da detecção pós-rota de áreas SUA."""
from types import SimpleNamespace
import json
import unittest

from nexatlas_router.restricted_airspaces import (
    SUA_TYPES, _route_lines, find_route_airspace_intersections,
)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql, params):
        self.params = params

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.cur = FakeCursor(rows)
        self.rolled_back = False

    def cursor(self):
        return self.cur

    def rollback(self):
        self.rolled_back = True


class RestrictedAirspacesTest(unittest.TestCase):
    def test_route_lines_preserves_curve_and_orients_it(self):
        result = SimpleNamespace(
            points=[{"lon": 0, "lat": 0}, {"lon": 2, "lat": 0}],
            route=SimpleNamespace(edges=[SimpleNamespace(
                geom=((2, 0), (1, 1), (0, 0)))])
        )
        self.assertEqual(_route_lines(result), [[[0.0, 0.0], [1.0, 1.0], [2.0, 0.0]]])

    def test_route_lines_uses_straight_segment_without_edge_geometry(self):
        result = SimpleNamespace(
            points=[{"lon": -1, "lat": -2}, {"lon": 3, "lat": 4}],
            route=SimpleNamespace(edges=[SimpleNamespace(geom=None)])
        )
        self.assertEqual(_route_lines(result), [[[-1.0, -2.0], [3.0, 4.0]]])

    def test_query_maps_geometry_and_limits_types(self):
        geometry = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 0]]]}
        conn = FakeConnection([(
            "id-1", "sua_restricted", "SBR001", "Teste", "SFC", "FL100",
            "H24", "Observação", None, json.dumps(geometry),
        )])
        result = SimpleNamespace(points=[{"lon": 0, "lat": 0}, {"lon": 1, "lat": 1}],
                                 route=None)

        areas = find_route_airspace_intersections(conn, result)

        self.assertEqual(areas[0]["code"], "SBR001")
        self.assertEqual(areas[0]["geometry"], geometry)
        self.assertEqual(conn.cur.params["types"], list(SUA_TYPES))
        route = json.loads(conn.cur.params["route_geojson"])
        self.assertEqual(route["type"], "MultiLineString")


if __name__ == "__main__":
    unittest.main()

