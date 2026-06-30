# nexatlas_router — Motor de rotas V1 (VFR com corredores visuais)

Motor de roteamento lateral VFR sobre a malha de corredores **REA** do esquema
`published` (banco `jetstream`). A rota é uma sequência

```
origem → [waypoints REA ligados por corredores e trechos DIRETO] → destino
```

e o objetivo é **menor distância lateral total** respeitando a regra
operacional VFR (abaixo).

> **Quem decide a rota.** Em qualquer caso com TMA REA, a rota principal vem do
> caminho mínimo EXATO com estado de fase (`dijkstra.shortest_route`), que é
> determinístico e correto. As **alternativas** ("próximas melhores rotas") vêm
> do **k-shortest exato (algoritmo de Yen, `dijkstra.k_shortest_routes`)** sobre
> o MESMO grafo com fase — logo também são válidas (respeitam `owes`/`used`),
> distintas e ordenadas por distância. O **Grey Wolf Optimizer (GWO)** NÃO
> participa mais do resultado V1; permanece no código reservado a trabalho
> multiobjetivo futuro (V2/V3). (Isto inverte e simplifica o desenho antigo, em
> que o GWO era o motor principal e depois o gerador de alternativas; ver
> "Histórico" no fim.)

## Regra de negócio central (do piloto orientador)

- Dentro de uma TMA que publica corredores REA, **voa-se os corredores** — não
  se corta reto tangenciando um waypoint.
- O trecho DIRETO (voo livre) só é permitido para: **entrar** na malha
  (origem → portal), **sair** da malha (nó de saída → destino) e **saltar**
  entre duas TMAs diferentes (ponte inter-TMA).
- **Entrar numa TMA obriga a voar o corredor dela.** Pousar num portal (por
  entrada ou por ponte) e sair reto é proibido.
- Sai-se de um corredor só num **terminal natural** (nó cujo corredor de saída
  não progride mais rumo ao destino).
- Se a REA de uma TMA não está entre origem e destino, ela é irrelevante e não
  aparece. Se nenhuma ponta está em TMA REA, a rota pode ser direta.
- Entre as opções que respeitam o acima, prefere-se a **menor distância total**.

## Arquitetura

```
nexatlas_router/
├── geo.py          # haversine, conversões; ponto único da inversão lon/lat
├── graphmodel.py   # RouteGraph: nós, arestas reais, arestas sintéticas
│                   #   (entrada/saída/ponte + escala de rota longa);
│                   #   requires_corridor; portões mínimo-local
├── db.py           # extração PostGIS do esquema published (cartas, waypoints,
│                   #   conexões, adhps); ST_X/ST_Y/ST_Length; filtro type='REA'
├── dijkstra.py     # shortest_route: caminho mínimo EXATO com estado de fase
│                   #   (AUTORIDADE da rota); k_shortest_routes: as K melhores
│                   #   rotas válidas (Yen) p/ alternativas; _phase_shortest
│                   #   (núcleo compartilhado); dijkstra simples (sem fase)
├── gwo.py          # Grey Wolf Optimizer + decodificador guloso
│                   #   (RESERVADO V2/V3; não alimenta mais o resultado V1)
├── v1.py           # orquestração plan_v1_route; route_source; fallback direto;
│                   #   alternativas via k_shortest_routes
└── plot_route.py   # plot_v1_route (rota principal) + plot_v1_alternatives
│                   #   (2º mapa, rotas candidatas)
raiz: nexatlas_cli.py (REPL), run_v1.py (uso único), diagnose_route.py
```

## Como rodar

```bash
# 1. ambiente + credenciais
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
source .env.sh                 # exporta NEXATLAS_DB_* (esquema published)

# 2. CLI interativo (uso principal): Tab autocompleta ICAO; abre o PNG da rota
#    e, quando há alternativas, também o 2º PNG (rotas candidatas)
python3 nexatlas_cli.py

# 3. uso único / automação (imprime o JSON da V1 e salva o(s) PNG(s))
python3 run_v1.py SBBH SBMT

# 4. diagnóstico de por que uma rota cai no fallback direto
python3 diagnose_route.py SBBH SBMT
```

