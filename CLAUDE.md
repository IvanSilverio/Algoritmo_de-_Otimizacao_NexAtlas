# CLAUDE.md — NexAtlas Router

Guia para trabalhar neste repositório. Leia antes de propor ou fazer qualquer mudança.

---

## 0. Como colaborar (regras de trabalho)

- **Sempre em português do Brasil.**
- **Fluxo estrito: diagnóstico escrito → aprovação explícita → implementação.** Nunca faça mudanças especulativas. Explique o que vai fazer, espere um "sim" / "pode fazer", só então altere o código.
- **Nunca invente dados.** Use apenas fontes reais e verificáveis (o banco `jetstream`). Stubs, dumps offline e reconstruções **não são verdade** — servem para exercitar lógica, mas a validação final é sempre no banco + CDN ao vivo.
- **Documentos de referência primeiro.** PDFs de spec e transcrições de reunião são analisados antes de implementar; a spec é transcrita **fielmente** e validada contra os exemplos do próprio documento.
- **Uma mudança de cada vez, com regressão.** Rode a bateria de 100 casos antes de considerar algo estável.

---

## 1. O que é o projeto

Motor de planejamento de rotas **VFR/IFR** para o espaço aéreo brasileiro (corredores REA). Arquitetura em camadas versionadas:

| Camada | O que faz | Estado |
|---|---|---|
| **V1 — lateral VFR** | Rota lateral pelos corredores visuais REA (Dijkstra com estado de fase). | Estável |
| **V2 — lateral IFR** | Rota lateral por SID/STAR/IAC/aerovias. | Adiada (sem dados) |
| **V3 — vertical** | Perfil vertical (altitude, cruzeiro, subida/descida, TOC/TOD, tempos, combustível, vento) sobre a rota lateral. | Implementada |

**Costura V3 ↔ V1:** o contrato `nexatlas_router/vertical/contract.py::LateralRoute`. **A V3 importa a V1, nunca o contrário.** Enquanto a V1 produzir uma rota (nós + arestas), ela pode ser reescrita sem tocar na V3.

---

## 2. Banco de dados e credenciais

- DB **`jetstream`**, schema **`published`**, host `jetstream.nexatlas.com:5433`.
- Tabelas principais: `special_routes_waypoints`, `special_routes_connections`, `adhps`, `aircraft_models`.
- **Geometria em WKB hex; coordenadas em ordem `[longitude, latitude]`.**
- Credenciais em **`.env.sh`** — **NUNCA versionar** (já está no `.gitignore`, junto com `*.json`, `*.png`, `CLAUDE.md`).
- A CLI roda **localmente** contra o banco completo.

---

## 3. Como rodar e testar

```bash
pip install -r requirements.txt     # inclui pygeomag (WMM) — obrigatório para a V3
source .env.sh                      # credenciais (não versionado)
python3 nexatlas_cli.py             # pede a aeronave, roda V1 + V3, imprime o perfil e salva mapa/gráfico
```

- **Regressão V1:** bateria de 100 casos REA (`resultados_testes_REA.json`, via `run_test_cases.py`) — deve dar **100/100** plan+plot. Não toca na V3 (nem importa `nexatlas_router.vertical`).
- **Regressão V3 (múltiplas aeronaves × rotas, com vento):** `python3 testes_voos.py` (edite `AERONAVES`/`ROTAS`/`HORA_PARTIDA_UTC` no topo do arquivo) — roda a matriz contra o banco + CDN reais e gera `analise_voos.csv` + gráficos em `analise_voos/`.
- **Se a V3 não pedir a aeronave:** quase sempre é `pygeomag` faltando (`pip install pygeomag`) ou o `cruise.py` ausente em `vertical/`. Cheque com `python3 -c "import nexatlas_router.vertical"`.

---

## 4. V1 — motor lateral e regras críticas

