# Ingestão e uso dos dados — Motor de Rotas V1

Como cada entidade (waypoints, rotas/corredores e aeródromos) sai do banco,
é transformada e entra no algoritmo de roteamento.

---

## Visão geral do pipeline

```
  BANCO (PostGIS)                EXTRAÇÃO (db.py)            GRAFO (memória)
  ───────────────                ────────────────            ───────────────
  special_routes_waypoints_v2 ─► nós com ST_X/ST_Y(geom)  ─► RouteGraph.nodes
  special_routes_connections_v2─► arestas + ST_Length     ─► RouteGraph.adj
  adhps + CSV OurAirports     ─► origem/destino (resolver)─► nós terminais
                                                              │
                                                              ▼
                                                      GWO (gwo.py) → rota
```

---

## 1. Waypoints (os nós da malha)

**Origem:** tabela `special_routes_waypoints_v2` (1.033 pontos: 550 REA,
401 REH, 76 VAC, 6 REUL).

**Como são lidos:** a query em `db.py` extrai a coordenada com
`ST_X(geom)` (longitude) e `ST_Y(geom)` (latitude). Isso resolve a
"armadilha do X/Y": o PostGIS guarda `[lon, lat]`, e o código padroniza
internamente como `LonLat(lon, lat)` para nunca inverter.

**Como são filtrados:** o motor NÃO carrega os 1.033 de uma vez. A função
`discover_charts()` usa `ST_DWithin` para achar só as cartas próximas da
origem e do destino (ex.: a rota SBMT→SBJD ativou 5 cartas: REA São Paulo,
REH Campinas, REH Sorocaba, REH São José dos Campos, REH São Paulo). Só os
waypoints dessas cartas viram nós. É o princípio do **subgrafo regional** —
processar a malha nacional inteira seria desperdício.

**Como são usados:** cada waypoint vira um índice no vetor de prioridades do
GWO. O algoritmo atribui uma "nota" a cada nó, e o decodificador caminha
pela malha escolhendo o vizinho de maior nota.

---

## 2. Rotas / corredores (as arestas)

**Origem:** tabela `special_routes_connections_v2` (~2.100 conexões).

**Como são lidas:** a query faz o **JOIN duplo obrigatório** — cruza
`source_id` e `target_id` com a tabela de waypoints para resgatar a
geometria das duas pontas. O peso de cada aresta vem de
`ST_Length(c.geom::geography)`, que mede o comprimento REAL do corredor
(a LineString, com curvas), com fallback para `ST_DistanceSphere` entre os
nós se a LineString for nula.

**Direção:** o grafo é DIRECIONADO. Cada linha da tabela é uma aresta de
mão única (`source → target`), respeitando a proa magnética do corredor.
Conexões de ida e volta são linhas distintas com piso/teto/classe próprios.

**Como são usadas:** as arestas definem por onde o decodificador pode
caminhar. O peso (distância) é o que o GWO minimiza. Corredores com
`is_mandatory` ou aplicáveis à saída/chegada acionam a regra de
obrigatoriedade (penalidade no fitness se ignorados).

---

## 3. Aeródromos (os nós terminais)

**Origem do código:** tabela `adhps` (5.956 registros) — fornece o código
ICAO válido e o tipo (AD/HP), mas **NÃO tem geometria**.

**Origem da coordenada (PROVISÓRIA):** como a `adhps` não guarda posição e
nenhuma outra tabela do banco a tem (confirmado por diagnóstico), as
coordenadas vêm de `data/aerodromos_br_ourairports.csv` — derivado da base
pública OurAirports (domínio público), filtrada para 4.677 aeródromos
brasileiros. Validada contra a `adhps`: SBMT resolve em (-46.6378, -23.5091).

**Como entram no grafo:** o usuário informa só o ICAO (ex.: "SBMT"). O
`CsvResolver` (resolver.py) traduz ICAO → coordenada e cria o nó terminal.
Depois, `add_synthetic_edges()` liga esse aeródromo aos waypoints próximos
(raio de 30 NM) com arestas sintéticas, e cria também a aresta direta
origem→destino (garante que sempre existe uma solução possível).

**Caminho definitivo (futuro):** quando o banco interno tiver a coordenada
oficial, troca-se o `CsvResolver` pelo `AdhpsGeomResolver` em uma linha —
o resto do motor não muda. Ver `PERGUNTA_ADMIN_BANCO.md`.

---

## 4. Como tudo se junta no algoritmo (GWO)

1. **Monta o subgrafo:** nós (waypoints das cartas próximas) + nós terminais
   (aeródromos resolvidos) + arestas (corredores reais com peso geodésico +
   arestas sintéticas de ligação).
2. **GWO otimiza prioridades:** cada "lobo" é um vetor de notas sobre os nós.
3. **Decodificador gera a rota:** caminha da origem ao destino seguindo as
   notas, sempre por arestas válidas do digrafo.
4. **Fitness avalia:** soma das distâncias + penalidades (rota incompleta,
   corredor obrigatório ignorado).
5. **Saída V1:** lista ordenada de pontos, corredores usados, distância
   direta, distância total e justificativa.

---

## 5. Visualização

- `plot_route.py` → zoom REGIONAL no subgrafo da rota (inspeção de perto).
- `plot_national.py` → malha NACIONAL inteira sobre o contorno do Brasil
  (padrão do script original), com a rota e os aeródromos destacados.

```bash
source .env.sh
python3 -m nexatlas_router.plot_national            # só a malha do país
python3 -m nexatlas_router.plot_national SBMT SBJD  # malha + rota destacada
```