Credenciais via ambiente (defaults do `published`/`jetstream`, sobrescrevíveis):
`NEXATLAS_DB_HOST=jetstream.nexatlas.com`, `NEXATLAS_DB_PORT=5433`,
`NEXATLAS_DB_NAME=jetstream`, `NEXATLAS_DB_USER`, `NEXATLAS_DB_PASSWORD`.
As credenciais ficam SÓ no `.env.sh` local (protegido pelo `.gitignore`).

## Casos de referência (bateria rápida no banco real)

| Caso | O → D | Esperado |
|---|---|---|
| REA saída **e** chegada (TMAs próximas) | `SBBH → SBMT` | corredores das DUAS cartas; ponte curta entre elas; sem salto único que pula a TMA do destino |
| REA saída **e** chegada (TMAs distantes, >300 NM) | `SBBH → SBMG` | corredores das DUAS cartas + um **DIRETO longo entre portões** no vão; `meta["..."]` com `bridges_long_haul ≥ 1`; sem fallback direto |
| Sem REA | `SBHT → SBPJ` | direto |
| REA só na saída | `SBLO → SBTG` | voa a TMA de origem, depois DIRETO longo |
| REA só na chegada | `SBMO → SBRF` | DIRETO, entra na TMA do destino, voa até o portão |
| REA irrelevante | `SWWA → SBUL` | direto (corredores não estão entre O e D) |

Para os casos com TMA, confirme `meta["route_source"] == "dijkstra-fase"`. Esses
casos também produzem um 2º PNG com as alternativas (próximas melhores rotas).

## Como funciona (resumo)

### Modelo do grafo (`graphmodel.py`)

Nós são aeródromos e waypoints REA (cada waypoint tem `chart` = a TMA).
Arestas **reais** são os corredores do banco (direcionados, peso = comprimento
real do corredor, podem ser `is_mandatory`). `add_synthetic_edges` cria as
arestas **sintéticas** "DIRETO":

- **Entrada**: origem → portais. Se a origem está em TMA, só aos portais da
  **própria carta** (entrar já obriga a voar aquela TMA).
- **Saída**: nó → destino, só de nós **alcançados por corredor**
  (`_has_real_incoming`) — impede usar um waypoint solto como trampolim.
- **Ponte (inter-TMA)**: nó da carta A → portão da carta B (A≠B), rumo ao
  destino, com alvo que tenha corredor de saída e por **mínimo-local**
  (portão → portão, nunca pelo meio do corredor) e sem cruzar obrigatório. Cap
  ~300 NM (`inter_tma_nm`): quem evita os "pulos" é a regra de fase + os
  portões, não um número apertado.
- **Escala de rota longa**: quando **ambas** as pontas estão em TMA e, mesmo
  após as pontes ≤300 NM e a válvula, a malha **não conecta** (`_reaches` é
  falso — TMAs genuinamente distantes, ex.: BH↔Londrina ~366 NM), liberam-se
  pontes **acima** do teto, mas SÓ portão→portão e SÓ se a reta não cruzar
  corredor obrigatório, progressivas e parando assim que conectar (perna de
  ligação mínima). Resultado: `[corredor de saída][DIRETO longo entre portões]
  [corredor de chegada]`. Contador `bridges_long_haul`. Como é condicional a
  `not _reaches`, não compete com nenhuma rota válida e não reabre atalhos sobre
  malha que existe (ver "Por que o teto e a escala não conflitam").
- **Direto origem→destino**: só se **nenhuma** ponta está em TMA.
- **Válvulas de segurança** forçam o mínimo de pontes se a malha ficaria
  desconexa (senão duas TMAs cairiam indevidamente no direto).

`requires_corridor` fica `True` quando alguma ponta está em TMA REA: sinaliza
ao `v1` que a rota deve usar ≥1 corredor e que a rota exata é a autoridade.

