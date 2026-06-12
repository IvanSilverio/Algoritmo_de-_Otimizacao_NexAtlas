# Pergunta para o administrador do banco — coordenadas de aeródromos

## Contexto (o que já verifiquei)

Estou implementando o motor de rota VFR (V1) e preciso da posição
(latitude/longitude) dos aeródromos de origem e destino, que o usuário
informa pelo código ICAO.

Investiguei o banco `jetstream` e confirmei, via queries diretas, que:

1. A tabela **`adhps`** tem 5.956 registros, mas só com as colunas
   `id`, `icao`, `type`, `created_at`, `updated_at`. **Não há coluna de
   geometria nem latitude/longitude.**

2. As tabelas **`adhp_metars`** e **`adhp_tafs`** se ligam à `adhps` por
   `adhp_id`, mas contêm apenas dados meteorológicos (METAR/TAF). O texto
   bruto do METAR/TAF não carrega coordenada da estação.

3. O catálogo **`geometry_columns`** lista todas as 15 tabelas com geometria
   do banco. Nenhuma é um cadastro de aeródromos com posição. As únicas com
   geometria Point são `cities` (centro de município, não a pista),
   `special_routes_waypoints_v2` (pontos visuais VFR, não aeródromos) e
   `airways_waypoints_normalized` (que está com 0 linhas — malha IFR ainda
   não ingerida).

## As perguntas

1. **Onde ficam hoje as coordenadas (lat/lon) de cada aeródromo da `adhps`?**
   - Existe em outra tabela/schema que eu não tenha acesso?
   - Vem de uma API ou serviço interno no momento da consulta?
   - Ou ainda não foi ingerida em lugar nenhum?

2. Se a intenção era a `adhps` ter geometria: **há previsão de popular essa
   coluna?** Em que prazo aproximado?

3. As tabelas `airways_waypoints_normalized` e
   `airways_connections_normalized` estão vazias. **Há previsão de ingestão
   da malha IFR?** (relevante para a V2, não bloqueia a V1)

4. Caso a fonte oficial não exista internamente a curto prazo: **há
   restrição em eu importar o cadastro público de aeródromos do DECEA/AISWEB
   para uma tabela própria**, enquanto a oficial não fica pronta?

## Por que isso bloqueia a V1

O motor de rota já está pronto e testado: ele percorre a malha de waypoints
visuais (`special_routes_*_v2`, que está completa e com geometria). O único
elo faltante é traduzir o ICAO digitado pelo usuário (ex.: "SBMT") na
coordenada do ponto de partida/chegada. Sem isso, o algoritmo não tem por
onde começar nem terminar a rota.