- **Dijkstra com estado de fase** é o motor **autoritativo**. **GWO** só gera alternativas; **Yen's k-shortest** também para alternativas.
- **Regras/gotchas (bugs já resolvidos — não regredir):**
  - **Detecção de loop por ID do nó, NUNCA por nome.** Existem waypoints homônimos (ex.: dois `TREVO` a ~1563 NM). Nome dá falso positivo em rota válida.
  - **Corredores são voados no `higher_limit`.**
  - **Entrada de corredor é topológica** (cabeça de cadeia: `_has_real_incoming == False` e `_has_real_outgoing == True`), **não** pela string `"PORTÃO"` — muitos pontos de entrada não têm o nome, e muitos com o sufixo são de meio de cadeia.
  - **Esporão (vai-e-volta):** corrigido com `OWES_SYNTH_REACH_MARGIN_M = 5 NM` (gatilho 1) + laço anti-esporão de 2 passes por **node-ID** em `v1.plan_v1_route` (gatilho 2).
  - **Bridge overshoot:** `_overshoots_dest` em `graphmodel.py` descarta pontes que passam do destino.
  - **Obrigação de corredor é regional** (corredores de uma região são alternativas), **não** por corredor individual → usar `rule_satisfied`.
  - **Testar offline sem `require_real_edge=graph.requires_corridor` ESCONDE o bug do esporão.** Sempre testar com a flag real.

---

## 5. V3 — camada vertical (`nexatlas_router/vertical/`)

Módulos: `contract.py`, `terrain.py`, `wind.py`, `magnetic.py`, `rules.py`, `aircraft.py`, `cruise.py`, `profile.py`, `plot_profile.py`, `__init__.py`.

**Modelo (do piloto Vinícius + coordenador Cristiano):**

- **Saída = lista de VÉRTICES (reais + virtuais)** em `PerfilVertical` — fonte única do gráfico e do JSON. Vértice: `x_nm, alt_ft, tipo, nome, real`. Pontos virtuais marcam onde uma transição de altitude termina (não são pontos reais da rota).
- **Subida em degraus:** a aeronave sobe **SEMPRE na razão máxima** (`start_to`), atinge o `higher_limit` do corredor num **ponto virtual** e nivela até o próximo. Nada de rampa linear ao longo da perna. Em trecho curto, a subida "carrega" para a perna seguinte (é o máximo físico).
- **Descida em CROSS (só o 1º corredor de chegada) + START (corredores seguintes), sempre na razão do banco** (`rate_dc`/`speed_dc`, `_descida_final` em `profile.py`; TAREFA_descida_transicao_e_aviso.md, validado com o Vinícius 12/08/26): a aeronave **permanece na altitude máxima** (cruzeiro ou o corredor mais alto) até o **"TOD de aproximação"** — o ponto mais tarde possível a partir do qual, descendo na razão do banco, ela chega EXATAMENTE no `higher_limit` do **1º corredor de chegada**, na ENTRADA dele (isso é o TOD; único ponto que ainda usa a linha reta até um alvo). **Corredores de chegada SEGUINTES usam START, não cross:** a aeronave mantém o `higher_limit` do corredor atual até PASSAR o ponto (mesmo atravessando trechos DIRETO entre corredores) e só desce, na razão do banco, DENTRO da perna seguinte, para o `higher_limit` dela — nunca antes (é o mesmo mecanismo `start_to` da subida, mas descendo). Durante a transição em si — entre passar o ponto e alcançar o novo teto — é normal e esperado ficar acima do teto novo por um instante; isso NÃO é violação. O trecho final (fora de corredor, até o aeródromo) desce na razão máxima do banco.
  **O `higher_limit` de cada perna de corredor é OBRIGATÓRIO, não um alvo best-effort** (ajuste 14/08/26, reportado pelo Ivan com SBPA→SBFL/TUCA a partir de PAPAGAIO): se a razão do banco não é suficiente para chegar lá a tempo — no 1º corredor, numa transição seguinte, ou no trecho final — a aeronave usa a razão REALMENTE necessária (única opção física; o piloto reduz velocidade / faz espiral), e o trecho fica marcado como íngreme em `PerfilVertical.descida_ingreme_nm` (lista de `(x0_nm, x1_nm)`) — vermelho no gráfico e no terminal, com aviso mostrando a razão necessária. **Nunca "carregar" a violação silenciosamente para a perna seguinte** (bug corrigido: antes, uma perna curta demais deixava a aeronave acima do próprio teto e esse valor "vazava" para as pernas seguintes, em cascata, sem aviso).
