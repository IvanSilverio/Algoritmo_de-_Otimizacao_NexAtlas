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
- Credenciais em **`.env.sh`** — **NUNCA versionar** (já está no `.gitignore`, junto com `*.png` e saídas geradas). `resultados_testes_REA.json` e `data/portoes_rea.json` SÃO versionados de propósito — servem de referência pra quem for consumir/transcrever o algoritmo.
- A CLI roda **localmente** contra o banco completo.

---

## 3. Como rodar e testar

```bash
pip install -r requirements.txt     # inclui pygeomag (WMM) — obrigatório para a V3
source .env.sh                      # credenciais (não versionado)
python3 nexatlas_cli.py             # pede a aeronave, roda V1 + V3, imprime o perfil e salva mapa/gráfico
```

- **Regressão V1:** bateria de 100 casos REA (`resultados_testes_REA.json`, via `run_test_cases.py`) — deve dar **100/100 explicado** (OK + `ERRO_PORTAO`; NUNCA `ERRO` genérico). Não toca na V3 (nem importa `nexatlas_router.vertical`). Status `ERRO_PORTAO` (distinto de `ERRO`) é falha EXPLICADA de portão obrigatório (ver §4), não erro silencioso — cobre tanto resolução/desconexão de portão quanto pista obrigatória não informada (`PistaObrigatoriaError`, 26/08/26) — a bateria também imprime um relatório de colisões portão×coerência no final. **Hoje: 89 OK + 11 ERRO_PORTAO** (os 11 são pista obrigatória sem cabeceira informada — ver §4).
- **Regressão V3 (múltiplas aeronaves × rotas, com vento):** `python3 testes_voos.py` (edite `AERONAVES`/`ROTAS`/`HORA_PARTIDA_UTC` no topo do arquivo) — roda a matriz contra o banco + CDN reais e gera `analise_voos.csv` + gráficos em `analise_voos/`.
- **Se a V3 não pedir a aeronave:** quase sempre é `pygeomag` faltando (`pip install pygeomag`) ou o `cruise.py` ausente em `vertical/`. Cheque com `python3 -c "import nexatlas_router.vertical"`.
- **Gabarito de equivalência Python↔TypeScript** (`tests/ts_equivalencia/`, 29 casos): snapshot CONGELADO (banco + CDN) que prova pro time do TS que a transcrição reproduz lateral+vertical+vento+magnético dentro de tolerância — não bit-a-bit (ver `tests/ts_equivalencia/README_equivalencia.md`). `python3 tests/ts_equivalencia/verifica_gabarito.py` roda nosso Python ao vivo contra o snapshot (referência de 100% e exemplo de como comparar); `gerar_gabarito.py` regenera quando o banco mudar de forma relevante pra algum dos casos. Independente da bateria de regressão acima (essa pega dado desatualizado; o gabarito pega equivalência de código).
  - **`tests/ts_equivalencia/CONTRATO_ERROS.md`** (TAREFA_gabarito_v2_contrato.md, 26/08/26): o que o motor REALMENTE devolve quando o input está incompleto — pra hoje, só `faltou_input` de pista obrigatória (`PistaObrigatoriaError.to_dict()`) e o comportamento "sem hora → sem vento" — separado do que NÃO é do motor (aeronave ausente pro perfil vertical, origem/destino ausente — hoje cai no MESMO `LookupError` genérico de um ICAO inválido, gap conhecido e documentado, não resolvido). Cada sinal tem um caso correspondente no gabarito com `tipo_caso: "contrato_input"` (`esperado` é o sinal, não uma rota) — `verifica_gabarito.py` reconhece o formato pela chave `status` (`compara_sinal`, compara campos estruturados, nunca a `mensagem` livre).
  - **Vento no gabarito só nos casos que testam vento de propósito** (achado ao vivo 26/08: o gabarito antigo, com hora FIXA pra todo mundo, estava em 0/25 — a janela de previsão do CDN (~5 dias) já tinha passado da data congelada). Default agora é `hora_partida_utc: null` (sem vento, nunca expira — mesmo contrato da Missão 2 abaixo); só 4 casos usam `vento_modo: "relativo"` (hora = "agora da geração/verificação" + `vento_offset_h`, recalculada a cada execução, nunca relida do congelado) com comparação ESTRUTURAL de vento (não por valor exato). Os 2 casos de par recíproco (`vento_par_reciproco_ida`/`_volta`) usam uma checagem CRUZADA entre os dois (mesmo campo de vento, rumos opostos, então uma perna sempre mais rápida e a outra mais lenta que sem vento) em vez de afirmar qual perna leva cauda — isso muda com o forecast do momento, não é propriedade fixa da rota.
