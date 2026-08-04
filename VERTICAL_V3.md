# Camada V3 — Navegação vertical (perfil de altitude)

A V3 calcula o **perfil vertical** (subida → cruzeiro → descida) por cima da
rota lateral que a V1 já produz. Ela é uma **camada separada** e **opt-in**: a
V1 continua rodando exatamente como antes, sem saber que a V3 existe.

## Arquitetura e separação (importante)

- A V3 vive no subpacote `nexatlas_router/vertical/`.
- A **dependência é unidirecional**: a V3 importa a V1; **a V1 nunca importa a
  V3**. Por isso, para consertar um problema da V1 você pode me enviar o projeto
  **sem a pasta `nexatlas_router/vertical/`** — o lateral roda e conserta 100%
  sozinho, sem o peso da V3.
- A fronteira entre as camadas é um **contrato** (`vertical/contract.py`,
  estrutura `LateralRoute`). A V3 consome só o contrato; o único ponto que
  "olha" a V1 é o adaptador `lateral_route_from_v1(graph, route)`. Se você
  reescrever o Dijkstra / as pontes da V1, enquanto ela emitir uma rota
  adaptável, a V3 não é afetada.
- Os únicos "ganchos" adicionados na V1 são **aditivos e inertes** (não deixam a
  V1 pesada nem a fazem depender da V3):
  - `Edge.heading` (proa magnética do corredor) — em `graphmodel.py`/`db.py`;
  - `geo.initial_bearing(a, b)` — proa verdadeira, usada só pela V3;
  - `V1RouteResult.route` — expõe a rota p/ a V3; **não** entra no `to_dict`
    (a serialização do `run_test_cases.py` fica intacta).

## Como rodar

```bash
source .env.sh                     # credenciais do jetstream (NÃO versionar)
pip install -r requirements.txt    # inclui pygeomag (WMM). O terreno usa só urllib.
python nexatlas_cli.py
```

Na CLI: escolha a **aeronave** (lista das que têm performance completa em
`aircraft_models` — hoje 26), depois digite **origem** e **destino** (ICAO).
Além da rota lateral, aparece o **perfil vertical** (cruzeiro, TOC/TOD, tempo por
fase e altitude por trecho). Se você der Enter na escolha da aeronave, a V3 é
pulada e só a rota lateral é exibida.

> `pygeomag` é **obrigatório** para a V3: `magnetic.declination()` não tem mais
> fallback — se o WMM não estiver disponível, ela **levanta erro** em vez de
> chutar uma declinação (chutar dá paridade semicircular, e portanto altitude
> de cruzeiro, errada). A CLI captura esse erro na hora de montar o perfil e
> **segue só com a V1** (a V3 é desativada com aviso, sem quebrar nada) — mas
> o correto é instalar o `pygeomag` e ter o cálculo real.

## Acesso ao CDN de terreno (o que mexer, se precisar)

O terreno é lido **da nuvem** por `nexatlas_router/vertical/terrain.py`, que
baixa os tiles FlatBuffer do CDN público:

```
BASE_URL = "https://jetstream-data-cdn.nexatlas.com/bra/terrain_fb"
  metadata: {BASE_URL}/metadata.fb
  tiles:    {BASE_URL}/{timestamp}/{level}/{z}/{x}/{y}.fb
```

Usa só `urllib` (stdlib) — **sem dependência extra e sem autenticação**. O
`Terrain()` baixa o `metadata.fb` uma vez, escolhe zoom/level/timestamp e faz
cache dos tiles em memória.

**Teste isolado do CDN na sua máquina** (não precisa de banco):
```bash
python nexatlas_router/vertical/terrain.py
```
Deve imprimir o metadata e elevações de referência (ex.: SBBH ≈ 789 m). Se isso
funcionar, a integração do terreno na CLI vai funcionar.

Ajustes, **só se necessário**:
- **Outro host/URL do CDN:** troque `BASE_URL` em `terrain.py`, ou passe
  `Terrain(base_url="https://…")`. Na CLI, o `Terrain()` é criado em
  `plan_from_v1(graph, result, aircraft, Terrain())` — dá para passar o
  `base_url` aí.
- **Proxy corporativo:** o `urllib` respeita as variáveis `HTTPS_PROXY` /
  `HTTP_PROXY` do ambiente. Ex.: `export HTTPS_PROXY=http://proxy:porta`.
- **Timeout/lentidão:** o `_http_get` usa timeout de 30 s; aumente se a sua rede
  for lenta para os primeiros tiles.
- **CDN indisponível:** a V3 não derruba a rota — ela emite avisos e o cálculo
  de terreno degrada; a rota lateral continua sendo exibida.

## Parâmetros regulamentares (defaults, ajustáveis)

Em `vertical/rules.py` e nos argumentos de `plan_vertical_profile(...)`:
- `margem_ft = 1000` — folga de terreno (RBAC 91.119, área habitada).
- raio de obstáculo `600 m` → `radius_px ≈ 4` em z=10 (~150 m/pixel).
- `step_nm = 0.5` — passo de amostragem do terreno ao longo da perna.

O **enquadramento semicircular** (VFR: milhar ±500 por direção; IFR/RVSM:
milhar cheia) não vive mais em `rules.py` — está em dois lugares:
- `cruise.py` (Etapa 3 da spec) enquadra o alvo distância×teto no nível legal.
- `profile.py::_niveis_legais/_nivel_legal_acima` reenquadra esse resultado
  quando o **piso de terreno obrigatório** (terreno máximo no trecho en-route
  + 500 ft) fica acima do cruzeiro sugerido: sobe para o menor nível legal que
  respeite o piso, ou para o maior nível legal com aviso forte se o piso passar
  do teto operacional da aeronave.

Para mudar (ex.: 500 ft fora de área habitada):
```python
plan_from_v1(graph, route, aeronave, Terrain(), margem_ft=500, radius_px=2)
```

## O que ainda precisa ser validado na sua máquina

Aqui no ambiente de desenvolvimento o CDN e o banco `jetstream` **não** são
alcançáveis, então validei toda a matemática com terreno stub. Na sua máquina,
confirme:
1. `python nexatlas_router/vertical/terrain.py` lê o CDN e imprime elevações
   coerentes.
2. `python nexatlas_cli.py` com uma aeronave e uma rota real: a rota lateral sai
   igual à de antes, e o perfil vertical aparece com terreno real do CDN.
