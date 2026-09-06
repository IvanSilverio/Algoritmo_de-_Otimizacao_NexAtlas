# Perguntas de aceitação do orquestrador — o que o algoritmo alimenta (para o Caio)

> Objetivo: verificar se o orquestrador **responde certo com base no que o motor de rotas entrega**. Cada pergunta da **Seção A** tem resposta **determinística**: ela está num campo da saída estruturada do motor para aquela rota. A **Seção B** lista o que **NÃO** vem do motor — pra não cobrar do orquestrador (nem do algoritmo) um dado que nunca chega. Base: `tests/ts_equivalencia/CONTRATO_ERROS.md` (a fronteira motor↔orquestrador). Os **nomes exatos dos campos** estão no `CONTRATO_ERROS.md` e no `gabarito_rotas.json` — aqui descrevo o dado, não decoro a chave.

## Como usar (protocolo)
Para cada pergunta: escolha uma rota R (origem, destino, aeronave e — quando for de vento — a hora de partida UTC). **Rode R no motor e guarde a saída estruturada (JSON).** Faça a pergunta ao orquestrador **sobre R** e compare a resposta dele com o **campo indicado** da saída do motor. **A saída do motor é o gabarito**; a resposta do orquestrador tem que bater com ela (tolerância de arredondamento e idioma). Não fixe números neste documento — o esperado é sempre o JSON do motor para a rota exata testada (o vento, por exemplo, muda com a hora).

---

## Seção A — o orquestrador DEVE acertar (respondível pela alimentação do motor)

### Rota e distância
- "Qual a rota de {O} para {D}?" → a **sequência de pontos** (nomes, na ordem). Conferir: mesma sequência.
- "Quantas milhas tem a rota?" → **distância total (NM)**. "E em linha reta?" → **distância direta (NM)**.
- "Por quais corredores REA a rota passa?" → **lista de corredores usados**. "Passou por corredor ou foi direto?" → lista vazia = rota direta autorizada.
- "Quais são as pernas, trecho a trecho?" → **legs** (de → para).

### Vertical (altitude / cruzeiro)
- "Qual a altitude de cruzeiro para {aeronave}?" → **cruzeiro (ft)**. "Chegou a nivelar?" → **flag de cruzeiro alcançado**.
- "Onde começa a descida (e onde termina a subida)?" → **TOC / TOD (NM)**.
- "Quanto tempo em cada fase (subida / cruzeiro / descida)?" → **tempo por fase (min)**.

### Vento  ⟵ *o Caio disse que isso não estava na versão que ele puxou; depois do push, é o teste-chave*
- "Com decolagem às {hora}Z, o vento muda o tempo de voo? Quanto?" → comparar **tempo total com vento** vs **tempo total sem vento**.
- "E o combustível, muda com o vento?" → **combustível total com vento** vs **sem vento**.
- "Não informei hora — dá pra considerar o vento?" → **NÃO** (comportamento correto do contrato): sem hora de partida, o bloco de vento sai vazio (sem segmentos; tempo e combustível de vento nulos) e **a rota sai completa mesmo assim**. O orquestrador deve dizer que sem hora não há vento (e, se quiser, pedir a hora) — **nunca inventar vento**.

### Combustível
- "Quanto de combustível {aeronave} gasta nessa rota?" → **combustível total** (+ unidade + tipo de combustível). "Por fase?" → **combustível de subida / cruzeiro / descida**.
- "E se a aeronave não tem dados de combustível?" → o motor devolve **combustível nulo** (resultado parcial válido, não é erro). O orquestrador deve **avisar** que não deu pra estimar — não travar.

### Aviso de descida  ⟵ *na call o orquestrador já pegou isso; bom fixar como teste de regressão*
- "Tem trecho de descida fora do limite da aeronave?" → **flag de trecho íngreme** + **posição (NM)**. O orquestrador deve avisar **onde**, e o aviso deve aparecer só na **perna final**.