- **Portões obrigatórios de aeródromo:** `data/portoes_rea.json` é gerado por `python3 parse_portoes.py` a partir de `regras_rea_primeiros_ultimos_pontos.md` — rode de novo só se o documento de referência mudar (o parser avisa linhas fora do padrão, que devem ser zero).

---

## 4. V1 — motor lateral e regras críticas

- **Dijkstra com estado de fase** é o motor **autoritativo**. **Yen's k-shortest** (mesmo grafo com fase) gera as alternativas. O **GWO** (Grey Wolf Optimizer) existiu no código até 18/08/26 e foi **removido** (nunca superava o Dijkstra em distância, não conhecia a regra de fase) — se aparecer em comentário antigo/histórico do git, é só isso, não tem mais nada rodando.
- **Regras/gotchas (bugs já resolvidos — não regredir):**
  - **Detecção de loop por ID do nó, NUNCA por nome.** Existem waypoints homônimos (ex.: dois `TREVO` a ~1563 NM). Nome dá falso positivo em rota válida.
  - **Corredores são voados no `higher_limit`.**
  - **Entrada/saída de corredor é topológica POR PADRÃO** (cabeça de cadeia: `_has_real_incoming == False` e `_has_real_outgoing == True`), **não** pela string `"PORTÃO"` — muitos pontos de entrada não têm o nome, e muitos com o sufixo são de meio de cadeia. **Exceção deliberada:** os aeródromos com regra em `data/portoes_rea.json` (ver "Portões obrigatórios" abaixo) ignoram esse mínimo-local — aí sim o documento manda, mesmo quando o nó no banco tem sufixo `"(PORTÃO)"`/`"(REA)"` (usado só pra resolver o NOME do ponto do documento, nunca pra decidir topologia sozinho, fora desse contexto).
  - **Esporão (vai-e-volta):** corrigido com `OWES_SYNTH_REACH_MARGIN_M = 5 NM` (gatilho 1) + laço anti-esporão de 2 passes por **node-ID** em `v1.plan_v1_route` (gatilho 2).
  - **Bridge overshoot:** `_overshoots_dest` em `graphmodel.py` descarta pontes que passam do destino.
  - **Obrigação de corredor é regional** (corredores de uma região são alternativas), **não** por corredor individual → usar `rule_satisfied`.
  - **Testar offline sem `require_real_edge=graph.requires_corridor` ESCONDE o bug do esporão.** Sempre testar com a flag real.
  - **Duplicidade do último ponto:** alguns aeródromos têm um waypoint da REA com o MESMO NOME do ICAO, a poucos metros dele (o próprio ponto de referência do campo, cadastrado também como nó da malha — ex.: SNCL, SIVU). `v1._remove_duplicidade_destino` funde esse waypoint no destino (nome + `DEDUP_DEST_RADIUS_M` = 0,5 NM, hoje em `graphmodel.py`) antes de reportar a rota.

### Coerência geométrica (TAREFA_coerencia_geometrica.md, 17/08/26)

A malha REA às vezes é tecnicamente conectada mas geometricamente absurda (waypoint isolado fora do eixo, corredor que continua além do ponto que já serve o destino). Checado sobre a rota JÁ DECIDIDA pelo Dijkstra, não como custo por aresta (a curva depende do trecho anterior — exigiria expandir o estado de fase; a decisão final é sempre binária "mantém a malha ou troca pela direta", então avaliar a rota pronta basta e não toca no motor de fase):
- `geo.progresso_nm` (usada por `v1.py` e `graphmodel.py`): projeção do passo no eixo origem→destino, negativo = retrocesso. `v1._turn_deg`: mudança de rumo entre trechos consecutivos.
- **A malha só vence a rota direta** se tiver ≥1 corredor REAL, for coerente (sem retrocesso `RETROCESSO_LIMIAR_NM=3 NM` nem curva `CURVA_LIMIAR_DEG=120°` acima do limiar) e não custar mais que `FATOR_RELATIVO_DIRETO=1,7×` a direta (constantes em `v1.py`). Mesmo limiar de curva pra rota principal E alternativas do k-shortest — um limiar frouxo só pras alternativas deixava passar desvios pouco viáveis.
- **Portão de saída aceita vizinho mais próximo do destino quando o trecho até ele é OPCIONAL** (`graphmodel._skips_closer_corridor_node(..., only_mandatory=True)`, só no lado da SAÍDA): caso 008, TRAPICHE é portão válido de SBFL mesmo com TREVO mais perto, porque TRAPICHE→TREVO não é obrigatório.
- **Rota direta injetada como alternativa extra** (`v1._mesma_malha`) quando a malha inteira está numa única carta REA (proximidade), mesmo com o corredor como recomendação principal.

