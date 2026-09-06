# Roteiro de validação de resposta do orquestrador (para o Caio)

> **Objetivo:** verificar se o orquestrador **responde de forma coerente e usa as informações que o motor fornece**. Não é diff de bit — é conferir se a resposta **contém/reflete** o que tem que conter. Três tipos de checagem:
> - **Estável** → tem número exato pra bater (a resposta pode arredondar; "634 NM", "88 galões" contam).
> - **Vento** → o valor muda com a hora, então confira **coerência** (considerou o vento? a direção faz sentido?), não o número.
> - **Comportamento** → pergunta a pista quando falta; admite o que não sabe em vez de inventar.
>
> Os números abaixo são a **saída real do motor** para cada entrada (motor já corrigido). Os de vento são de **uma execução** (decolagem 17:00Z) — variam com a hora.

---

## Rota A — SIOZ → SBSL, C182

### Estável (bata o número)
| Pergunta | A resposta tem que conter |
|---|---|
| "Qual a rota de SIOZ pra SBSL?" | a sequência **SIOZ → ILHA DOS CARANGUEJOS → ILHA DO MEDO → BATATÃ CAEMA → SBSL** |
| "Quantas milhas tem? E em linha reta?" | **~634 NM** total (633,99), **~628 NM** direta |
| "Passou por corredor ou foi direto?" | passou por **2 corredores: FOXTROT e ECHO** (não pode dizer que foi direto) |
| "Qual o cruzeiro do C182?" | **9.500 ft** (e nivelou) |
| "Quanto de combustível gasta?" | **~87,5 us-gal** total (com unidade); por fase ~5,8 subida / ~77 cruzeiro / ~4,7 descida |
| "Onde começa a descida?" | **TOD ~584 NM** (subida termina ~25 NM) |
| "Tem trecho de descida fora do limite da aeronave? Onde?" | **SIM** — trecho final **BATATÃ CAEMA → SBSL**, razão ~605 fpm acima da máxima do banco (500 fpm); tem que **avisar**, e só na **perna final** |
| "Qual o rumo das pernas?" | rumos **magnéticos** (ex.: SIOZ→ILHA DOS CARANGUEJOS ~80°, ILHA DOS CARANGUEJOS→ILHA DO MEDO ~52°) |

### Vento (confira coerência, não o número)
| Pergunta | A resposta tem que refletir |
|---|---|
| "Com decolagem às 17Z, o vento muda o tempo de voo?" | tem que **considerar o vento** (tempo com vento ≠ sem vento). No exemplo: ~325 min sem vento → ~381 min com vento. O que importa é refletir que o vento **aumentou** o tempo, com direção coerente. |
| "E o combustível, muda com o vento?" | **sobe junto** (mais tempo = mais combustível). No exemplo ~88 → ~103 us-gal. |
| "Não informei hora — dá pra considerar o vento?" | tem que dizer que **sem hora não tem vento** (não inventa vento); a rota sai completa mesmo assim. |

---

## Rota B — SBRJ → SBMT (comportamento)

| Pergunta | A resposta tem que fazer |
|---|---|
| "Rota de SBRJ pra SBMT" (**sem informar pista**) | **PERGUNTAR a pista** — de decolagem em SBRJ **e** de pouso em SBMT (os dois têm regra de cabeceira). Não pode travar nem inventar rota. |
| "É SBRJ pra SBMT, pista 99" (**pista que não existe**) | **NÃO travar**; devolver rota normal (~199 NM, passa por PORTÃO MARAPENDI e FOXTROT, cruzeiro 6.500 ft). Pista que não casa nenhuma regra → roda pela via normal, sem forçar portão. |
| "Quero uma rota pra SBRF" (**sem informar a origem**) / "Quero uma rota de SBRJ" (**sem informar o destino**) | O orquestrador tem que **dizer que não foi informada a origem/destino** pra calcular a rota e **pedir** o aeródromo que falta. Não pode travar nem inventar a rota. |

---

## Fronteira — o motor NÃO fornece (a resposta tem que admitir, não inventar)
| Pergunta | A resposta tem que fazer |
|---|---|
| "Cabe no tanque? / preciso parar pra reabastecer?" | **não inventar** capacidade nem parada. O motor dá o combustível **queimado** (Rota A ~87,5 us-gal), mas **não** dá a capacidade do tanque — a resposta ou usa uma fonte real (se o orquestrador tiver) ou diz que **não tem esse dado**. O que não pode é chutar um número. |
| "Qual a reserva de combustível?" | idem — não é do motor; não inventar os 45 min. |

---

_Nota: o teste é de **coerência e uso da informação**, não de igualdade exata (essa é a Parte 1, a equivalência Python↔TS contra o `gabarito_rotas.json`). Aqui: a resposta pegou o que o motor entrega e respondeu com sentido? Nos campos estáveis, bateu o número (com arredondamento)? Nos de vento, considerou o vento? Nos de fronteira, admitiu o que não sabe?_
