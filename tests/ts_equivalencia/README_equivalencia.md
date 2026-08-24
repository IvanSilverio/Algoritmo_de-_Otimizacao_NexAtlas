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
    "hora_partida_utc_usada": "24/08/2026 15:00",
    "nota": "..."
  },
  "casos": [
    {
      "id": "lateral_portao_obrigatorio_com_pista",
      "cobertura": ["lateral:portao_por_cabeceira_pista_informada"],   // só documentação
      "entrada": {
        "origem": "SBBH", "destino": "SBFZ",
        "aeronave": "1n7s5U5J",            // id de published.aircraft_models
        "pista_origem": "13", "pista_destino": null,
        "hora_partida_utc": "24/08/2026 15:00"
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
          "segmentos": [{"x0_nm": 0.0, "x1_nm": 12.4, "fase": "subida",
                        "gs_kt": 92.3, "componente_cauda_kt": 5.1, "deriva_deg": -2.3}, ...],
          "tempo_min_vento": 236.8,
          "combustivel_vento": 94.1
        },
        "magnetico": [
          {"corredor": "CEASA", "declinacao_deg": -21.34, "rumo_magnetico_deg": 47.0,
           "rumo_verdadeiro_deg": 25.66}, ...
        ]
      }
    }
  ]
}
```

Campos de `entrada`:
- `aeronave`: **id** de `published.aircraft_models` (não o `designator_icao` — não é único
  no banco; ver `nexatlas_router/vertical/aircraft.py`).
- `pista_origem`/`pista_destino`: cabeceira em uso (ex.: `"13"`), só relevante nos 7
  aeródromos com regra de portão por pista; `null` quando não informada.
- `hora_partida_utc`: string no formato aceito por `parse_hora_utc` (`wind.py`) — fixa por
  caso, para o vento ser reprodutível.
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
| `vento.segmentos[].gs_kt` / `.componente_cauda_kt` | ± 0,1 kt | |
| `magnetico[].declinacao_deg` / `.rumo_magnetico_deg` / `.rumo_verdadeiro_deg` / `vento.segmentos[].deriva_deg` | **± 0,01°** | absorve o ruído ~0,0001° de implementação, mas detecta troca de edição do WMM (muda em graus) |
| `vertical.avisos` (texto) | **não comparado** | texto livre, específico da implementação/idioma; o que importa estruturalmente já está em `descida_ingreme_nm` |

Um caso "passa" quando **todos** os campos do bloco (lateral/vertical/vento/magnético)
batem dentro da tolerância. `verifica_gabarito.py` reporta PASS/FAIL por bloco, não só por
caso — um caso pode passar no lateral e falhar no vento, por exemplo, e isso já aponta onde
olhar.

### Declinação usa a DATA DO VOO — o gabarito não expira sozinho

A declinação (WMM) é calculada com a **data do voo** (`hora_partida_utc`), não com a data em
que o código roda (corrigido em `profile.py` — `magnetic.py` já suportava data explícita
desde o início; só faltava alguém passar). Isso vale tanto pro bloco `magnetico` quanto pra
`deriva_deg`/rumo verdadeiro do vento, que dependem da mesma declinação. Como `hora_partida_utc`
é **fixa por caso** neste gabarito, rodar `verifica_gabarito.py` daqui a um mês ou daqui a um
ano dá a **mesma declinação** — o snapshot é reprodutível de verdade, não depende de *quando*
se roda. Se algum dia o TS reproduzir cada caso usando a `entrada.hora_partida_utc` do JSON
(não a data corrente da máquina rodando o teste), o comportamento deles vai bater com o mesmo
princípio.

O WMM ainda tem variação secular contínua (~0,025°/ano medidos num ponto de MG) — mas agora
ela só aparece quando o `hora_partida_utc` de dois casos difere de fato (ex.: um voo em 2026 x
um em 2029), nunca por causa de quando o teste foi executado.

## 4. O que cada caso cobre

Ver a lista completa com a cobertura marcada na mensagem de entrega desta tarefa (ou o campo
`cobertura` de cada caso em `gabarito_rotas.json`). Resumo dos blocos exigidos por
`TAREFA_gabarito_TS_e_faxina.md` §1.3:

- **Lateral:** direto puro longo, corredor coerente (malha da ponta), portão obrigatório,
  portão por cabeceira de pista (com/sem pista informada, mesmo aeródromo), exceção de
  portão único ("X ou Y"), malha de passagem (vira direto), k-shortest (alternativas).
- **Vertical:** subida até teto de corredor, descida em degraus com `start` entre
  corredores, trecho final íngreme com aviso (SBPA→SBFL / SR22), combustível por fase (3
  unidades nativas diferentes: l/h, us gal/h, lb/h), aeronave sem dados de combustível.
- **Vento:** par recíproco (mesma hora de partida, sentidos opostos) para garantir cauda
  numa direção e proa na outra pelo mesmo campo de vento; deriva conferida nos trechos de
  través de qualquer um dos casos com vento.
- **Magnético:** corredores em cartas/regiões distantes (Norte, Nordeste, Centro-Oeste,
  Sudeste, Sul) — declinações bem diferentes entre si, então qualquer troca de edição do WMM
  entre as duas implementações apareceria em pelo menos um desses casos.

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

1. Ler este README e `gabarito_rotas.json`.
2. Para cada `caso.entrada`, rodar o algoritmo TS com os mesmos parâmetros (mesmo
   `origem`/`destino`/`aeronave`/`pista_origem`/`pista_destino`/`hora_partida_utc`).
3. Comparar a saída do TS contra `caso.esperado`, campo a campo, com as tolerâncias da
   tabela do §3 (não bit-a-bit).
4. Um caso "passa" quando os 4 blocos (lateral/vertical/vento/magnético) batem dentro da
   tolerância. Reportar PASS/FAIL por bloco, não só por caso — ajuda a isolar onde está a
   divergência (ex.: lateral bate mas o vento não → é a integração com o CDN de vento, não a
   rota).
5. Se um bloco falhar só na parte magnética/deriva por uma fração de grau pequena, confira
   primeiro se o TS está calculando a declinação com a **`hora_partida_utc` do caso** (não com
   a data corrente da máquina rodando o teste) — ver §3. Feito isso corretamente, o resultado
   não deve variar com *quando* o teste roda.
6. `verifica_gabarito.py` (Python) é a referência de como fazer essa comparação — o runner
   do TS deve seguir a mesma lógica campo a campo, só trocando "rodar Python" por "rodar TS".
