# Handoff — validação do motor de rotas (para o Caio)

Você está recebendo o motor de rotas **corrigido** (V3 com vento/combustível + fix de pico de
cruzeiro e razão de descida). A entrega tem **duas partes independentes**:

- **Parte 1 — Igualdade Python↔TS**: prova que a transcrição em TypeScript reproduz o mesmo
  cálculo do motor Python, campo a campo, dentro de tolerância. Teste automatizado, rigoroso.
- **Parte 2 — Validação de resposta do orquestrador**: confere se o **orquestrador** (a camada
  que conversa com o piloto) usa corretamente o que o motor entrega. Teste no olho, por
  coerência, não bit a bit.

---

## Parte 1 — Igualdade Python↔TS

- **`tests/ts_equivalencia/gabarito_rotas.json`** — o gabarito: um snapshot congelado de
  entradas + saídas esperadas (rota, vertical, vento, magnético, sinais de input).
- **`tests/ts_equivalencia/README_equivalencia.md`** — as tolerâncias campo a campo (não é
  comparação bit-a-bit — ver §3 do README) e o passo a passo pro seu runner em TS.
- **`tests/ts_equivalencia/CONTRATO_ERROS.md`** — a fronteira motor↔orquestrador: o que o
  motor devolve quando o input está incompleto (pista obrigatória faltando, origem/destino
  ausente) e o que **não** é sinalizado por ele (aeronave ausente, ICAO inexistente).

Protocolo: rode o motor TS com os parâmetros de cada caso (`entrada`) e compare a saída
contra `esperado`, campo a campo, com as tolerâncias do README. Casos `tipo_caso:
"contrato_input"` comparam o **sinal** (`status`/`faltando`/`aerodromos`), não uma rota.

Se rodar `verifica_gabarito.py` (nosso runner de referência) e der FAIL **só** em casos de
vento, rode de novo antes de reportar quebra — é instabilidade do CDN de vento no momento, não
bug (ver nota no README_equivalencia.md).

## Parte 2 — Validação de resposta do orquestrador

- **`PERGUNTAS_CAIO_roteiro.md`** — o roteiro de validação: números exatos onde é estável,
  checagem de coerência no vento, comportamento esperado (pergunta a pista quando falta,
  admite o que não sabe em vez de inventar).
- **`PERGUNTAS_ORQUESTRADOR_CAIO.md`** — o catálogo completo por trás do roteiro: o que
  perguntar, de qual campo vem a resposta, e o que **não** dá pra cobrar do motor (Seção B).
- **`gerar_gabarito_perguntas.py`** — ferramenta **opcional**, se quiser automatizar: gera
  `gabarito_perguntas_orquestrador.md/.json` com o esperado atual de cada pergunta a partir do
  motor **ao vivo**, junto com a `hora_partida_utc` e os frames do CDN usados na geração.

**O teste principal é no olho, seguindo o `PERGUNTAS_CAIO_roteiro.md`** — pergunte ao seu
orquestrador sobre as rotas do roteiro e confira se a resposta contém/reflete o que o motor
entrega. O harness (`gerar_gabarito_perguntas.py`) é só pra quem quiser automatizar essa
conferência, não é o caminho principal.

### Protocolo (se for usar o harness)
1. Pull do repo. Copie `.env.sh.example` pra `.env.sh`, preencha as credenciais, `source
   .env.sh` — aponta pro `jetstream` + CDN **ao vivo**.
2. Rode `python3 gerar_gabarito_perguntas.py` → gera `gabarito_perguntas_orquestrador.md/.json`
   com o esperado atual de cada pergunta.
3. **Na mesma janela** (mesmo bloco de 3h de previsão do CDN), pergunte ao seu orquestrador as
   mesmas perguntas, sobre as mesmas rotas/hora.
4. Compare:
   - **ESTÁVEL** (rota, distância, corredores, cruzeiro, TOC/TOD, tempo/combustível **sem
     vento**, íngreme, rumos, `faltou_input`) → tem que bater **exato**;
   - **VENTO** (tempo/combustível **com vento**) → dentro de **±1–2%** (só vale pra essa
     execução);
   - `faltou_input` (pista obrigatória, origem/destino ausente) → o orquestrador tem que
     **perguntar** o que falta ao piloto, não travar nem inventar.
   - Campos longos (`legs`, `rumos`) vêm truncados no `.md` — confira esses no `.json`.

## A fronteira (o ponto que travou na call)

O orquestrador só é testável contra o motor **no que o motor entrega** (Seção A do catálogo).
A **Seção B** — combustível **utilizável**/capacidade de tanque, **reserva** (45 min), **parada**
de reabastecimento, **alternativa** — **o motor não entrega**. Não teste isso contra o motor;
ele nunca dá esse lado. Ex.: "cabe no tanque?" depende do combustível **queimado** (o motor dá)
e da **capacidade** do tanque (o motor **não** dá — esse dado está em peso&balanceamento). Onde
essa lógica vai ficar (orquestrador, catálogo, camada nova) ainda não está definido — então isso
não é algo pra testar agora, é só pra saber que não vem do motor.

## Vento — por que não tem número fixo

O vento varia com a hora e com a atualização do CDN. Por isso o esperado de vento **não é
congelado**: você **regera** o gabarito no momento do teste (é o que o harness faz), e o frame
do CDN gravado no cabeçalho confirma que você testou na mesma janela. Se testar num bloco de 3h
diferente do da geração, os campos VENTO não vão bater — regere.

## Você tem Claude + Code

Dá pra automatizar a conferência da Parte 2: aponte seu Code pro `gerar_gabarito_perguntas.py` +
`PERGUNTAS_ORQUESTRADOR_CAIO.md` e monte um loop que (1) roda o harness, (2) dispara cada
pergunta no seu orquestrador, (3) faz o diff com o `.json` (exato pros ESTÁVEL, tolerância pros
VENTO). O gabarito já sai estruturado em `.json` justamente pra isso.
