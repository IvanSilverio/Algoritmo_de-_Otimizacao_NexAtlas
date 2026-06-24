# nexatlas_router — Motor de rotas V1 (VFR com corredores visuais)

Motor de roteamento lateral VFR sobre a malha de corredores **REA** do esquema
`published` (banco `jetstream`). A rota é uma sequência

```
origem → [waypoints REA ligados por corredores e trechos DIRETO] → destino
```

e o objetivo é **menor distância lateral total** respeitando a regra
operacional VFR (abaixo).

> **Quem decide a rota.** Em qualquer caso com TMA REA, a rota vem do
> caminho mínimo EXATO com estado de fase (`dijkstra.shortest_route`), que é
> determinístico e correto. O **Grey Wolf Optimizer (GWO)** roda apenas para
> popular **alternativas** informativas — ele não conhece a regra de fase e
> nunca sobrepõe a rota exata. (Isto inverte o desenho antigo, em que o GWO
> era o motor principal; ver "Histórico" no fim.)

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
│                   #   (entrada/saída/ponte); requires_corridor
├── db.py           # extração PostGIS do esquema published (cartas, waypoints,
│                   #   conexões, adhps); ST_X/ST_Y/ST_Length; filtro type='REA'
├── dijkstra.py     # shortest_route: caminho mínimo EXATO com estado de fase
│                   #   (AUTORIDADE da rota); dijkstra simples (sem fase)
├── gwo.py          # Grey Wolf Optimizer + decodificador guloso (alternativas)
├── v1.py           # orquestração plan_v1_route; route_source; fallback direto
└── plot_route.py   # plotagem regional da rota
raiz: nexatlas_cli.py (REPL), run_v1.py (uso único), diagnose_route.py
```

## Como rodar

```bash
# 1. ambiente + credenciais
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
source .env.sh                 # exporta NEXATLAS_DB_* (esquema published)

# 2. CLI interativo (uso principal): Tab autocompleta ICAO, abre o PNG
python3 nexatlas_cli.py

# 3. uso único / automação (imprime o JSON da V1 e salva o PNG)
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
| REA saída **e** chegada | `SBBH → SBMT` | corredores das DUAS cartas; sem salto único que pula a TMA do destino |
| Sem REA | `SBHT → SBPJ` | direto |
| REA só na saída | `SBLO → SBTG` | voa a TMA de origem, depois DIRETO longo |
| REA só na chegada | `SBMO → SBRF` | DIRETO, entra na TMA do destino, voa até o portão |
| REA irrelevante | `SWWA → SBUL` | direto (corredores não estão entre O e D) |

Para os casos com TMA, confirme `meta["route_source"] == "dijkstra-fase"`.

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
- **Ponte**: nó da carta A → nó da carta B (A≠B), rumo ao destino, com alvo que
  tenha corredor de saída. Cap generoso (~300 NM): quem evita os "pulos" é a
  regra de fase, não um número apertado.
- **Direto origem→destino**: só se **nenhuma** ponta está em TMA.
- **Válvulas de segurança** forçam o mínimo de pontes se a malha ficaria
  desconexa (senão duas TMAs cairiam indevidamente no direto).

`requires_corridor` fica `True` quando alguma ponta está em TMA REA: sinaliza
ao `v1` que a rota deve usar ≥1 corredor e que a rota exata é a autoridade.

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
deterministicamente, toda a regra de negócio acima.

### GWO (`gwo.py`) — só alternativas

Cada lobo é um vetor de prioridades em `[0,1]^N`; um decodificador guloso anda
pelo digrafo escolhendo o sucessor de maior prioridade (toda rota é válida por
construção). O fitness é distância + penalidade por rota incompleta. Como o GWO
não conhece a fase, ele só preenche `alternatives`; a rota escolhida nos casos
com TMA é sempre a do `shortest_route`.

## Hiperparâmetros de referência

| Parâmetro | Valor | Observação |
|---|---|---|
| `n_wolves` | 30 | suficiente até ~250 nós |
| `n_iterations` | 200 | `patience=50` interrompe antes se convergir |
| `max_hops` | 80 | teto do decodificador (rotas longas, múltiplas TMAs) |
| `chart_radius_nm` | 60 | raio de descoberta de cartas (`db.build_subgraph`) |
| `tma_radius_nm` | 60 | raio que decide se a ponta está "em TMA" |
| `inter_tma_nm` | 300 | cap (generoso) das pontes inter-TMA |
| `link_radius_nm` | 30 | mantido por compat.; o modelo usa k-vizinhos |

Dependências: `numpy`, `matplotlib`, `psycopg2-binary` (e `geopandas` só para
`plot_national`).

## Pontos de atenção (dívida técnica conhecida)

1. **CLI: o rótulo "iterações GWO" é enganoso.** `nexatlas_cli.py` imprime
   "Convergência: N iterações GWO" mesmo quando a rota veio do `dijkstra-fase`.
   A fonte real está em `result.meta["route_source"]`; o CLI deveria mostrá-la.
2. **Código morto / docstrings defasadas.**
   - `graphmodel._has_mandatory_real_exit` (antiga "Trava de Continuidade") não
     é mais chamado; `locked_out`/`exits_locked_by_continuity` ficam sempre 0.
     O docstring do topo de `graphmodel.py` ainda descreve a trava como ativa.
   - `dijkstra.owes_at` é um shim `# compat.` não usado, e o docstring do módulo
     ainda diz "não faz parte do produto final" (hoje é a autoridade da rota).
3. **`_has_real_incoming` é O(E) por chamada** dentro de laços de montagem
   (O(V·E) no total). Tolerável no subgrafo regional; trivial pré-computar um
   conjunto de nós com corredor de entrada uma única vez.
4. **As alternativas do GWO podem violar a regra de fase** (ele não a conhece).
   São apenas informativas — não tratar como rotas válidas equivalentes.
5. **`is_mandatory` está FORA do custo.** É carregado na `Edge` e usado só para
   classificar `[Obrigatório]/[Opcional]` e o estilo do mapa. Confirmar com o
   piloto se o campo significa "obrigado a reportar" (fonia) ou "a passar".

## Histórico (por que o GWO não é mais o motor)

O motor nasceu em torno do GWO (esquema `_v2`, Dijkstra como mero "oráculo de
testes"). Para um grafo desse tamanho (~50–150 nós) com custo aditivo de
distância, o caminho mínimo exato com estado de fase é superior em tudo:
correto, determinístico, rápido e fácil de restringir. O GWO foi mantido,
porém rebaixado a gerador de alternativas. Uma metaheurística só "ganha o
lugar" quando o objetivo deixa de ser separável (ex.: perfil vertical da V3) —
e mesmo aí o padrão é resolver a rota lateral exata e otimizar o vertical em
cima.

## Segurança

Credenciais **nunca** no código ou em commits — sempre em `.env.sh` (já no
`.gitignore`, junto com `*.png` e `CLAUDE.md`). Confirmar se a senha que vazou
num script compartilhado foi rotacionada.