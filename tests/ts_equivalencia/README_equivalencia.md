# Gabarito de equivalência Python ↔ TypeScript

Este diretório é o **contrato de equivalência** entre o motor de referência em Python
(`nexatlas_router/`, temporário, mantido como referência/teste até meados de 2027) e a
transcrição do algoritmo em TypeScript (o produto). Ele prova que a reimplementação em TS
produz **a mesma rota e o mesmo perfil de voo**, dentro de tolerâncias documentadas — não
bit-a-bit (as duas linguagens usam bibliotecas WMM diferentes; ver §3).

## Arquivos

- **`gabarito_rotas.json`** — o gabarito em si: um snapshot **congelado** de entradas +
  saídas esperadas, gerado a partir do estado atual do banco `jetstream` (schema
  `published`) e do CDN de terreno/vento. É o arquivo que o time do TS consome.
- **`verifica_gabarito.py`** — roda o **nosso Python ao vivo** contra o gabarito e reporta
  PASS/FAIL por caso e por bloco. Serve de (a) prova de que o gabarito está correto (deve
  dar 100%) e (b) **exemplo de como comparar**, para o time do TS espelhar no runner deles.
- **`motor_gabarito.py`** — pipeline compartilhado (`build_subgraph` → `plan_v1_route` →
  `plan_from_v1`) usado tanto para gerar quanto para verificar o gabarito, para as duas
  pontas nunca divergirem na forma de CALCULAR (só o comparador muda).
- **`gerar_gabarito.py`** — regenera `gabarito_rotas.json` do zero. Só rodar quando o banco
  mudar de forma relevante para algum dos casos (ver §5).
- **`CONTRATO_ERROS.md`** — o que o motor devolve quando o input está incompleto (pista
  obrigatória faltando) ou quando um comportamento (não erro) muda o formato da saída (sem
  hora → sem vento), pro orquestrador (Caio) mapear pra pergunta ao piloto. Separado do que
  NÃO é do motor (aeronave ausente, ICAO inválido, etc.) — ver TAREFA_gabarito_v2_contrato.md.

## 1. Como rodar o runner de referência (nosso lado)

```bash
source .env.sh                                      # credenciais do jetstream
python3 tests/ts_equivalencia/verifica_gabarito.py           # todos os casos
python3 tests/ts_equivalencia/verifica_gabarito.py --caso <id>   # um caso só
python3 tests/ts_equivalencia/verifica_gabarito.py -v         # mostra os diffs de cada FAIL
```

Precisa de acesso ao banco `jetstream` **ao vivo** e ao CDN de terreno/vento — o runner
recalcula tudo na hora e compara com o congelado no JSON; ele não roda offline.

## 2. Formato de `gabarito_rotas.json`