- **TOC/TOD:** 1º/último ponto no topo. Sem cruzeiro nivelado (só corredores / rota curta) → **intervalo da altitude máxima** do perfil.
- **Tempo por segmento:** subida/descida = `Δalt / razão` (min); nivelado = `distância / velocidade`. (Contar subida/descida pela razão, não por velocidade × distância.)
- **Terminal (`nexatlas_cli.py::_print_vertical`, `testes_voos.py::imprimir_vertices`)** mostra, por trecho, a razão (fpm) e a velocidade (kt) — para verificação. A **velocidade do banco é FIXA e a razão é DERIVADA** do trecho real (`Δalt × velocidade / (Δx × 60)`), NUNCA o contrário — dá a razão nominal certa nos trechos normais e a razão REAL/necessária nos trechos íngremes (mostrar razão fixa e "velocidade derivada" nesses trechos estaria errado). Trechos em `descida_ingreme_nm` aparecem em vermelho no terminal, igual ao gráfico.
- **Marcadores TOC/TOD no gráfico usam `toc_nm`/`tod_nm`** (mesma fonte do terminal — nunca divergem).
- **Cruzeiro:** altitude vem de `cruise.suggest_cruise_altitude` (spec do documento). **A distância passada é a do TRECHO EN-ROUTE** (maior corrida fora de corredor, medida por **distância**), **não a total** — senão sobe mais do que cruza. Sem corredor (DIRETO puro), en-route = total.
- **Voo tratado como IFR** (a V3 acrescenta altitude/cruzeiro) → **teto operacional cheio** da aeronave.
- **Piso de terreno OBRIGATÓRIO:** o cruzeiro deve ficar **≥ terreno_máx_en-route + 500 ft** (folga do documento). Se ficar abaixo, sobe para o **menor nível semicircular legal** que respeite o piso (paridade Leste/Oeste). Se o piso passar do teto, usa o maior nível legal + aviso forte.
- **Combustível por fase** (subida/cruzeiro/descida, incluindo corredores): `tempo_da_fase × consumo_por_hora_da_fase`, na unidade NATIVA da aeronave (`l/h`, `us gal/h` etc. — sem normalizar, evita conversão por densidade). `None` (indisponível) se a aeronave não tiver os 3 consumos completos.
- **Vento (passo 1 — tempo/combustível; ver subseção `wind.py` abaixo):** cada trecho do perfil tem tempo/combustível recalculados pelo triângulo do vento — a GEOMETRIA (posições/altitudes dos vértices) não muda, só tempo e combustível. Números sem vento continuam existindo em paralelo (comparação lado a lado). Escolha da altitude de cruzeiro pelo vento é o **passo 2, ainda NÃO implementado**.

**Terreno (`terrain.py`):**
- Tiles **FlatBuffer** do CDN NexAtlas (`.../bra/terrain_fb`), projeção **Web Mercator** (idêntica ao `read-tiles.js`). Em z=10, ~**150 m/pixel**. Valor cru em **metros**; escala = `10 ** fixed_point_precision` (slot 10 do `MetaData`, lido corretamente — ver gotcha abaixo); dá `10**0=1` pro terreno, confirmado ao vivo; saída em **pés** (×3,28084).
- `elevation(lon, lat, radius_px)` devolve o **máximo numa janela** `(2r+1)²`; `RADIUS_PX ≈ 4` = ±600 m (raio do obstáculo do 91.119).
- `max_along(path, step_nm=0.5, radius_px)` amostra a polilinha a cada **0,5 NM**, **interpolando a coordenada** entre waypoints. Origem/destino usam `radius_px=0` (ponto exato).
- **Gotcha (11/08/26, não regredir): `_META` (slots do `MetaData`) vem do `JetStreamDataTile.fbs`** (schema oficial, na raiz do repo). O mapeamento anterior (decodificado por tentativa-e-erro, sem o `.fbs`) tinha `zooms`/`altitudes` com a largura de elemento errada (`[uint8]`/`[float]` lidos como `[int32]`) e `fixed_point_precision` no slot errado (lia `version` por engano) — funcionava por coincidência (valores pequenos + zero-padding), mas era frágil. Sempre conferir o `.fbs` antes de tocar em `_META`.

