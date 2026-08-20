# wmm-2025.json — proveniência

Coeficientes WMM-2025 (NOAA), cópia espelho de `data/wmm-2025.json` da lib
`@cristianob/geomagnetism@0.2.0` (npm) — o produto TS usa este mesmo modelo.

- Fonte: `https://registry.npmjs.org/@cristianob/geomagnetism` (tarball da versão 0.2.0,
  `sha512-bkMSaC1s95s8YumWsDwEe9vkEYLZfnmxQW6NWiJINB+0v0wwNwHYwje5mjEziYbtjhn0VQeCIdVvhelW+86qTg==`),
  arquivo `data/wmm-2025.json`, copiado **sem alteração**.
- **Manter sincronizado com o pacote TS.** Revisar quando sair o WMM-2030 (início de 2030) —
  a edição WMM-2025 é válida de 2024-11-13 a 2029-11-13 (`start_date`/`end_date` no próprio JSON).
- Carregado por [`loader.py`](loader.py), que converte o array achatado em coeficientes
  `pygeomag` (ver `nexatlas_router/vertical/magnetic.py::WMM_EDITION`).