### Rumos (magnético)
- "Qual o rumo de cada perna?" → **rumo por perna** (já em **magnético** — o motor reconverte da declinação WMM na data do voo). Conferir contra o campo de rumo da perna.

### Sinais de input (o motor devolve estruturado — mapear direto pra uma pergunta ao piloto)
- "Rota de {aeródromo com regra de cabeceira} sem informar a pista." → o motor devolve **`status: "faltou_input"`** com **`faltando`** e **`aerodromos`**. O orquestrador deve **perguntar a pista** (uma pergunta por item de `faltando`), não travar nem inventar. Ex.: **SBRJ→SBMT sem pista** → pede pista de decolagem em SBRJ **e** de pouso em SBMT. (7 aeródromos com regra: SBBH, SBNT, SBJR, SBRJ, SBJD, SBMT, SDCO.)
- "Informei uma pista que não existe naquele aeródromo." → o motor **não** trava: devolve rota normal (sem forçar portão). O orquestrador **não** deve tratar como erro.
- "Não informei o aeródromo de origem e/ou destino (campo vazio)." → o motor devolve **`status: "faltou_input"`** com **`faltando`** (`"origem"` e/ou `"destino"`) — mesmo formato do sinal de pista, mas sem `aerodromos` preenchido (não há ICAO nenhum: é isso que falta). O orquestrador deve **perguntar o aeródromo** (uma pergunta por item de `faltando`), não travar nem propagar erro genérico. *(05/09/26, TAREFA_faltou_input_origem_destino.md — antes caía no mesmo `LookupError` genérico de um ICAO inexistente; agora é distinto. ICAO **informado mas inexistente** continua caindo no `LookupError` de sempre — não é este sinal.)*

---

## Seção B — o motor NÃO entrega (não cobrar do algoritmo)  ⟵ *pontos que o Caio tentou/mencionou e que não saem da saída do motor*

> Aqui está o que **o motor não fornece**. Onde cada coisa vai ser resolvida (orquestrador, catálogo, camada nova) **ainda não está definido** — este documento não atribui responsabilidade; só marca que o dado não vem do motor, pra não testar o orquestrador contra ele.

- **Combustível utilizável / capacidade de tanque** (pra dizer "não cabe no tanque"): está no banco de **peso & balanceamento**, **não** no de performance — o motor **não entrega**. Foi o que travou o teste do Caio na call.
- **Reserva de combustível** (ex.: 45 min): regra de planejamento — **não vem do motor**.
- **Parada de reabastecimento / aeroporto de alternativa**: o motor **não** planeja parada nem alternativa (é feature futura); ele dá distância/tempo/combustível de **uma** perna. "A rota é longa demais, precisa parar?" **não** é respondível pela saída do motor.
- **Aeronave ausente pro vertical**: **não** existe `faltou_input: aeronave`. Sem aeronave, o motor só não calcula a vertical (a lateral sai normal) — o motor não sinaliza isso como erro.
- **ICAO informado mas inexistente**: continua caindo no `LookupError` genérico de sempre — não é um sinal estruturado como o `faltou_input` de origem/destino **ausente** (esse já é distinto, ver Seção A). O motor não diferencia "ICAO digitado errado" de outro tipo de erro de busca; ele só sinaliza que a busca falhou.
- **Formatação da resposta** (unidades por idioma, layout): apresentação — o motor só devolve os números estruturados.

---

## Cobertura dos pontos que o Caio levantou na call
| Ponto do Caio | Onde cai | Como testar |
|---|---|---|
| Vento + combustível não estava na versão que puxou | Seção A (Vento, Combustível) | depois do push, rodar as perguntas de Vento/Combustível |
| Rota longa → sugerir parada | Seção B (paradas) | não sai do motor (feature futura) |
| "Não cabe no tanque" (combustível utilizável) | Seção B (peso&balanceamento) | dado não vem do motor |
| Reserva 45 min | Seção B (reserva) | não vem do motor |
| Orquestrador "freestyle" (não formata/coleta) | Seção A inteira | as perguntas fixam o que ele tem que extrair certo |