### Portões obrigatórios de aeródromo (TAREFA_portoes.md, 17-18/08/26)

`data/portoes_rea.json` (52 aeródromos, gerado por `parse_portoes.py` a partir de `regras_rea_primeiros_ultimos_pontos.md`) lista o(s) ponto(s) OFICIALMENTE publicado(s) de entrada/saída. Quando existe regra pro ICAO, ela **SUBSTITUI** a escolha por mínimo-local/k-mais-próximos — topológico, via `graphmodel.add_synthetic_edges(origin_forced=..., dest_forced=...)`, resolvido em `db.py` (que conhece o ICAO de cada ponta; `graphmodel.py` fica agnóstico ao documento).
- **Resolução de nome por `nome + carta`** (`nexatlas_router/portoes.py`) — há 10 homônimos entre cartas, carta é obrigatória. **Fallback em níveis** quando o banco cadastra o ponto com um qualificador que o documento não usa (achado ao vivo: 35 dos 126 pontos precisavam disso): nome puro → `"PORTÃO"` prefixo ou sufixo → `"(REA)"` sufixo. `"(REA)"` **não** significa "é portão" (ex.: `FLORES (REA)`/`MANNESMANN (REA)` em Belo Horizonte são pontos que a própria V1 já identifica como NÃO-portão) — por isso "PORTÃO" tem prioridade sobre "(REA)" quando os dois existem pro mesmo nome (só ocorreu 1x: IGARATÁ/SBSJ — dedução, não confirmada formalmente; Ivan vai levar como observação pro Vinícius/Cristiano). **Falha explícita** (`PortaoResolucaoError`/`PortaoDesconectadoError`, nunca `ERRO` genérico) se um ponto não resolve pra exatamente 1 nó, ou se forçar o portão desconecta a rota — nunca relaxa pro k-mais-próximos, nunca inventa o ponto.
- **"X ou Y" (múltiplos pontos válidos):** os dois viram candidatos de aresta sintética; um empurrão de peso (`graphmodel.PORTAO_RETROCESSO_PESO_M_POR_NM`) prefere o mais coerente com o eixo origem→destino, mas NUNCA exclui o outro — o portão continua válido mesmo perdendo essa preferência.
- **Gotcha ao vivo (caso SIVU) — waypoint duplicata do destino como continuação do portão:** o portão obrigatório às vezes só alcança as vizinhanças do aeródromo por um trecho REAL OBRIGATÓRIO até um waypoint homônimo do destino (mesmo padrão da "duplicidade do último ponto" acima). Sem estender a saída forçada até esse waypoint também, `owes_real` barra o salto sintético e a malha fica DESCONEXA (`shortest_route` devolve `None` silenciosamente). `add_synthetic_edges` já cobre isso — se voltar a acontecer num aeródromo novo, é o primeiro lugar a olhar.
- **Portão PREVALECE sobre a coerência geométrica** (autoridade publicada > heurística), com duas camadas (ajuste 19/08/26, feedback do Ivan): (1) a perna de entrada/saída por portão nunca é, sozinha, motivo pra marcar a rota incoerente (`v1._rota_incoerente(..., pernas_protegidas=...)`) — mas isso só protege ESSA perna; uma curva/retrocesso em outro trecho do corredor, ou a ausência de cadeia real, ou o fator relativo, ainda podiam descartar a malha INTEIRA (portão incluso) quando origem/destino não são "aeródromos próximos". (2) Por isso, fora da proximidade (`v1._aerodromos_proximos`: mesma carta REA documentada E distância direta ≤ `PORTAO_PROXIMIDADE_LIMIAR_NM=60 NM` — carta desconhecida de qualquer lado cai só no critério de distância, nunca conta como "diferente" por si só) o portão **nunca** é descartado por conta da heurística acima, ponto final — achado ao vivo (casos SBBE→SBCY, SBBH→SBFZ, SBTV→SBRF, SBMT→SBRJ: cadeia real, fator baixo, mas incoerência em perna NÃO-adjacente ao portão descartava tudo). Dentro da proximidade, a direta continua podendo vencer como sempre — são justamente os casos já validados pelo Vinícius (gabarito antigo 003 SBBH→SBCF, 006 SBCY→SIAQ, 009 SBFZ→SNFF, 011 SISM→SBMQ, 012 SBNT→SBSG, 015 SBPS→SBTV, 020 SBVT→SIVU). Toda colisão continua sinalizada em `meta["colisao_portao_coerencia"]` (`{"entrada": bool, "saida": bool}`) — nunca escondida, mesmo quando o portão acaba sendo forçado; ver relatório da bateria (§3).
- **Pista/cabeceira — OBRIGATÓRIA quando a direção só tem regra condicionada a cabeceira** (TAREFA_pista_obrigatoria_e_vento_default.md, 26/08/26, decisão da reunião com o Vinícius — **revoga a opcionalidade** do TAREFA_pista.md de 20/08/26): `portoes.direcao_exige_pista(icao, direcao)` é True quando TODAS as regras daquela direção (`partida`/`destino`) têm `pistas` (nenhuma regra "geral" cobre o caso). Quando isso é True e a pista não veio, `db.build_subgraph` levanta `PistaObrigatoriaError` (subclasse de `PortaoError`; `.to_dict()` → `{"status": "faltou_input", "faltando": [...], "aerodromos": {...}, "mensagem": "..."}`) **ANTES de qualquer query** (checagem é só o JSON) — nunca calcula rota sem o portão, nunca cai silenciosamente no mínimo-local. Checagem é **por DIREÇÃO**, não por aeródromo: SBBH exige pista só na `partida` (regra única, `pistas: ["13"]`); a `destino` tem regra geral ("CEASA", sem `pistas`) e nunca bloqueia. **Continua exatamente como antes** (`pontos_obrigatorios`/`_regra_aplicavel` não mudaram) quando: a pista informada casa com uma regra → força o portão; a pista informada não casa nenhuma regra → NÃO força, roda pela via normal (mínimo-local/coerência); a direção tem ao menos uma regra geral, ou o aeródromo não tem regra nenhuma → pista é irrelevante ali. `portoes.aerodromo_exige_pista(icao)` (qualquer direção) continua só guiando o PROMPT do CLI — quem decide se BLOQUEIA é `direcao_exige_pista`, dentro do motor. **7 aeródromos afetados:** SBBH, SBNT, SBJR, SBRJ, SBJD, SBMT, SDCO. Efeito na bateria de 100: **11 dos 15 casos** que tocam esses aeródromos passam de OK para `ERRO_PORTAO` — SBBH→SBCF, SBBH→SBFZ, SBNT→SBSG, SBNT→SBFZ, SBNT→SWDI, SBBR→SBNT, SBCX→SBNT, SBMT→SBRJ, SBRJ→SBMT (os dois lados), SBKP→SBJR, SBJR→SBKP — e reverte a observação do TAREFA_pista.md de 20/08/26 (003 SBBH→SBCF "reaparecia" no relatório de colisão quando a pista virou opcional; agora bloqueia antes de chegar lá, é o esperado). Nenhum `ERRO` genérico introduzido — ver §3.
- **Caso 004 (SBBR→SIQE) continua em aberto:** o documento só cobre o destino (SIQE: `GRANJA 2`), não a origem (SBBR não aparece no documento) — o portão de entrada "certo" perto de Brasília (ex.: Estufa Vermelha, citado em conversas anteriores) não está em nenhuma fonte versionada. Fica pendente até haver dado real pra origem.