#### Por que o teto (~300 NM) e a escala de rota longa não conflitam

O teto e a escala parecem ambos mexer em pontes, mas atuam em casos **disjuntos**,
separados pelo `_reaches` (existe caminho origem→destino no grafo?):

- **`_reaches = True`** (há rota válida por corredores): o teto manda. Ele corta
  pontes longas demais para que o Dijkstra, que só minimiza distância, não
  prefira um salto reto que "rasparia" malha que deveria ser voada (o caso em
  que um atalho é uns poucos NM mais curto que a rota correta). A escala de rota
  longa **nem roda**.
- **`_reaches = False`** (nenhuma malha conecta as TMAs): não há rota válida
  concorrente para um atalho atropelar — a única alternativa seria o fallback
  reto que pula tudo. Aí a escala entra e troca esse fallback pela combinação
  corredor + ponte longa + corredor.

A janela do teto é estreita e sai das distâncias reais entre cartas vizinhas: na
amostra de 5 cartas, o maior par que ainda deve conectar como vizinho é
Rio×Florianópolis ≈ 295 NM e o menor par que já deve ser tratado como distante é
Rio×Londrina ≈ 321 NM — daí 300 cair na faixa `[295, 321)`. É um valor calibrado;
a designação publicada de portões de entrada (quando existir no banco) tornaria o
teto desnecessário.

### Caminho mínimo com fase (`dijkstra.shortest_route`)

Dijkstra de fixação de rótulos sobre o estado `(nó, owes, used)`:

- `owes=1`: dentro de um corredor; **não** pode pegar DIRETO agora.
- `used=1`: já voou ≥1 corredor real (exigido quando uma ponta está em TMA).

A obrigação depende de **como** se chega ao nó (assimetria essencial):

- chegou por **corredor real** → `owes=1` só se há corredor de saída que
  **progride** rumo ao destino; senão `owes=0` (terminal natural, pode sair);
- chegou por **trecho sintético** (entrada/ponte/saída) → `owes=1` se o nó tem
  **qualquer** corredor de saída (entrar obriga a voar).

Objetivo: `(destino, owes=0, used≥need)`. Isso reproduz, exata e
deterministicamente, toda a regra de negócio acima — inclusive nas rotas longas:
quando uma ponte (curta ou longa) **pousa** num portão da TMA destino, isso conta
como entrada por trecho sintético e dispara `owes=1`, obrigando a voar o corredor
de chegada. A escala de rota longa só fornece a aresta de ligação do vão; voar os
corredores das duas pontas é a fase quem garante.

Internamente, `shortest_route` delega a um núcleo genérico `_phase_shortest`
(aceita estado inicial de fase, custo inicial, e conjuntos de arestas/nós
proibidos). O `shortest_route` público mantém comportamento idêntico; o núcleo é
reaproveitado pelo k-shortest.

### Alternativas — k-shortest exato (`dijkstra.k_shortest_routes`, Yen)

As "próximas melhores rotas" vêm do algoritmo de **Yen** sobre o mesmo grafo com
fase. A 1ª rota é idêntica à de `shortest_route`; as seguintes são as próximas
melhores **distintas** e **válidas** (todas respeitam `owes`/`used`), ordenadas
por distância. Sutileza da implementação: cada sub-caminho "spur" parte do nó de
desvio **já com a fase acumulada** ao longo do trecho-raiz (replay das
transições), de modo que nenhuma alternativa nasce inválida; candidatos com laço
ou duplicados são descartados. `v1` pede `k=5`, descarta a 1ª (= principal) e usa
as 4 seguintes. Determinístico — sem sementes nem convergência.

### GWO (`gwo.py`) — reservado para V2/V3

Cada lobo é um vetor de prioridades em `[0,1]^N`; um decodificador guloso anda
pelo digrafo escolhendo o sucessor de maior prioridade (toda rota é válida por
construção). Para o grafo da V1 (custo aditivo de distância, ~50–150 nós), o
caminho mínimo exato é superior em tudo, e o GWO **não é mais usado** na V1 —
nem para a rota, nem para as alternativas (essas vêm do k-shortest exato). O GWO
permanece no código porque uma metaheurística volta a fazer sentido quando o
objetivo deixar de ser separável (ex.: perfil vertical da V3).

