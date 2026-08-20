"""Loader do WMM-2025 a partir do JSON espelhado do `@cristianob/geomagnetism` (produto TS).

Ver README.md (proveniência) e `wmm-2025.json` (dados). Converte o array achatado
`(n=1..12, m=0..n)` do JSON em coeficientes no formato que o `pygeomag` aceita via
`GeoMag(coefficients_data=...)`: `((epoch, name, release_date), [(n, m, gnm, hnm, dgnm, dhnm), ...])`.
"""
from __future__ import annotations

import json
from pathlib import Path

_JSON_PATH = Path(__file__).parent / "wmm-2025.json"


def _flatten_to_coefficients(data: dict) -> list[tuple[int, int, float, float, float, float]]:
    g = data["main_field_coeff_g"]
    h = data["main_field_coeff_h"]
    dg = data["secular_var_coeff_g"]
    dh = data["secular_var_coeff_h"]
    n_max = data["n_max"]

    coefficients = []
    i = 1  # índice 0 é um dummy (placeholder de n=0), não usado
    for n in range(1, n_max + 1):
        for m in range(0, n + 1):
            coefficients.append((n, m, g[i], h[i], dg[i], dh[i]))
            i += 1
    return coefficients


def load_wmm_2025_coefficients_data() -> tuple:
    """Devolve `((epoch, name, release_date), coeficientes)` pronto para `GeoMag(coefficients_data=...)`."""
    data = json.loads(_JSON_PATH.read_text())
    epoch = float(data["epoch"])
    name = data["name"]
    release_date = data["start_date"]
    coefficients = _flatten_to_coefficients(data)
    return (epoch, name, release_date), coefficients