---

## 5. V3 — camada vertical (`nexatlas_router/vertical/`)

Módulos: `contract.py`, `terrain.py`, `wind.py`, `magnetic.py`, `rules.py`, `aircraft.py`, `cruise.py`, `profile.py`, `plot_profile.py`, `__init__.py`.

**Modelo (do piloto Vinícius + coordenador Cristiano):**

- **Saída = lista de VÉRTICES (reais + virtuais)** em `PerfilVertical` — fonte única do gráfico e do JSON. Vértice: `x_nm, alt_ft, tipo, nome, real`. Pontos virtuais marcam onde uma transição de altitude termina (não são pontos reais da rota).
- **Subida em degraus:** a aeronave sobe **SEMPRE na razão máxima** (`start_to`), atinge o `higher_limit` do corredor num **ponto virtual** e nivela até o próximo. Nada de rampa linear ao longo da perna. Em trecho curto, a subida "carrega" para a perna seguinte (é o máximo físico).
- **Descida em CROSS (só o 1º corredor de chegada) + START (corredores seguintes), sempre na razão do banco** (`rate_dc`/`speed_dc`, `_descida_final` em `profile.py`; TAREFA_descida_transicao_e_aviso.md, validado com o Vinícius 12/08/26): a aeronave **permanece na altitude máxima** (cruzeiro ou o corredor mais alto) até o **"TOD de aproximação"** — o ponto mais tarde possível a partir do qual, descendo na razão do banco, ela chega (quando a distância permite) EXATAMENTE no `higher_limit` do **1º corredor de chegada**, na ENTRADA dele (isso é o TOD; único ponto que ainda usa a linha reta até um alvo). **Corredores de chegada SEGUINTES usam START, não cross:** a aeronave mantém o `higher_limit` do corredor atual até PASSAR o ponto (mesmo atravessando trechos DIRETO entre corredores) e só desce, na razão do banco, DENTRO da perna seguinte, para o `higher_limit` dela — nunca antes (é o mesmo mecanismo `start_to` da subida, mas descendo). Durante a transição em si — entre passar o ponto e alcançar o novo teto — é normal e esperado ficar acima do teto novo por um instante; isso NÃO é violação. O trecho final (fora de corredor, até o aeródromo) desce na razão máxima do banco.
  **O `higher_limit` de um corredor de chegada — 1º (cross) ou seguinte (start) — é ALVO, não obrigação** (refinado 19-20/08/26, TAREFA_descida_refino.md, validado com o Vinícius — substitui o ajuste 14/08/26 anterior, que tratava isso como obrigatório): a aeronave **NUNCA extrapola a razão máxima do banco** para encaixar o teto de um corredor de chegada — se a distância não é suficiente para chegar lá a tempo (cross apertado, ou perna curta demais entre corredores), ela passa **ACIMA do teto** e continua descendo na razão máxima, **sem aviso nem vermelho** (achado ao vivo, SBPA→SBFL/SR22: Papagaio 4.500 ft → Três Irmãs 1.500 ft em só 3,93 NM — o avião passa Três Irmãs a ~3.910 ft e Pântano do Sul a ~3.428 ft, ambos acima do teto, sem aviso; só de Pântano do Sul → SBFL é que mergulha). **Só o trecho final** (fora de corredor, até o aeródromo) pode de fato extrapolar a razão máxima — é aí, e SÓ aí, que aparece o trecho íngreme em `PerfilVertical.descida_ingreme_nm` (lista de `(x0_nm, x1_nm)`) — vermelho no gráfico e no terminal, com aviso mostrando a razão REAL necessária.