```jsonc
{
  "meta": {
    "gerado_em": "...",          // timestamp ISO-8601 UTC da geração
    "commit": "0b9327e",          // hash curto do commit em que foi gerado
    "wmm_edition": "WMM-2025",
    "vento_offset_h": 6,           // regra usada pelos casos vento_modo=relativo (ver §3)
    "agora_relativa_usada": "...", // valor resolvido NA GERAÇÃO — informativo, não é relido
    "nota": "..."
  },
  "casos": [
    {
      "id": "lateral_portao_obrigatorio_com_pista",
      "tipo_caso": "rota",       // "rota" (default) ou "contrato_input" — ver caso-sinal abaixo
      "cobertura": ["lateral:portao_por_cabeceira_pista_informada"],   // só documentação
      "entrada": {
        "origem": "SBBH", "destino": "SBFZ",
        "aeronave": "1n7s5U5J",            // id de published.aircraft_models
        "pista_origem": "13", "pista_destino": null,
        "hora_partida_utc": null            // null = SEM vento (default); ver §3
      },
      "esperado": {
        "lateral": {
          "rota": [{"id": "...", "name": "SBBH"}, ...],   // NA ORDEM — ver tolerância
          "distancia_total_nm": 812.3,
          "distancia_direta_nm": 795.1,
          "corredores_usados": [{"name": "CEASA", "is_mandatory": true}, ...]
        },
        "vertical": {
          "vertices": [{"x_nm": 0.0, "alt_ft": 3010.0, "tipo": "origem", "nome": null, "real": true}, ...],
          "toc_nm": 42.3, "tod_nm": 760.1,
          "tempo_min": {"subida": 12.4, "cruzeiro": 210.5, "descida": 18.2, "total": 241.1},
          "combustivel": {"subida": 6.1, "cruzeiro": 84.2, "descida": 5.4, "total": 95.7, "unidade": "l"},
          "avisos": ["..."],                 // texto — informativo, NÃO comparado (ver §3)
          "descida_ingreme_nm": [[758.0, 760.1]]   // trechos fora da razão máxima (vermelho)
        },
        "vento": {
          "segmentos": [],                   // vazio quando entrada.hora_partida_utc é null
          "tempo_min_vento": null,
          "combustivel_vento": null
        },
        "magnetico": [
          {"corredor": "CEASA", "declinacao_deg": -21.34, "rumo_magnetico_deg": 47.0,
           "rumo_verdadeiro_deg": 25.66}, ...
        ]
      }
    },
    {
      "id": "lateral_portao_obrigatorio_sem_pista",
      "tipo_caso": "contrato_input",       // caso de CONTRATO — esperado é um SINAL, não rota
      "cobertura": ["contrato:pista_obrigatoria_faltando"],
      "entrada": {
        "origem": "SBBH", "destino": "SBFZ", "aeronave": "1n7s5U5J",
        "pista_origem": null, "pista_destino": null, "hora_partida_utc": null
      },
      "esperado": {
        "status": "faltou_input",
        "faltando": ["pista_origem"],
        "aerodromos": {"pista_origem": "SBBH"},
        "mensagem": "SBBH tem regra de cabeceira; informe a pista de decolagem para calcular a rota."
      }
    }
  ]
}
```

Um caso é de **rota** (`esperado` tem as chaves `lateral`/`vertical`/`vento`/`magnetico`) ou de
**contrato** (`esperado` tem a chave `status` — um sinal estruturado, não uma rota) — ver
`CONTRATO_ERROS.md` pro contrato completo desses sinais.

Campos de `entrada`:
- `aeronave`: **id** de `published.aircraft_models` (não o `designator_icao` — não é único
  no banco; ver `nexatlas_router/vertical/aircraft.py`).
- `pista_origem`/`pista_destino`: cabeceira em uso (ex.: `"13"`), só relevante nos 7
  aeródromos com regra de portão por pista; `null` quando não informada.
- `hora_partida_utc`: **`null` por padrão** (o motor não calcula vento sem hora — ver §3);
  string ISO-8601 só nos casos que pedem vento de propósito (`vento_modo` presente).
- `vento_modo`/`vento_offset_h` (só quando o caso pede vento): sempre `"relativo"`/o número de
  horas — ver §3, "os dois modos de vento".
- `vento_par_reciproco` (só nos 2 casos de par ida/volta): mesmo valor nos dois, usado pela
  checagem cruzada de `verifica_gabarito.py` — ver §3.
- `aeronave_observacao` (só quando presente): sinaliza que a aeronave é uma **aeronave real
  do banco com o combustível zerado artificialmente** — ver §4, caso
  `vertical_aeronave_sem_combustivel`.

## 3. Tolerâncias (NÃO é comparação bit-a-bit)

Python (`pygeomag`) e o produto TS (`@cristianob/geomagnetism`) usam os **mesmos
coeficientes NOAA do WMM-2025**, mas implementações diferentes — a diferença entre as duas
no mesmo ponto/data é da ordem de **0,0001°** (ruído de implementação, não do modelo). Some
a isso que o algoritmo em si (Dijkstra com fase, otimizador, etc.) pode ter caminhos de
cálculo internos ligeiramente diferentes entre as duas linguagens sem que o RESULTADO
mude. Por isso a comparação usa tolerância, campo a campo:

| Campo | Tolerância | Observação |
|---|---|---|
| `lateral.rota` (sequência de IDs) | **igualdade exata**, na ordem | são os MESMOS waypoints do banco; não há motivo para IDs diferirem |
| `lateral.distancia_total_nm` / `distancia_direta_nm` | ± 0,05 NM | |
| `lateral.corredores_usados` | igualdade exata (nome + `is_mandatory`) | categórico |
| `vertical.vertices[].x_nm`, `toc_nm`, `tod_nm`, `descida_ingreme_nm` | ± 0,1 NM | posições ao longo da rota |
| `vertical.vertices[].alt_ft` | ± 1 ft | |
| `vertical.vertices[].tipo` / `.nome` / `.real` | igualdade exata | categórico |
| `vertical.tempo_min.*` | ± 0,1 min | por fase e total |
| `vertical.combustivel.*` | ± 0,1 (unidade nativa) | ver `combustivel.unidade` — **nunca normalizar** entre l/h, us gal/h, lb/h |
| `vento.segmentos[].gs_kt` / `.componente_cauda_kt` | ± 0,1 kt | só nos casos **sem** `vento_modo` (comparação exata); ver "os dois modos de vento" abaixo |
| `magnetico[].declinacao_deg` / `.rumo_magnetico_deg` / `.rumo_verdadeiro_deg` / `vento.segmentos[].deriva_deg` | **± 0,01°** | absorve o ruído ~0,0001° de implementação, mas detecta troca de edição do WMM (muda em graus) |
| `vertical.avisos` (texto) | **não comparado** | texto livre, específico da implementação/idioma; o que importa estruturalmente já está em `descida_ingreme_nm` |
| `sinal.status` / `.faltando` / `.aerodromos` (casos `tipo_caso: "contrato_input"`) | igualdade exata | ver `CONTRATO_ERROS.md` — `sinal.mensagem` é texto livre, **não comparado** |

Um caso "passa" quando **todos** os campos do bloco (lateral/vertical/vento/magnético — ou só
`sinal`, nos casos de contrato) batem dentro da tolerância. `verifica_gabarito.py` reporta
PASS/FAIL por bloco, não só por caso — um caso pode passar no lateral e falhar no vento, por
exemplo, e isso já aponta onde olhar. Um caso de contrato só tem o bloco `sinal` — os outros 4
aparecem como "—" (não se aplica, nunca conta contra o PASS/FAIL) no relatório.

### Os dois modos de vento — por que a maioria dos casos não leva hora

TAREFA_gabarito_v2_contrato.md (26/08/26): antes, TODO caso levava uma `hora_partida_utc` FIXA
(a mesma data pra tudo), e isso incluía sempre um bloco `vento` calculado e comparado por
igualdade exata — inclusive nos casos que não tinham NADA a ver com vento. Isso expirava
sozinho: o CDN só serve ~5 dias de previsão a partir de "agora", então uma data congelada há
algumas semanas já não bate mais com o que o CDN devolve hoje (achado ao vivo, 26/08: o
gabarito antigo estava em 0/25 por causa disso). O modelo mudou pra dois modos, escolhidos por
caso:

1. **Sem hora (a maioria — default do motor desde a TAREFA_pista_obrigatoria_e_vento_
   default.md):** `entrada.hora_partida_utc = null`. O motor não calcula vento — `esperado.vento`
   fica `{"segmentos": [], "tempo_min_vento": null, "combustivel_vento": null}` **pra sempre**.
   Como não depende do CDN, esse bloco NUNCA expira — comparação por igualdade exata (trivial:
   os dois lados sempre vazios).
