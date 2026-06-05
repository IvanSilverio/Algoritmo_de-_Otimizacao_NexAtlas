# nexatlas_router — Motor de rotas V1 (VFR com corredores visuais)

Motor de otimização de rota lateral VFR usando **Grey Wolf Optimizer** com
codificação por prioridades sobre o digrafo de `special_routes_waypoints_v2` /
`special_routes_connections_v2` (esquema BD_Nex_2_0), com **Dijkstra como
oráculo de testes**.

**Regra de negócio central (piloto orientador):** se existir corredor visual
aplicável à saída ou à chegada, a passagem por ele é OBRIGATÓRIA. A rota
direta só é válida quando nenhum corredor se aplica. Implementada via
`GWOConfig.enforce_corridor_rule` (default True).

## Arquitetura

```
nexatlas_router/
├── geo.py          # haversine, conversões; ponto único da inversão lon/lat
├── graphmodel.py   # RouteGraph: nós, arestas direcionadas, arestas sintéticas
├── db.py           # extração PostGIS (cartas, waypoints, conexões, adhps)
├── gwo.py          # GWO original + decodificador guloso + fitness
├── dijkstra.py     # oráculo exato para validação
├── v1.py           # orquestração e saída no formato do escopo V1
├── plot_route.py   # visual no padrão do mapa nacional, com rota destacada
└── demo.py         # execução sem banco (cluster sintético + 2 cenários)
```

## Rodar o demo (sem banco)

```bash
python -m nexatlas_router.demo
```

Cenário 1: rota livre — GWO deve igualar o Dijkstra (gap ≤ 1%).
Cenário 2: corredor obrigatório de chegada — GWO deve rotear pelo portão.

## Uso com o banco real

```python
import psycopg  # ou psycopg2
from nexatlas_router.db import PostgisLoader
from nexatlas_router.v1 import plan_v1_route
from nexatlas_router.gwo import GWOConfig

conn = psycopg.connect("postgresql://user:pass@host/db")
loader = PostgisLoader(conn, region="BRA")

# Enquanto a tabela de coordenadas de adhps não for mapeada,
# passe lon/lat manualmente (ordem do banco: LONGITUDE primeiro):
graph, meta = loader.build_subgraph(
    "SBMT", "SBJD",
    origin_lonlat=(-46.6377, -23.5092),
    dest_lonlat=(-46.9436, -23.1817),
    chart_radius_nm=60.0, link_radius_nm=30.0,
)

result = plan_v1_route(graph, meta["origin_id"], meta["dest_id"],
                       GWOConfig(n_wolves=30, n_iterations=200, seed=42))
print(result.to_dict())
```

## Decisões de projeto

- **Lobo = vetor de prioridades em [0,1]^N**, não a rota. As equações do GWO
  (Mirjalili, 2014) operam intactas no espaço contínuo; o decodificador guloso
  traduz prioridades em rota caminhando apenas por arestas reais do digrafo,
  garantindo validade topológica de 100% das soluções.
- **Pesos nascem no banco**: `ST_Length(c.geom::geography)` mede o corredor
  REAL (a LineString nativa das conexões v2, inclusive curvas), com fallback
  para `ST_DistanceSphere` entre os nós. O fitness apenas soma pesos.
- **Obrigatoriedade de corredor**: `corridor_nodes_near()` detecta waypoints
  de corredores reais num raio da origem/destino; o fitness penaliza com μ
  qualquer rota completa que não atravesse esses conjuntos quando existem.
- **Penalidades graduadas** (múltiplos da distância direta): beco sem saída
  recebe `M + λ·distância_restante` — cria gradiente para a matilha; corredor
  obrigatório ignorado recebe `μ`.
- **Aresta direta sintética sempre existe** → sempre há solução factível, e a
  comparação corredor × direta (exigida pelo escopo) sai de graça.
- **Subgrafo regional**: descoberta de cartas por `ST_DWithin` ao redor de
  origem/destino. Nunca carregar a malha nacional.

