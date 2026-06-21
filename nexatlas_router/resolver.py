"""Resolução de ICAO -> coordenada — MÓDULO DEPRECIADO.

Com o esquema **published**, a coluna ``adhps.geom`` é nativa e o
``PostgisLoader.fetch_aerodrome`` (db.py) resolve os aeródromos diretamente
via ST_X(geom)/ST_Y(geom). Não há mais necessidade de um resolver externo.

``AdhpsGeomResolver`` é mantido apenas como casca de compatibilidade e emite
um DeprecationWarning. Os demais resolvers (CSV/manual) seguem disponíveis
para usos offline/standalone (ex.: plot_national sem acesso a adhps).
"""
from __future__ import annotations

import warnings
from typing import Any, Protocol

from .geo import LonLat
from .graphmodel import Node


class AerodromeResolver(Protocol):
    def resolve(self, icao: str) -> Node: ...


class AdhpsGeomResolver:
    """DEPRECIADO. Use ``PostgisLoader.fetch_aerodrome`` (db.py) diretamente.

    Mantido só para não quebrar imports antigos. Internamente delega a leitura
    a published.adhps.geom, mas o caminho recomendado é o loader.
    """

    def __init__(self, conn: Any, geom_col: str = "geom") -> None:
        warnings.warn(
            "AdhpsGeomResolver está depreciado: o PostgisLoader já resolve "
            "aeródromos via published.adhps.geom. Use loader.fetch_aerodrome().",
            DeprecationWarning, stacklevel=2,
        )
        self.conn = conn
        self.geom_col = geom_col

    def resolve(self, icao: str) -> Node:
        # Delegação mínima para compatibilidade (esquema published).
        sql = (f"SELECT icao, ST_X({self.geom_col}) AS lon, "
               f"ST_Y({self.geom_col}) AS lat "
               f"FROM published.adhps WHERE icao = %(code)s LIMIT 1;")
        with self.conn.cursor() as cur:
            cur.execute(sql, {"code": icao})
            row = cur.fetchone()
        if not row:
            raise LookupError(f"Aeródromo '{icao}' não encontrado em published.adhps.")
        code, lon, lat = row
        if lon is None or lat is None:
            raise LookupError(f"'{icao}' existe mas sem coordenada (geom nula).")
        return Node(id=f"ADHP:{code}", name=code, pos=LonLat(lon, lat),
                    kind="aerodrome")


class CsvResolver:
    """Lê coordenadas de um CSV (icao,name,lon,lat[,...]).

    Útil para execuções offline (ex.: plot_national de demonstração) sem
    acesso ao banco. Fonte pública rastreável (OurAirports).
    """
    def __init__(self, csv_path: str) -> None:
        import csv
        self.table: dict[str, tuple[str, float, float]] = {}
        with open(csv_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    self.table[r["icao"].strip().upper()] = (
                        r.get("name", r["icao"]),
                        float(r["lon"]), float(r["lat"]),
                    )
                except (ValueError, KeyError):
                    continue

    def resolve(self, icao: str) -> Node:
        key = icao.strip().upper()
        if key not in self.table:
            raise LookupError(f"Aeródromo '{icao}' não está no CSV de coordenadas.")
        name, lon, lat = self.table[key]
        return Node(id=f"ADHP:{key}", name=name, pos=LonLat(lon, lat),
                    kind="aerodrome")


class ManualResolver:
    """Coordenada informada explicitamente: {"SBMT": (lon, lat), ...}."""
    def __init__(self, coords: dict[str, tuple[float, float]]) -> None:
        self.coords = coords

    def resolve(self, icao: str) -> Node:
        if icao not in self.coords:
            raise LookupError(f"Coordenada de '{icao}' não foi fornecida.")
        lon, lat = self.coords[icao]
        return Node(id=f"ADHP:{icao}", name=icao, pos=LonLat(lon, lat),
                    kind="aerodrome")