## Hiperparâmetros de referência

| Parâmetro | Valor | Observação |
|---|---|---|
| `chart_radius_nm` | 60 | raio de descoberta de cartas (`db.build_subgraph`) |
| `tma_radius_nm` | 60 | raio que decide se a ponta está "em TMA" |
| `inter_tma_nm` | 300 | cap das pontes inter-TMA; acima dele só a escala de rota longa (condicional a `not _reaches`) |
| `link_radius_nm` | 30 | mantido por compat.; o modelo usa k-vizinhos |
| `k` (alternativas) | 5 | k-shortest (Yen); descarta a 1ª (= principal), usa 4 |

Parâmetros do GWO (`n_wolves=30`, `n_iterations=200`, `patience=50`,
`max_hops=80`) continuam em `GWOConfig`, mas **hoje não influenciam a saída da
V1** (GWO dormante; reservado V2/V3).

Dependências: `numpy`, `matplotlib`, `psycopg2-binary` (e `geopandas` só para
`plot_national`).

## Pontos de atenção (dívida técnica conhecida)

1. **Perna longa não validada no vão intermediário.** A escala de rota longa
   conecta TMAs distantes com uma reta única entre portões (~330 NM no
   `SBBH→SBMG`). O portão geométrico garante que ela não corta corredor
   obrigatório, mas segmentar/validar o espaço aéreo do vão (vento, NOTAM,
   terreno, airspaces) é responsabilidade da V2 — a V1 resolve a **topologia**,
   não a validação do trajeto livre.
2. **Código morto / docstrings defasadas (graphmodel).**
   `graphmodel` ainda expõe o contador `exits_locked_by_continuity` (sempre 0,
   resquício da antiga "Trava de Continuidade", hoje inativa) e o docstring do
   topo do módulo ainda pode descrevê-la como ativa. Limpeza pendente.
3. **`_has_real_incoming` é O(E) por chamada** dentro de laços de montagem
   (O(V·E) no total). Tolerável no subgrafo regional; trivial pré-computar um
   conjunto de nós com corredor de entrada uma única vez.
4. **`is_mandatory` está FORA do custo.** É carregado na `Edge` e usado só para
   classificar `[Obrigatório]/[Opcional]` e o estilo do mapa. Confirmar com o
   piloto se o campo significa "obrigado a reportar" (fonia) ou "a passar".

> Resolvidos nesta iteração: o CLI agora exibe o **método real** ("Dijkstra com
> estado de fase (exato)") em vez de "iterações GWO"; as alternativas deixaram
> de vir do GWO (que podia violar a fase) e passaram ao k-shortest exato — logo
> são sempre válidas; e o shim `owes_at`/docstring "oráculo de testes" do
> `dijkstra.py` saiu na reescrita.

## Histórico (por que o GWO não é mais o motor)

O motor nasceu em torno do GWO (esquema `_v2`, Dijkstra como mero "oráculo de
testes"). Para um grafo desse tamanho (~50–150 nós) com custo aditivo de
distância, o caminho mínimo exato com estado de fase é superior em tudo:
correto, determinístico, rápido e fácil de restringir. O GWO foi então rebaixado
a gerador de alternativas e, nesta iteração, **removido também desse papel** —
as alternativas passaram ao k-shortest exato (Yen) sobre o mesmo grafo com fase.
O GWO segue no código, reservado ao momento em que o objetivo deixar de ser
separável (ex.: perfil vertical da V3) — e mesmo aí o padrão é resolver a rota
lateral exata e otimizar o vertical em cima.

## Segurança

Credenciais **nunca** no código ou em commits — sempre em `.env.sh` (já no
`.gitignore`, junto com `*.png` e `CLAUDE.md`). Confirmar se a senha que vazou
num script compartilhado foi rotacionada.