2. **`vento_modo: "relativo"` (só os 4 casos que testam vento de propósito):**
   `entrada.hora_partida_utc` é o valor resolvido NA GERAÇÃO (`agora` daquele momento +
   `vento_offset_h` horas — hoje 6h), só informativo. `verifica_gabarito.py` **recalcula** essa
   hora com o `agora` da própria execução (mesma regra, `+vento_offset_h`) em vez de reler o
   valor congelado — assim sempre cai dentro da janela de previsão do CDN, não importa quando
   rodar. Como o vento REAL muda a cada previsão, o valor exato (`gs_kt`, `componente_cauda_kt`)
   não é mais comparável — a comparação é ESTRUTURAL (`compara_vento_relativo` em
   `verifica_gabarito.py`): mesma contagem de segmentos (isso não depende do vento, só da
   geometria — comparado exato), groundspeed positiva/plausível em cada um, e `tempo_min_vento`
   existe.

   Os 2 casos `vento_par_reciproco_ida`/`_volta` (mesmo par, sentidos opostos, mesma hora — daí
   o campo `vento_par_reciproco` com o mesmo valor nos dois) recebem ainda uma **checagem
   cruzada**: como as duas pernas voam o MESMO campo de vento em rumos opostos, uma tem que sair
   mais rápida e a outra mais lenta que sem vento — nunca as duas iguais. Achado ao vivo (26/08):
   a checagem NÃO afirma qual perna especificamente leva cauda — isso é o forecast do momento,
   muda de execução pra execução (a mesma rota que era cauda na ida no snapshot antigo virou
   proa na hora relativa de outra execução, e está certo, é o vento mudando, não bug); a
   invariante que não muda é só "efeitos opostos".

### Declinação usa a data de execução — o WMM não é sensível a isso

O bloco `magnetico` (e a `deriva_deg` do vento) usa a declinação WMM calculada na data em que
`gerar_gabarito.py`/`verifica_gabarito.py` rodam — não em `hora_partida_utc` (que agora nem
existe na maioria dos casos). Isso é seguro: a variação secular do WMM é de ~0,025°/ano medidos
num ponto de MG, contra uma tolerância de 0,01° — dá margem de vários MESES entre gerar e
verificar antes de qualquer risco de estourar a tolerância por causa só da data. Regenerar o
gabarito com a cadência normal (§5) é bem mais frequente que isso.

## 4. O que cada caso cobre

Ver a lista completa com a cobertura marcada na mensagem de entrega desta tarefa (ou o campo
`cobertura` de cada caso em `gabarito_rotas.json`). Resumo dos blocos exigidos por
`TAREFA_gabarito_TS_e_faxina.md` §1.3:

- **Lateral:** direto puro longo, corredor coerente (malha da ponta), portão obrigatório,
  portão por cabeceira de pista (com pista informada — casando ou não com a regra —, mesmo
  aeródromo), exceção de portão único ("X ou Y"), malha de passagem (vira direto), k-shortest
  (alternativas).
- **Vertical:** subida até teto de corredor, descida em degraus com `start` entre
  corredores, trecho final íngreme com aviso (SBPA→SBFL / SR22), combustível por fase (3
  unidades nativas diferentes: l/h, us gal/h, lb/h), aeronave sem dados de combustível.
- **Vento:** par recíproco (`vento_par_reciproco_ida`/`_volta`, mesma hora relativa, sentidos
  opostos) para garantir efeito OPOSTO nas duas pernas pelo mesmo campo de vento (checagem
  cruzada — ver acima); deriva conferida nos trechos de través de qualquer um dos casos com
  vento; par mínimo sem-hora/com-hora (`contrato_sem_hora_sem_vento`/`_com_hora_com_vento`,
  mesmo par/aeronave) isolando o efeito do default "sem hora = sem vento".
- **Magnético:** corredores em cartas/regiões distantes (Norte, Nordeste, Centro-Oeste,
  Sudeste, Sul) — declinações bem diferentes entre si, então qualquer troca de edição do WMM
  entre as duas implementações apareceria em pelo menos um desses casos.
- **Contrato de input** (`tipo_caso: "contrato_input"`, `esperado` é um sinal — ver
  `CONTRATO_ERROS.md`): pista obrigatória faltando (1 lado / 2 lados), pista informada mas
  que não casa nenhuma regra do aeródromo (`contrato_pista_nao_casa` — rota normal, não é
  sinal), sem hora → sem vento, com hora → com vento.