## Pendências conhecidas

1. **`adhps` NÃO tem geometria** (confirmado em BD_Nex_2_0: tabela
   "cabeçalho" com id, icao, type). As coordenadas vivem em tabela ainda não
   identificada. Enquanto isso: (a) rode `SQL_FIND_COORD_TABLE` (em db.py)
   no banco para listar tabelas com geometria e localizar a fonte; (b) use
   `fetch_aerodrome(icao, lonlat=(lon, lat))` manualmente; (c) quando
   identificar, configure `PostgisLoader(aerodrome_coord_sql=...)`.
2. **`is_mandatory` está FORA do fitness** (decisão desta iteração).
   Observação para levar ao piloto orientador: o dicionário v2 descreve o
   campo como "obrigado a REPORTAR o ponto" (fonia ATC), diferente de
   "obrigado a PASSAR". Se confirmado como reporte, o campo pertence à
   camada de apresentação. O dado segue sendo carregado na Edge.
3. **Filtro de altitude por aeronave** (`aircraft_models.operational_ceiling`
   vs `lower_limit` da conexão): hook previsto, entra na V3.
4. Índices recomendados no banco: GIST em `special_routes_waypoints.geometry`;
   B-tree em `special_routes_connections.chart`, `source_id`, `target_id`.

## Hiperparâmetros de referência

| Parâmetro      | Valor | Observação                                   |
|----------------|-------|----------------------------------------------|
| n_wolves       | 30    | suficiente para clusters de até ~250 nós     |
| n_iterations   | 200   | com patience=50 raramente roda tudo          |
| max_hops       | 40    | teto de segurança do decodificador           |
| link_radius_nm | 30    | raio de ligação aeródromo ↔ corredores       |

Dependências: `numpy` (núcleo) e `psycopg`/`psycopg2` (apenas `db.py`).

## Setup inicial (primeira vez)

```bash
# 1. dentro da pasta nexatlas_v1/
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. credenciais: copie o modelo e preencha a senha
cp .env.sh.exemplo .env.sh
nano .env.sh          # edite a linha NEXATLAS_DB_PASSWORD

# 3. a cada nova sessão de terminal, carregue ambiente + credenciais:
source .venv/bin/activate
source .env.sh
```

As credenciais ficam SÓ no `.env.sh` local (protegido pelo `.gitignore`).
Nenhum arquivo do projeto contém senha.

## Conexão com o banco real

```bash
pip install psycopg2-binary numpy matplotlib
export NEXATLAS_DB_HOST=assistant.nexatlas.com
export NEXATLAS_DB_PORT=5433
export NEXATLAS_DB_NAME=jetstream_replica
export NEXATLAS_DB_USER=seu_usuario
export NEXATLAS_DB_PASSWORD=sua_senha

# 1. Diagnóstico (localizar coordenadas da adhps) — cole a saída no chat:
python diagnose_db.py

# 2. Rota de ponta a ponta (lon/lat manual enquanto a adhps não é resolvida):
python run_v1.py SBMT SBJD --origin-lonlat -46.6377 -23.5092 \
                           --dest-lonlat   -46.9436 -23.1817
```

No Windows (PowerShell), troque `export` por `$env:NEXATLAS_DB_HOST="..."`.

## Plotagem

```bash
python -m nexatlas_router.plot_route        # demo sintético -> rota_v1_demo.png
```

Integrado (após plan_v1_route):

```python
from nexatlas_router.plot_route import plot_v1_route
plot_v1_route(graph, result, "rota_SBMT_SBJD.png")
```

Mesmo padrão visual do script nacional (fundo #0f172a, plasma, nós ciano),
com zoom regional automático, rota em destaque e nomes de fonia anotados.

## Segurança

NUNCA versionar credenciais em código (o script de plotagem original contém
usuário/senha em texto puro — rotacionar a senha e migrar para variáveis de
ambiente, ex.: `os.environ["NEXATLAS_DB_URL"]`).
