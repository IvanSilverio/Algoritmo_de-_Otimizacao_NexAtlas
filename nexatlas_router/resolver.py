"""Resolução de ICAO -> coordenada (ponto terminal da rota).

PROBLEMA: o usuário informa o aeródromo pelo código ICAO (ex.: "SBMT"). O
motor precisa da coordenada (lon, lat) desse aeródromo para iniciar/terminar
a rota. Hoje a tabela `adhps` NÃO tem geometria (confirmado no banco), então
esta camada isola "de onde vem a coordenada" do resto do motor.

DESENHO: uma interface AerodromeResolver com várias implementações plugáveis.
Trocar a fonte = trocar a instância passada ao motor. Nenhuma outra parte do
código muda.

Backends prontos para o dia em que a fonte existir:
  - AdhpsGeomResolver: lê a coordenada da `adhps` QUANDO ela ganhar geometria.
  - OwnTableResolver:   lê de uma tabela própria (ex.: importada do DECEA).

"""
from __future__ import annotations

from typing import Any, Optional, Protocol

from .geo import LonLat
from .graphmodel import Node


class AerodromeResolver(Protocol):
    """Contrato: dado um ICAO, devolve o nó terminal com coordenada."""
    def resolve(self, icao: str) -> Node: ...


# ---------------------------------------------------------------------------
# Backends REAIS (prontos, aguardando a fonte de dados existir)
# ---------------------------------------------------------------------------

class AdhpsGeomResolver:
    """Lê a coordenada da `adhps` QUANDO a coluna de geometria for populada.

    Ative quando o admin confirmar o nome da coluna geométrica (ex.: 'geom').
    Ajuste `geom_col` conforme a resposta.
    """
    def __init__(self, conn: Any, geom_col: str = "geom") -> None:
        self.conn = conn
        self.geom_col = geom_col

    def resolve(self, icao: str) -> Node:
        sql = f"""
            SELECT icao, ST_X({self.geom_col}) AS lon, ST_Y({self.geom_col}) AS lat
            FROM adhps WHERE icao = %(code)s LIMIT 1;
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, {"code": icao})
            row = cur.fetchone()
        if not row:
            raise LookupError(f"Aeródromo '{icao}' não encontrado na adhps.")
        code, lon, lat = row
        if lon is None or lat is None:
            raise ValueError(
                f"'{icao}' existe na adhps mas está SEM coordenada. "
                "A coluna de geometria ainda não foi populada."
            )
        return Node(id=f"ADHP:{code}", name=code,
                    pos=LonLat(lon, lat), kind="aerodrome")


class OwnTableResolver:
    """Lê de uma tabela própria de aeródromos (ex.: importada do DECEA/AISWEB).

    Use se o admin autorizar criar uma fonte própria enquanto a oficial não
    fica pronta. Espera uma tabela com colunas (icao, geom Point).
    """
    def __init__(self, conn: Any, table: str = "adhps_coords",
                 geom_col: str = "geom") -> None:
        self.conn = conn
        self.table = table
        self.geom_col = geom_col

    def resolve(self, icao: str) -> Node:
        sql = f"""
            SELECT icao, ST_X({self.geom_col}) AS lon, ST_Y({self.geom_col}) AS lat
            FROM {self.table} WHERE icao = %(code)s LIMIT 1;
        """
        with self.conn.cursor() as cur:
            cur.execute(sql, {"code": icao})
            row = cur.fetchone()
        if not row:
            raise LookupError(f"Aeródromo '{icao}' não encontrado em {self.table}.")
        code, lon, lat = row
        return Node(id=f"ADHP:{code}", name=code,
                    pos=LonLat(lon, lat), kind="aerodrome")


class CsvResolver:
    """Lê coordenadas de um CSV (icao, name, lon, lat, ...).

    PROVISÓRIO mas com dado REAL: o CSV padrão é derivado do OurAirports
    (base pública, domínio público, davidmegginson/ourairports-data),
    filtrado para ICAOs brasileiros. NÃO é dado inventado — é fonte pública
    rastreável, usada só até a coordenada oficial existir no banco interno.

    Cabeçalho esperado: icao,name,lon,lat[,type,municipality]
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
            raise LookupError(
                f"Aeródromo '{icao}' não está no CSV de coordenadas "
                f"(fonte pública OurAirports). Verifique o código ICAO.")
        name, lon, lat = self.table[key]
        return Node(id=f"ADHP:{key}", name=name,
                    pos=LonLat(lon, lat), kind="aerodrome")


class ManualResolver:
    """Coordenada informada explicitamente na chamada (origin_lonlat/dest_lonlat).

    É o que usamos no teste SBMT->SBJD: a coordenada veio do usuário, não de
    dado inventado. Útil quando o piloto fornece as pontas manualmente.
    """
    def __init__(self, coords: dict[str, tuple[float, float]]) -> None:
        # coords: {"SBMT": (lon, lat), ...}  — fornecido por quem chama
        self.coords = coords

    def resolve(self, icao: str) -> Node:
        if icao not in self.coords:
            raise LookupError(f"Coordenada de '{icao}' não foi fornecida.")
        lon, lat = self.coords[icao]
        return Node(id=f"ADHP:{icao}", name=icao,
                    pos=LonLat(lon, lat), kind="aerodrome")