### Nota de transparência: caso `vertical_aeronave_sem_combustivel`

Hoje, das 130 aeronaves de `published.aircraft_models` com performance completa (teto,
razões de subida/descida, velocidades), **nenhuma** tem os 3 campos de combustível
incompletos ao mesmo tempo — ou tem os três, ou não tem performance suficiente para entrar
no catálogo. Não existindo esse caso real no banco hoje, o caso `vertical_aeronave_sem_
combustivel` usa uma aeronave real (Cessna 172, id `1n7s5U5J`) com os 3 campos de
combustível **zerados artificialmente** só para exercitar o caminho `combustivel = None` do
`profile.py` (ele nunca pode quebrar o perfil). Isso está sinalizado explicitamente no JSON
pelo campo `entrada.aeronave_observacao` — não é uma aeronave nem um voo reais, é uma
aeronave-dublê para cobertura de código.

## 5. Como e quando regenerar

```bash
source .env.sh
python3 tests/ts_equivalencia/gerar_gabarito.py
```

Regenerar quando o banco `jetstream` mudar de forma relevante para algum dos pares
origem/destino do gabarito (nova carta REA, corredor alterado, portão redocumentado,
aeronave removida do catálogo) — **não** é preciso regenerar por rotina; é um snapshot,
não uma bateria ao vivo (essa é `run_test_cases.py`/`testes_voos.py`, que continuam
rodando contra o banco atual e pegam mudança real de dado). O gabarito prova
**equivalência de código**; a bateria ao vivo prova **dado correto**. São coisas
diferentes e as duas continuam necessárias.

## 6. Passo a passo para o time do TS

1. Ler este README, `CONTRATO_ERROS.md` e `gabarito_rotas.json`.
2. Para cada caso, checar `tipo_caso` primeiro:
   - `"rota"` (a maioria): rodar o algoritmo TS com os parâmetros de `caso.entrada` (mesmo
     `origem`/`destino`/`aeronave`/`pista_origem`/`pista_destino`/`hora_partida_utc` — a
     maioria tem `hora_partida_utc: null`, o que significa NÃO calcular vento, não "agora").
     Comparar a saída contra `caso.esperado.{lateral,vertical,vento,magnetico}`, campo a
     campo, com as tolerâncias do §3.
   - `"contrato_input"`: `caso.esperado` é um SINAL (`status`/`faltando`/`aerodromos`), não
     uma rota — rodar o algoritmo TS com os mesmos parâmetros e conferir que ele recusa a
     rota com o MESMO sinal estruturado (ver `CONTRATO_ERROS.md`). `sinal.mensagem` é texto
     livre, não comparar por igualdade.
3. Nos casos com `entrada.vento_modo == "relativo"` (só 4 no gabarito hoje), NÃO reler
   `entrada.hora_partida_utc` literalmente — recalcular `agora + entrada.vento_offset_h`
   horas na hora de rodar o teste (mesma regra que `verifica_gabarito.py` usa), e comparar o
   bloco `vento` por ESTRUTURA (contagem de segmentos, groundspeed positiva), não por valor
   exato — ver §3, "os dois modos de vento".
4. Um caso de rota "passa" quando os 4 blocos batem dentro da tolerância; um caso de contrato
   "passa" quando o sinal bate. Reportar PASS/FAIL por bloco, não só por caso — ajuda a isolar
   onde está a divergência (ex.: lateral bate mas o vento não → é a integração com o CDN de
   vento, não a rota).
5. Se um bloco falhar só na parte magnética/deriva por uma fração de grau pequena, é
   provavelmente só a data de execução ter mudado (§3 — a declinação não usa
   `hora_partida_utc`, usa a data corrente; a tolerância de 0,01° cobre isso por meses) —
   não é regressão.
6. `verifica_gabarito.py` (Python) é a referência de como fazer essa comparação — o runner
   do TS deve seguir a mesma lógica campo a campo, só trocando "rodar Python" por "rodar TS".