**Vento (`wind.py`):**
- Mesmo CDN/formato do terreno (mesmo `_META`, reaproveita o decoder de `terrain.py`), dataset `wind_fb`: 2 canais (`u`=leste, `v`=norte, m/s), 12 níveis de altitude (0–44.300 ft, NÃO uniformes), 41 timestamps (previsão de 3 em 3h, ~5 dias). `Wind` **nunca lança erro** no construtor — se o CDN falhar, fica "indisponível" e `vento_em()` sempre devolve `(0,0)` (perfil nunca quebra por causa do vento).
- `vento_em(lon, lat, altitude_ft, hora_unix) -> (u_kt, v_kt)`: encaixa no nível e no timestamp **mais próximos** disponíveis (não precisa bater exato).
- `ground_speed(rumo_verdadeiro_deg, tas_kt, u_kt, v_kt) -> (gs_kt, cauda_kt, deriva_deg)`: triângulo do vento clássico (`cauda = u·sen(rumo)+v·cos(rumo)`; `través = u·cos(rumo)−v·sen(rumo)`; `GS = cauda + √(TAS²−través²)`; `deriva = asen(través/TAS)`). `rumo` PRECISA ser VERDADEIRO (u/v são leste/norte verdadeiros) — corredor usa o rumo do banco (que é magnético) + declinação WMM; DIRETO usa a geometria (`initial_bearing`), por perna (cacheado, não por sub-trecho).
- Em `profile.py::_vento_por_segmento`: ETA de cada trecho = hora de partida + tempo ACUMULADO SEM VENTO até ali (evita circularidade vento→tempo→ETA→vento) — `distância/TAS` sem vento é matematicamente idêntico ao `Δalt/razão` já usado no cálculo original.
- `parse_hora_utc`: aceita formato humano BR (`"15/08/2026 14:30"`, `"15/08/2026"` = 00:00, ou só `"14:30"` = hoje em UTC), ISO-8601 ou unix — nessa ordem de tentativa.

**Magnético (`magnetic.py`):** proa magnética via **WMM (`pygeomag`)**. **SEM fallback** — se o `pygeomag` faltar, `declination` **levanta erro** (chutar a declinação daria paridade/nível de cruzeiro errados). Os corredores já trazem a proa magnética do banco; `magnetic_bearing`/`declination` também são usados pelo vento (reconverter o rumo do corredor pra verdadeiro) — ver acima.

**Aeronave (`aircraft.py`):** modelo `Aeronave` (`teto_ft`, `rate_ac_fpm`/`rate_dc_fpm`, `speed_ac_kt`/`speed_cruise_kt`/`speed_dc_kt`, `fuel_ac`/`fuel_cruise`/`fuel_dc`/`fuel_unit`/`fuel_type`), normalização de unidades. A chave é **`id`** (o `designator_icao` não é único); ~26 aeronaves têm performance completa.

**`rules.py`:** só constantes usadas (`CLEARANCE_FT`, `OBSTACLE_RADIUS_M`, `Z_METERS_PER_PIXEL`, `RADIUS_PX`, `STEP_NM`). O enquadramento semicircular do cruzeiro é feito no `cruise.py`, não aqui.

---

## 6. Documento de altitude de cruzeiro (`calculoaltitudecruzeiro.pdf`)

`cruise.py` é **transcrição fiel** da spec, em 4 etapas:
1. **Faixa viável** — `min = max(elev_partida, elev_destino) + 500`; `max` por viabilidade (subir + descer cabem na distância).
2. **Alvo distância × teto** — percentual do teto por banda (tabelas), corte de segurança 10.500 para teto baixo.
3. **Enquadramento semicircular** — VFR (<14.500) milhar ±500 por direção; IFR/RVSM milhar cheio.
4. **Validação** — clampa em `[min, max]`.

Validado contra o exemplo do documento: 150 NM / proa 90° / teto 12.000 / elevações 2.000/1.500 → **7.500 ft**.

O documento **não** considera vento, combustível nem TAS×altitude — a tabela distância×teto é uma **heurística de referência**.

---

## 7. Decisões nossas que SOBREPÕEM ou ESTENDEM o documento

