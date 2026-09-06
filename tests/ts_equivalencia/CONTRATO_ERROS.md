# Contrato de erros/sinais do motor de rotas — para o orquestrador (Caio)

> TAREFA_gabarito_v2_contrato.md (26/08/26). Este documento diz o que o **motor** (V1 lateral +
> V3 vertical, `nexatlas_router/`) devolve quando o input está incompleto ou quando uma decisão
> de comportamento (não erro) muda o formato da saída. É o contrato que o orquestrador pode
> **confiar e mapear direto pra uma pergunta ao piloto** — cada sinal da Seção 1 foi validado ao
> vivo (rodado de verdade contra o banco/CDN, não só lido no código) antes de entrar aqui.
>
> A Seção 2 lista o que **NÃO** vem do motor — pra não programar o assistente esperando um sinal
> que nunca chega.

---

## Seção 1 — Sinais que o MOTOR devolve

### 1.1 `faltou_input` — pista obrigatória não informada

**Gatilho:** a origem e/ou o destino tem, em `data/portoes_rea.json`, TODAS as regras da direção
relevante (`partida` pra origem, `destino` pra destino) condicionadas a cabeceira (`pistas`) — ou
seja, nenhuma regra "geral" cobre o caso — e a pista não foi informada nessa chamada.

**Onde:** `nexatlas_router.portoes.PistaObrigatoriaError`, levantada por `db.build_subgraph`
**antes de qualquer query** (não é preciso ir ao banco pra saber que falta o input).

**Retorno estruturado exato** (`PistaObrigatoriaError.to_dict()`):
```json
{
  "status": "faltou_input",
  "faltando": ["pista_origem"],
  "aerodromos": {"pista_origem": "SBBH"},
  "mensagem": "SBBH tem regra de cabeceira; informe a pista de decolagem para calcular a rota."
}
```
Quando os dois extremos faltam (validado ao vivo com SBRJ→SBMT, nenhum dos dois com pista):
```json
{
  "status": "faltou_input",
  "faltando": ["pista_origem", "pista_destino"],
  "aerodromos": {"pista_origem": "SBRJ", "pista_destino": "SBMT"},
  "mensagem": "SBRJ tem regra de cabeceira; informe a pista de decolagem; SBMT tem regra de cabeceira; informe a pista de pouso para calcular a rota."
}
```
`faltando` só contém os campos que REALMENTE faltam (nunca lista `pista_destino` se o destino não
tem regra condicionada, ou se a pista dele já foi informada). `aerodromos` mapeia cada campo de
`faltando` pro ICAO correspondente. `mensagem` é texto livre — não confiar nela pra lógica, é só
pra log/debug.

**Pergunta sugerida ao piloto:** uma por item de `faltando`:
- `pista_origem` presente → "Qual a pista de decolagem em {aerodromos.pista_origem}?"
- `pista_destino` presente → "Qual a pista de pouso em {aerodromos.pista_destino}?"

**7 aeródromos hoje com alguma regra por cabeceira:** SBBH, SBNT, SBJR, SBRJ, SBJD, SBMT, SDCO
(nem todos em ambas as direções — ver `data/portoes_rea.json`).

**Quando NÃO dispara** (comportamento normal, sem sinal):
- Pista informada casa com uma regra → o motor força o portão publicado, devolve rota.
- Pista informada NÃO casa nenhuma regra do aeródromo → o motor NÃO força portão, devolve rota
  normal (mínimo-local/coerência) — validado ao vivo (SBBH pista `31`, que não existe em nenhuma
  regra de SBBH, dá rota sem Ceasa).
- Aeródromo/direção sem regra nenhuma condicionada a cabeceira → pista é irrelevante, ignorada.

---

### 1.2 Sem `hora_partida_utc` → sem vento (comportamento, não erro)

**Gatilho:** `hora_partida_utc` não informado na chamada da V3.

**Retorno:** rota e perfil vertical NORMAIS (nenhuma exceção, nenhum campo faltando fora do
vento) — só o bloco de vento fica vazio: `segmentos_vento = []`, `tempo_min_vento = None`,
`combustivel_vento = None` (mesmos nomes no gabarito: `vento.segmentos`, `vento.tempo_min_vento`,
`vento.combustivel_vento`). Validado ao vivo (par SBMO→SBRF, mesma aeronave, com e sem hora —
casos `contrato_sem_hora_sem_vento` / `contrato_com_hora_com_vento` do gabarito).

**Não é um erro** — é o default deliberado (reunião de 26/08 com o Vinícius: vento nunca deve ser
subentendido). Não há `status`/`faltando` aqui; o piloto SEMPRE recebe uma rota completa.

**Pergunta sugerida ao piloto (opcional, não bloqueante):** "Se quiser considerar o vento no
tempo de voo e combustível, me diga a data e hora de decolagem (UTC)."