- **TOC/TOD:** 1º/último ponto no topo. Sem cruzeiro nivelado (só corredores / rota curta) → **intervalo da altitude máxima** do perfil.
- **Tempo por segmento:** subida/descida = `Δalt / razão` (min); nivelado = `distância / velocidade`. (Contar subida/descida pela razão, não por velocidade × distância.)
- **Terminal (`nexatlas_cli.py::_print_vertical`, `testes_voos.py::imprimir_vertices`)** mostra, por trecho, a razão (fpm) e a velocidade (kt) — para verificação. A **velocidade do banco é FIXA e a razão é DERIVADA** do trecho real (`Δalt × velocidade / (Δx × 60)`), NUNCA o contrário — dá a razão nominal certa nos trechos normais e a razão REAL/necessária nos trechos íngremes (mostrar razão fixa e "velocidade derivada" nesses trechos estaria errado). Trechos em `descida_ingreme_nm` aparecem em vermelho no terminal, igual ao gráfico.
- **Marcadores TOC/TOD no gráfico usam `toc_nm`/`tod_nm`** (mesma fonte do terminal — nunca divergem).
- **Cruzeiro:** altitude vem de `cruise.suggest_cruise_altitude` (spec do documento). **A distância passada é a do TRECHO EN-ROUTE** (maior corrida fora de corredor, medida por **distância**), **não a total** — senão sobe mais do que cruza. Sem corredor (DIRETO puro), en-route = total.
- **Voo tratado como IFR** (a V3 acrescenta altitude/cruzeiro) → **teto operacional cheio** da aeronave.
- **Piso de terreno OBRIGATÓRIO:** o cruzeiro deve ficar **≥ terreno_máx_en-route + 500 ft** (folga do documento). Se ficar abaixo, sobe para o **menor nível semicircular legal** que respeite o piso (paridade Leste/Oeste). Se o piso passar do teto, usa o maior nível legal + aviso forte.
- **Combustível por fase** (subida/cruzeiro/descida, incluindo corredores): `tempo_da_fase × consumo_por_hora_da_fase`, na unidade NATIVA da aeronave (`l/h`, `us gal/h` etc. — sem normalizar, evita conversão por densidade). `None` (indisponível) se a aeronave não tiver os 3 consumos completos.
- **Vento (passo 1 — tempo/combustível; ver subseção `wind.py` abaixo):** cada trecho do perfil tem tempo/combustível recalculados pelo triângulo do vento — a GEOMETRIA (posições/altitudes dos vértices) não muda, só tempo e combustível. Números sem vento continuam existindo em paralelo (comparação lado a lado). **Default é SEM vento** (TAREFA_pista_obrigatoria_e_vento_default.md, 26/08/26): só calcula quando `hora_partida_utc` é informado EXPLICITAMENTE — sem hora, `subida_vento`/`cruzeiro_vento`/`descida_vento`/`segmentos_vento` ficam `None`/vazios, sem aviso de "assumi agora" (decisão do Vinícius: vento nunca é subentendido, e evita o gabarito congelado "expirar" quando a janela de previsão do CDN passa). Escolha da altitude de cruzeiro pelo vento é o **passo 2, ainda NÃO implementado**.

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