- **Distância en-route** (não total) na entrada da spec.
- **Teto cheio / IFR** (o voo é IFR quando há V3).
- **Piso de terreno en-route** (+500) como regra obrigatória (o documento só cobre as pontas).
- **Piso de corredor** no cruzeiro: nunca abaixo do corredor conectado (`max(H_pre, H_post)`), subindo ao nível do corredor.
- **Descida sempre na razão do banco**: cross no 1º corredor de chegada (TOD de aproximação), start nos seguintes. Ver §5.
- **`higher_limit` de corredor é limite OBRIGATÓRIO, não best-effort:** quando a razão do banco não basta (1º corredor, transição entre corredores ou trecho final), usa a razão necessária e avisa (nunca carrega a violação para a perna seguinte). Ver §5.
- **Combustível na unidade da própria aeronave** (sem normalizar), calculado por `tempo_da_fase × consumo/hora`, incluindo corredores. Ver §5.
- **Vento (passo 1) não muda a geometria do perfil** — só recalcula tempo/combustível por trecho; a escolha da altitude de cruzeiro pelo vento é o passo 2 (pendente). Ver §5.
- **Cruzeiro curto em teto alto é ESPERADO** (validado pelo Cristiano 05/08), não é para "equilibrar". Ver §8.

---

## 8. Roadmap / pendências (atualizado após reunião UNIFEI 05/08/26)

**Ordem de prioridade acordada (UNIFEI 05/08/26):** (1) perfil vertical (descida + classificação de trechos); (2) combustível básico; (3) vento — passo 1 (tempo/combustível por trecho). **Os três CONCLUÍDOS** (vento passo 1 fechado em 11/08/26 — ver `wind.py` e §5). Pendente: vento **passo 2**, abaixo.

- **Vento passo 2 (escolha de altitude pelo vento) — pendente:** hoje o vento só recalcula tempo/combustível (passo 1, feito); falta ele influenciar a **escolha da altitude de cruzeiro** (afeta só o trapézio de cruzeiro — altitude livre; nos corredores a altitude é fixa/obrigatória, o vento não entra na escolha, só no tempo/combustível deles, como já é hoje). Ideia geral (a confirmar com Jorge/Vinícius antes de implementar): comparar o vento nos níveis candidatos e preferir o que dá melhor tempo/combustível total, respeitando os pisos já existentes (terreno, corredor). Documentar como uma nova `TAREFA_*.md` antes de tocar no código — mesmo fluxo diagnóstico→aprovação→implementação do §0.
- **NÃO fazer — "equilíbrio subida × cruzeiro":** aeronave de teto alto que sobe muito, cruza pouco e desce muito **está CORRETA** (Cristiano validou 05/08): sem vento, subir o máximo é a solução (menos terreno/obstáculo; o vento em altitude costuma ajudar). O vento é que poderá, depois, baixar a altitude em casos específicos. **Não é bug, não é para corrigir.**
- **FORA DE ESCOPO — combustível/altitude ótima por performance:** exige dados de performance do fabricante (spec/DLLs por temperatura e pressão), que a equipe não tem acesso. Fica só a estimativa simples por fase (acima).
- **V2 (lateral IFR):** SID/STAR/IAC/aerovias — pendente de dados.
- **Descida — envelope unificado clima+descida (refinamento de baixa prioridade):** num caso raro (piso de corredor coloca o cruzeiro na altitude do próprio corredor e o "trecho livre" é a perna final), a aeronave segura o nível pelos corredores e comprime o trecho final. O ideal seria a descida poder começar **dentro** do último corredor (abaixo do teto). Só afeta rotas muito curtas.
- **Cap de O₂ / tipo de aeronave** — voo não pressurizado acima de 12.500 ft por >30 min exige O₂.
- **Cota publicada do aeródromo** (em vez do terreno no ponto) para origem/destino.
- **Multiobjetivo:** vento, NOTAM, combustível, alcance, meteorologia, terreno, peso.

---

## 9. Colaboradores

- **Vinícius** — piloto, autoridade aeronáutica.
- **Cristiano** — coordenador/engenheiro NexAtlas, autoridade de algoritmo/dados.
- **Jorge** — coordenação (UNIFEI).
- **Ivan** — desenvolvedor (responsável por este repositório).