---

### 1.3 `faltou_input` — origem e/ou destino ausente

**Gatilho:** `origin_icao`/`dest_icao` vazio ou `None` (string em branco também conta) na chamada
de `db.build_subgraph` — TAREFA_faltou_input_origem_destino.md (05/09/26). Checagem é a PRIMEIRA
de todas, antes até da de pista (§1.1): não faz sentido checar regra de cabeceira de um aeródromo
que nem foi informado. Não toca no banco nem no JSON de portões.

**Onde:** `nexatlas_router.portoes.EntradaAusenteError`, levantada por `db.build_subgraph` antes
de qualquer query. **Não** é subclasse de `PortaoError` (não tem relação com portão/gate) —
diferente do `PistaObrigatoriaError`, então quem só captura `PortaoError` precisa capturar esta
separadamente.

**Retorno estruturado exato** (`EntradaAusenteError.to_dict()`):
```json
{
  "status": "faltou_input",
  "faltando": ["origem"],
  "aerodromos": {},
  "mensagem": "Informe o aeródromo de partida."
}
```
Quando os dois faltam: `"faltando": ["origem", "destino"]`, mensagem combinando os dois. Mesmo
formato de 4 chaves do `PistaObrigatoriaError` (§1.1) — `aerodromos` sempre presente, mas sempre
`{}` aqui: não há ICAO nenhum a reportar (é justamente isso que está faltando).

**Pergunta sugerida ao piloto:** uma por item de `faltando`:
- `origem` presente → "Qual o aeródromo de partida?"
- `destino` presente → "Qual o aeródromo de destino?"

**Quando NÃO dispara:** ICAO informado mas INEXISTENTE no banco (ex.: `"SBXX"`) — continua fora
deste sinal, cai no `LookupError` genérico de sempre (ver Seção 2 — é entrada de fato inválida,
não "esqueci de informar"; decisão do Ivan, 05/09/26: manter como está, gap conhecido).

---

## Seção 2 — NÃO é do motor (responsabilidade da camada do orquestrador/catálogo)

Estes itens NUNCA chegam como um sinal estruturado do algoritmo de rota — se o assistente
precisa deles, a lógica é da camada de cima, antes ou depois de chamar o motor.

- **Aeronave ausente para perfil vertical.** Testado no código: `plan_from_v1(graph, route,
  aeronave, terreno, wind, ...)` recebe `aeronave` como parâmetro **posicional obrigatório** —
  não existe uma chamada onde o motor "recebe" ausência de aeronave e reage. Se o piloto não deu
  aeronave, quem decide é o orquestrador: simplesmente não chama a V3 (a rota lateral sai normal,
  sem seção vertical). Não há `faltou_input: aeronave`.

- **ICAO informado mas INEXISTENTE no banco** (ex.: `"SBXX"`). Origem/destino **ausente**
  (vazio/`None`) SAIU daqui — agora é o sinal estruturado `faltou_input` da §1.3. Só o ICAO
  informado-porém-inexistente continua caindo no `LookupError: Aeródromo 'X' não encontrado em
  published.adhps` genérico — decisão do Ivan (05/09/26, TAREFA_faltou_input_origem_destino.md):
  é entrada de fato inválida (não "esqueci de informar"), fica como está, não é um `faltou_input`
  estruturado. Se o orquestrador precisa dizer "não achei XYZ" de um jeito diferente de outro
  erro de banco, não é uma tarefa fechada aqui — fica como gap conhecido pra uma eventual tarefa
  futura.

- **Resolução de variante de aeronave** (ex.: "qual SR22? há N variantes em `aircraft_models`") —
  é busca no catálogo (`nexatlas_router.vertical.find`/`load_from_db`), feita ANTES de chamar o
  motor; o motor recebe o `id` já resolvido, nunca um nome ambíguo.

- **Aeronave sem dados de performance/combustível completos.** O motor calcula o que dá
  (`combustivel = None`, ver `vertical.combustivel.*` no gabarito) — não é um erro de input, é
  um resultado parcial válido. A decisão de pedir pro piloto cadastrar a aeronave (ou avisar que
  o combustível não pôde ser estimado) é da camada do assistente.

- **Formatação da resposta ao piloto** (perfil vertical bonito, unidades por idioma, etc.) — é
  apresentação, o motor só devolve os números estruturados.

---

## Como isso é testado

Cada sinal da Seção 1 tem pelo menos um caso em `gabarito_rotas.json` com
`tipo_caso: "contrato_input"` e `esperado` no formato do sinal (em vez do formato
lateral/vertical/vento/magnético de uma rota normal) — `verifica_gabarito.py` reconhece o
formato pela chave `status` e compara pelos campos estruturados (`compara_sinal`), nunca pelo
texto de `mensagem`. Ver `README_equivalencia.md` §2 (formato) e §4 (cobertura por caso).
