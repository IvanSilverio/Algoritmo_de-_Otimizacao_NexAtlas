"""Gera data/aerodromos_br_ourairports.csv a partir do dump público OurAirports.

Fonte: https://github.com/davidmegginson/ourairports-data (domínio público).
Dado PROVISÓRIO até a coordenada oficial existir no banco interno.

Uso:
    curl -sL https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv -o airports_world.csv
    python scripts/gerar_csv_aerodromos.py airports_world.csv
"""
import csv, sys

src = sys.argv[1] if len(sys.argv) > 1 else "airports_world.csv"
rows = []
with open(src, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        if row["iso_country"] != "BR":
            continue
        ident = row["ident"].strip()
        if len(ident) == 4 and ident[0] == "S":
            try:
                lon = float(row["longitude_deg"]); lat = float(row["latitude_deg"])
            except (ValueError, KeyError, TypeError):
                continue
            rows.append({"icao": ident, "name": row["name"],
                         "lon": f"{lon:.6f}", "lat": f"{lat:.6f}",
                         "type": row["type"], "municipality": row["municipality"]})
rows.sort(key=lambda r: r["icao"])
with open("data/aerodromos_br_ourairports.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["icao","name","lon","lat","type","municipality"])
    w.writeheader(); w.writerows(rows)
print(f"{len(rows)} aeródromos brasileiros gravados.")
