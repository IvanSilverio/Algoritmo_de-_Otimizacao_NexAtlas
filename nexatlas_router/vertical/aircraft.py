"""Aeronave (input da V3) — carregada de published.aircraft_models.

Convenção das colunas: ac = ascent (subida), dc = descent (descida).
  operational_ceiling (+_unit)  -> teto
  rate_ac / rate_dc  (+_unit)   -> razão de subida / descida
  speed_ac / speed_cruise / speed_dc (+_unit) -> velocidades

Observado nos dados reais (733 linhas): só ~26 têm performance completa;
unidades usadas: teto 'ft', razão 'ft/min', velocidade 'kt' (com 1 'mph').
`designator_icao` NÃO é único (ex.: C208 x3) — a chave é `id`.
Este módulo NÃO usa razão-inexistente-de-tempo: o banco só dá RAZÃO (ft/min),
o tempo é derivado no profile.py como Δaltitude/razão.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

KT_PER_MPH = 0.868976
FT_PER_M = 3.28084
FPM_PER_MS = 196.850394       # m/s -> ft/min (caso apareça)


@dataclass(frozen=True)
class Aeronave:
    id: str
    icao: str
    model: str
    teto_ft: float
    rate_ac_fpm: float        # razão de subida
    rate_dc_fpm: float        # razão de descida
    speed_ac_kt: float        # velocidade de subida
    speed_cruise_kt: float    # velocidade de cruzeiro
    speed_dc_kt: float        # velocidade de descida
    fuel_cruise: Optional[float] = None   # V3+: combustível (não usado agora)
    fuel_unit: Optional[str] = None

    @property
    def label(self) -> str:
        return f"{self.icao} — {self.model}"


# --------------------------------------------------------------------------- util
def _num(v) -> Optional[float]:
    try:
        if v is None:
            return None
        if isinstance(v, str) and v.strip() in ("", "None"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _u(v) -> str:
    return (v or "").strip().lower() if isinstance(v, str) else ""


def _to_ft(val: Optional[float], unit: str) -> Optional[float]:
    if val is None:
        return None
    if unit in ("", "ft", "feet"):
        return val
    if unit in ("m", "meter", "metros"):
        return val * FT_PER_M
    return None                       # unidade desconhecida -> descarta


def _to_fpm(val: Optional[float], unit: str) -> Optional[float]:
    if val is None:
        return None
    if unit in ("", "ft/min", "fpm"):
        return val
    if unit in ("m/s", "mps"):
        return val * FPM_PER_MS
    return None


def _to_kt(val: Optional[float], unit: str) -> Optional[float]:
    if val is None:
        return None
    if unit in ("", "kt", "kts", "knots"):
        return val
    if unit in ("mph",):
        return val * KT_PER_MPH
    if unit in ("km/h", "kph"):
        return val * 0.539957
    return None


def aeronave_from_row(row: dict) -> Optional[Aeronave]:
    """Converte uma linha de aircraft_models em Aeronave, ou None se
    incompleta / com unidade desconhecida."""
    teto = _to_ft(_num(row.get("operational_ceiling")),
                  _u(row.get("operational_ceiling_unit")))
    ru = _u(row.get("rate_unit"))
    r_ac = _to_fpm(_num(row.get("rate_ac")), ru)
    r_dc = _to_fpm(_num(row.get("rate_dc")), ru)
    su = _u(row.get("speed_unit"))
    s_ac = _to_kt(_num(row.get("speed_ac")), su)
    s_cr = _to_kt(_num(row.get("speed_cruise")), su)
    s_dc = _to_kt(_num(row.get("speed_dc")), su)

    vals = [teto, r_ac, r_dc, s_ac, s_cr, s_dc]
    if any(v is None or v <= 0 for v in vals):
        return None                    # filtro de completude
    return Aeronave(
        id=str(row.get("id")),
        icao=str(row.get("designator_icao") or "?"),
        model=str(row.get("model") or ""),
        teto_ft=teto, rate_ac_fpm=r_ac, rate_dc_fpm=r_dc,
        speed_ac_kt=s_ac, speed_cruise_kt=s_cr, speed_dc_kt=s_dc,
        fuel_cruise=_num(row.get("fuel_consumption_cruise")),
        fuel_unit=(row.get("fuel_consumption_unit") or None),
    )


def build_catalog(rows) -> list[Aeronave]:
    """Lista de aeronaves utilizáveis (performance completa), ordenada."""
    out = [ac for ac in (aeronave_from_row(r) for r in rows) if ac is not None]
    out.sort(key=lambda a: (a.icao, a.model))
    return out


# --------------------------------------------------------------- fontes de dados
SQL_AIRCRAFT = """
SELECT id, designator_icao, model, aircraft_type,
       operational_ceiling, operational_ceiling_unit,
       rate_ac, rate_dc, rate_unit,
       speed_ac, speed_cruise, speed_dc, speed_unit,
       fuel_consumption_cruise, fuel_consumption_unit
FROM published.aircraft_models;
"""


def load_from_db(conn) -> list[Aeronave]:
    with conn.cursor() as cur:
        cur.execute(SQL_AIRCRAFT)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return build_catalog(rows)


def load_from_json(path: str) -> list[Aeronave]:
    import json
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("aeronaves", data) if isinstance(data, dict) else data
    return build_catalog(rows)


def find(catalog: list[Aeronave], key: str) -> Optional[Aeronave]:
    """Busca por id exato, senão por ICAO (primeiro match, ordenado)."""
    for a in catalog:
        if a.id == key:
            return a
    for a in catalog:
        if a.icao.upper() == key.upper():
            return a
    return None