**Magnético (`magnetic.py`):** proa magnética via **WMM (`pygeomag`)**. **SEM fallback** — se o `pygeomag` faltar, `declination` **levanta erro** (chutar a declinação daria paridade/nível de cruzeiro errados). Os corredores já trazem a proa magnética do banco; `magnetic_bearing`/`declination` também são usados pelo vento (reconverter o rumo do corredor pra verdadeiro) — ver acima. **Declinação pela DATA DO VOO, não pela data de execução** (`profile.py`, 24/08/26): `declination`/`magnetic_bearing` sempre aceitaram uma data explícita, mas nenhum chamador passava uma — caía em `date.today()`. `plan_vertical_profile` deriva `data_voo` de `hora_partida_utc` (reaproveitada pro cruzeiro/paridade) e cai na data corrente, **SEM aviso**, se a hora não foi informada — a variação secular é pequena o bastante pra não justificar aviso. **O vento é diferente:** desde 26/08/26 (TAREFA_pista_obrigatoria_e_vento_default.md) ele só é calculado com `hora_partida_utc` EXPLÍCITO — sem hora não há rumo verdadeiro de vento a converter, e os campos `*_vento` ficam ausentes (nunca cai em "agora"; ver §5 acima, `wind.py`). Efeito: o mesmo caso dá a MESMA declinação não importa quando rodar — o gabarito de equivalência (acima) depende disso pra ser reproduzível de verdade (sem essa correção, a variação secular do WMM, ~0,025°/ano, ia divergir o bloco magnético sozinho em poucos meses).

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
- **`higher_limit` de corredor de chegada (1º/cross ou seguinte/start) é ALVO, não obrigação:** quando a razão do banco não basta, passa ACIMA do teto e continua na razão máxima, sem aviso. **Só o trecho final** pode extrapolar a razão máxima — só aí há aviso + vermelho. Ver §5.
- **Combustível na unidade da própria aeronave** (sem normalizar), calculado por `tempo_da_fase × consumo/hora`, incluindo corredores. Ver §5.
- **Vento (passo 1) não muda a geometria do perfil** — só recalcula tempo/combustível por trecho; a escolha da altitude de cruzeiro pelo vento é o passo 2 (pendente). **Default é SEM vento** — só calcula com `hora_partida_utc` explícito (26/08/26), nunca assume "agora". Ver §5.
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