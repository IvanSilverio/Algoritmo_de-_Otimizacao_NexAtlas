# Ingestão e uso dos dados — Motor de Rotas V1

Como cada entidade (waypoints, corredores, aeródromos, TMAs) sai do banco
`jetstream` / esquema `published`, é transformada e entra no motor. **Este
documento substitui a versão que descrevia o esquema `_v2`, o
`CsvResolver`/OurAirports e o GWO como motor — tudo obsoleto** (ver `CLAUDE.md`).

---

## Visão geral do pipeline

Dois caminhos, com a MESMA lógica de grafo e o MESMO solver:

```
PRODUÇÃO (online, contra o banco)
  published (PostGIS) ──db.PostgisLoader.build_subgraph──► RouteGraph ──► Dijkstra c/ fase (+Yen)

TESTES (offline, sem banco)
  published ──dump_nexatlas.py──► 3 JSONs ──build_sub (test_regressao.py)──► RouteGraph ──► idem
```

O dump é uma fotografia da base para testes internos rápidos e reprodutíveis; a
produção lê o banco direto. Os dois montam o mesmo grafo e chamam o mesmo solver
(`dijkstra.shortest_route` + `k_shortest_routes`).

---

## 1. Waypoints (nós da malha REA)

**Origem:** `special_routes_waypoints` (`WHERE type='REA'` → 550 nós; a tabela
também tem REH/VAC/REUL, não usados na V1).

**Coordenada:** `ST_X(geom)`=lon, `ST_Y(geom)`=lat (PostGIS guarda `[lon,lat]`);
o código padroniza como `LonLat` num único ponto (`geo.py`) para nunca inverter.

**Fronteira (`db`/`in`):** distância à borda da TMA e se está dentro, calculadas
contra a UNIÃO dos setores da TMA da carta (`ST_Distance` ao `ST_Boundary`,
`ST_Contains`). Alimentam o `border_score`, usado só para ponderar as pontes de
voo direto — nunca a entrada/saída, que vão por mínimo-local.

**Subgrafo regional:** nunca se carrega a malha nacional. `build_subgraph`
descobre por raio (`chart_radius_nm=60`, `ST_DWithin`) só as cartas perto de
origem e destino; só os waypoints dessas cartas viram nós.

---

## 2. Corredores (arestas REA)

**Origem:** `special_routes_connections` (`type='REA'` → 1.040 arestas).

**JOIN duplo:** cruza `source_id` e `target_id` com os waypoints para as duas
pontas. Peso = `ST_Length(geom::geography)` (comprimento real, com curvas);
fallback `ST_DistanceSphere` entre nós se a LineString for nula.

**Direção:** digrafo. Cada linha é mão única (`source→target`); ida e volta são
linhas distintas, com piso/teto/classe próprios (invariante do PORTÃO RESTINGA —
nunca espelhar arestas).

**Atributos de trecho:** `is_mandatory` (gera obrigação de fase — ver `CLAUDE.md`,
princípio 3), `lower_limit`/`higher_limit`, `class`, `heading`, `frequency`
(array). A V1 usa `is_mandatory` e o peso; os demais são carregados para exibição
e reservados à V2.

---

## 3. Aeródromos (nós terminais)

**Origem:** `adhps` (6.021 registros; coluna `designator_icao`, renomeada de
`icao`). **Tem geometria** — lida com `ST_X/ST_Y(geom)` (registros sem coordenada
são `NULL` e ignorados). O antigo `CsvResolver`/OurAirports e o
`AdhpsGeomResolver` estão **descontinuados**: o loader resolve direto do banco.

**Entrada no grafo:** o usuário informa só o ICAO. O loader cria o nó terminal e
`add_synthetic_edges` liga origem/destino à malha por arestas sintéticas
(entrada/saída/ponte).

> **Próxima etapa:** os portões de aeródromo do documento **IAC** substituem a
> estimativa geométrica de entrada/saída por dado publicado — ver `CLAUDE.md`,
> "Decisões abertas" nº 4. São distintos dos portões da malha REA.

---

## 4. TMAs (`airspaces`)

**Origem:** `airspaces` (`type='tma'` → 53 polígonos). No dump saem como GeoJSON
(`MultiPolygon`) com todas as colunas. Uso na V1: calcular `db`/`in` dos
waypoints (item 1) e validar geograficamente portões por point-in-polygon.

Nem toda REA tem TMA cadastrada: **Parintins, Ribeirão Preto e Tabatinga** não
têm, e seus waypoints ficam sem `db`/`in` (score neutro) — roteiam normalmente.
É lacuna do banco, não do dump; preencher as TMAs faltantes resolve.

---

## 5. Como tudo se junta (Dijkstra com fase — NÃO GWO)

1. **Monta o subgrafo:** waypoints das cartas próximas + aeródromos + corredores
   reais + arestas sintéticas (`add_synthetic_edges`). `requires_corridor` fica
   `True` se alguma ponta está em TMA.
2. **Rota principal:** `dijkstra.shortest_route` — caminho mínimo EXATO sobre o
   estado `(nó, owes, used)` (ver `CLAUDE.md`). Determinístico.
3. **Anti-esporão:** `v1.plan_v1_route` aplica a 2ª passada se a rota repetir nó.
4. **Alternativas:** `dijkstra.k_shortest_routes` (Yen) — as K melhores válidas.
5. **Saída (`V1RouteResult`):** pontos, corredores usados, distâncias, `reason`,
   `meta` (`route_source`, alternativas). O GWO NÃO participa (reservado à V3).

---

## 6. Gerar o dump / rodar a regressão

```bash
source .env.sh
python3 dump_nexatlas.py .        # gera nexatlas_rea_malha/aerodromos/airspaces.json
python3 test_regressao.py         # regressão offline sobre o dump (exit 0/1)
```

---

## 7. Visualização

- `plot_route.py` → zoom REGIONAL no subgrafo (rota principal + candidatas).
- `plot_national.py` → malha NACIONAL sobre o contorno do Brasil.

```bash
source .env.sh
python3 -m nexatlas_router.plot_national            # só a malha do país
python3 -m nexatlas_router.plot_national SBMT SBJD  # malha + rota destacada
```