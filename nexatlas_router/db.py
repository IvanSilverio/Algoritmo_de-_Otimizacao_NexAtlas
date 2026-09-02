"""Extração do subgrafo regional — esquema **published** (jetstream).

Mudança de arquitetura (v2 DESCONTINUADO):
  - published.adhps(id, designator_icao, type, geom Point)  <- icao renomeado p/ designator_icao
  - published.special_routes_waypoints(id, name, type, chart, geom Point)
  - published.special_routes_connections(id, source_id, target_id, name,
        type, is_mandatory, heading, lower_limit, higher_limit,
        dimensions_nm, class, atc, frequency[], geom LineString)

Decisões de arquitetura desta versão:
  * Peso da aresta: ST_Length(geom::geography) mede o corredor REAL (curvas).
    Fallback para ST_DistanceSphere entre os nós quando a LineString for nula.
  * Digrafo assimétrico: cada sentido é uma linha própria. Nunca espelhar.
  * adhps NÃO precisa mais de tabela de coordenadas externa: a coluna geom é
    nativa. A query lê ST_X(geom)/ST_Y(geom) direto de published.adhps.
  * Filtro REA: TODAS as queries de waypoints e connections aplicam
    `WHERE type = 'REA'` — ignoramos aerovias IFR e qualquer outro tipo.

NOTA SOBRE O TIPO DA COLUNA geom
--------------------------------
O dicionário de dados descreve geom como "JSON / GeoJSON". Se no banco a
coluna for de fato GEOMETRY(Point/LineString, 4326) (caso em que ST_X/ST_Y/
ST_Length operam direto, como pede a especificação), use as queries como
estão. Se a coluna for armazenada como JSON/JSONB literal, basta trocar
`geom` por `ST_GeomFromGeoJSON(geom::text)` nas funções ST_* — ver a
constante GEOM_EXPR abaixo, que centraliza essa escolha em um único ponto.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Iterable, Optional

from .geo import LonLat
from .graphmodel import Edge, Node, RouteGraph, border_score
from .portoes import (resolver_pontos_obrigatorios, PortaoDesconectadoError,
                      direcao_exige_pista, PistaObrigatoriaError, carta_de)


def _parse_linestring(geom_json: Optional[str]) -> Optional[tuple]:
    """GeoJSON (LineString) -> tupla ((lon,lat), ...) para o portão geométrico.

    Aceita None/tipo inesperado e devolve None (a aresta funciona sem traçado;
    o portão cai na reta nó->nó). Pontos isolados ou vazios também viram None.
    """
    if not geom_json:
        return None
    try:
        coords = json.loads(geom_json).get("coordinates")
    except (ValueError, AttributeError):
        return None
    if not coords or not isinstance(coords, list):
        return None
    pts = tuple((float(c[0]), float(c[1])) for c in coords if len(c) >= 2)
    return pts if len(pts) >= 2 else None

# ---------------------------------------------------------------------------
# Esquema published (única fonte da verdade; v2 removido)
# ---------------------------------------------------------------------------
SCHEMA = "published"
WP_TABLE = f"{SCHEMA}.special_routes_waypoints"
CONN_TABLE = f"{SCHEMA}.special_routes_connections"
ADHP_TABLE = f"{SCHEMA}.adhps"
AIRSPACE_TABLE = f"{SCHEMA}.airspaces"       # polígonos das TMAs (score de fronteira, PONTO 3)

# TAREFA_cartas_relevantes.md: fração mínima dos waypoints de uma carta REA
# que um designator (ctr/atz ou, na falta, tma) precisa cobrir pra virar a
# "carta dona" dela. Calibrado ao vivo contra as 53 cartas REA do banco: com
# 0.8, só 4 ficam abaixo do limiar em qualquer tipo (REA Londrina 73%, REA
# Parintins 0%, REH Bacia de Santos 4% — corredor offshore —, REH Cabo Frio
# 62%) — nenhuma delas aparece nos casos de validação da tarefa. Essas ficam
# "ambíguas": usam o melhor disponível mesmo abaixo do limiar (nunca ficam
# sem dono), sinalizadas em meta (ver _chart_home_designator).
RELEVANCE_COVERAGE_THRESHOLD = 0.8

# Ponto único para alternar entre coluna geometry nativa e GeoJSON.
# Coluna geometry nativa (especificação):   "{col}"
# Coluna JSON/JSONB GeoJSON (alternativa):   "ST_GeomFromGeoJSON({col}::text)"
def GEOM_EXPR(col: str) -> str:
    return col  # geometry nativa — troque por ST_GeomFromGeoJSON(col+"::text") se for JSONB

# ---------------------------------------------------------------------------
# SQL — malha VFR no esquema published (sempre filtrando type = 'REA')
# ---------------------------------------------------------------------------

# Descobre as cartas REA próximas a um ponto (origem/destino).
SQL_DISCOVER_CHARTS = f"""
SELECT DISTINCT w.chart
FROM {WP_TABLE} w
WHERE w.type = 'REA'                                   -- filtro REA (ignora IFR)
  AND ST_DWithin(
        {GEOM_EXPR('w.geom')}::geography,
        ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326)::geography,
        %(radius_m)s
      );
"""

# Waypoints REA das cartas selecionadas.
# O LEFT JOIN com a TMA da carta (camadas unidas) traz, por waypoint, a distância
# à borda (NM) e se está dentro — base do score de fronteira (PONTO 3). Cartas sem
# TMA em airspaces devolvem NULL (score neutro; comportamento antigo preservado).
SQL_WAYPOINTS_BY_CHARTS = f"""
WITH tma AS (
    SELECT 'REA ' || regexp_replace(a.name, '\\s+\\d+$', '') AS chart,
           ST_Union({GEOM_EXPR('a.geom')})                   AS geom
    FROM {AIRSPACE_TABLE} a
    WHERE a.type = 'tma'
    GROUP BY 1
)
SELECT w.id,
       w.name,
       w.chart,
       ST_X({GEOM_EXPR('w.geom')}) AS lon,
       ST_Y({GEOM_EXPR('w.geom')}) AS lat,
       CASE WHEN t.geom IS NULL THEN NULL
            ELSE ST_Distance({GEOM_EXPR('w.geom')}::geography,
                             ST_Boundary(t.geom)::geography) / 1852.0
       END                                                   AS dist_border_nm,
       (t.geom IS NOT NULL AND ST_Contains(t.geom, {GEOM_EXPR('w.geom')})) AS inside
FROM {WP_TABLE} w
LEFT JOIN tma t ON t.chart = w.chart
WHERE w.type = 'REA'                                   -- filtro REA
  AND w.chart = ANY(%(charts)s);
"""

# Conexões (corredores) REA das cartas selecionadas.
# Peso = comprimento real do corredor; fallback = reta geodésica entre nós.
SQL_CONNECTIONS_BY_CHARTS = f"""
SELECT c.id,
       c.source_id,
       c.target_id,
       c.name                       AS corridor,
       c.is_mandatory,
       c.lower_limit,
       c.higher_limit,
       c.heading,
       c.class                      AS airspace_class,
       ST_AsGeoJSON({GEOM_EXPR('c.geom')}) AS geom_json,             -- traçado real do corredor
       COALESCE(
           ST_Length({GEOM_EXPR('c.geom')}::geography),                 -- corredor real (curvas)
           ST_DistanceSphere({GEOM_EXPR('o.geom')}, {GEOM_EXPR('d.geom')})  -- fallback: reta entre nós
       )                            AS weight_m
FROM {CONN_TABLE} c
JOIN {WP_TABLE} o ON o.id = c.source_id
JOIN {WP_TABLE} d ON d.id = c.target_id
JOIN {WP_TABLE} w ON w.id IN (c.source_id, c.target_id)
WHERE c.type = 'REA'                                   -- filtro REA na conexão
  AND w.type = 'REA'                                   -- filtro REA no waypoint
  AND w.chart = ANY(%(charts)s)
GROUP BY c.id, c.source_id, c.target_id, c.name, c.is_mandatory,
         c.lower_limit, c.higher_limit, c.heading, c.class, c.geom,
         o.geom, d.geom;
"""

# Aeródromo direto de published.adhps — sem JOIN externo, geom é nativo.
SQL_AERODROME = f"""
SELECT a.designator_icao AS icao,
       a.designator_icao AS name,
       ST_X({GEOM_EXPR('a.geom')}) AS lon,
       ST_Y({GEOM_EXPR('a.geom')}) AS lat
FROM {ADHP_TABLE} a
WHERE a.designator_icao = %(code)s
LIMIT 1;
"""

# Lista de ICAOs (para autocomplete da CLI).
SQL_LIST_ICAOS = f"SELECT designator_icao FROM {ADHP_TABLE} ORDER BY designator_icao;"

# TAREFA_cartas_relevantes.md: espaços aéreos OFICIAIS (tma/ctr/atz, dado real
# em published.airspaces, não raio) que contêm um ponto — usado tanto para
# achar a "carta dona" de cada carta REA quanto para testar se uma ponta
# (origem/destino) cai dentro dela. type mais específico (ctr/atz) reflete o
# anel apertado de UM aeródromo; tma é a camada regional, quase sempre
# compartilhada por várias cidades (ex.: TMA de Brasília cobre Formosa/SWFR
# também) — por isso a prioridade ctr/atz > tma em _chart_home_designator.
SQL_POINT_AIRSPACE_DESIGNATORS = f"""
SELECT type, designator_icao
FROM {AIRSPACE_TABLE}
WHERE type IN ('ctr', 'atz', 'tma')
  AND ST_Contains({GEOM_EXPR('geom')},
                  ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326));
"""

# Para cada waypoint de UMA carta REA, em quais airspaces oficiais ele cai —
# base para decidir a "carta dona" (_chart_home_designator): o designator
# (normalizado, sem sufixo de setor _NN) que cobre a maior fatia dos
# waypoints da carta, dando prioridade a ctr/atz sobre tma.
SQL_CHART_WAYPOINT_AIRSPACES = f"""
SELECT w.id, a.type, a.designator_icao
FROM {WP_TABLE} w
JOIN {AIRSPACE_TABLE} a
  ON a.type IN ('ctr', 'atz', 'tma') AND ST_Contains({GEOM_EXPR('a.geom')}, {GEOM_EXPR('w.geom')})
WHERE w.type = 'REA' AND w.chart = %(chart)s;
"""

SQL_CHART_WAYPOINT_COUNT = f"""
SELECT count(*) FROM {WP_TABLE} WHERE type = 'REA' AND chart = %(chart)s;
"""


class PostgisLoader:
    """Loader do esquema published.

    Não há mais ``schema`` configurável nem ``aerodrome_coord_sql``: a coluna
    geom de adhps tornou o resolver externo desnecessário (ver resolver.py,
    agora depreciado). Os aeródromos são resolvidos aqui mesmo.
    """

    def __init__(self, conn: Any) -> None:
        self.conn = conn
        self._chart_home_cache: dict[str, Optional[str]] = {}

    def _rows(self, sql: str, params: dict) -> list[tuple]:
        try:
            with self.conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        except Exception:
            # Sem isto, uma query que falha deixa a transação abortada e TODA
            # query seguinte vira "current transaction is aborted", mascarando o
            # erro real. O rollback limpa a transação; o raise propaga o erro
            # ORIGINAL (ex.: "column ... does not exist"), que é o diagnosticável.
            self.conn.rollback()
            raise

    # ------------------------------------------------------------- aerodrome
    def fetch_aerodrome(self, icao_code: str) -> Node:
        """Resolve um aeródromo lendo ST_X/ST_Y direto de published.adhps.geom.

        Substitui toda a antiga lógica de aerodrome_coord_sql / JOIN externo.
        """
        rows = self._rows(SQL_AERODROME, {"code": icao_code})
        if not rows:
            raise LookupError(f"Aeródromo '{icao_code}' não encontrado em {ADHP_TABLE}.")
        code, name, lon, lat = rows[0]
        if lon is None or lat is None:
            raise LookupError(
                f"'{icao_code}' existe em {ADHP_TABLE} mas sem coordenada (geom nula)."
            )
        return Node(id=f"ADHP:{code}", name=name or code,
                    pos=LonLat(lon, lat), kind="aerodrome")

    def list_icaos(self) -> list[str]:
        return [r[0] for r in self._rows(SQL_LIST_ICAOS, {})]

    # ----------------------------------------------------------------- charts
    def discover_charts(self, points: Iterable[LonLat],
                        radius_nm: float = 60.0) -> list[str]:
        charts: set[str] = set()
        for p in points:
            rows = self._rows(SQL_DISCOVER_CHARTS,
                              {"lon": p.lon, "lat": p.lat,
                               "radius_m": radius_nm * 1852.0})
            charts.update(r[0] for r in rows)
        return sorted(charts)

    # --------------------------------------------------------- cartas relevantes
    @staticmethod
    def _normalize_designator(designator: str) -> str:
        """Tira o sufixo de setor (`SBWH_01` -> `SBWH`, `SBAN_2` -> `SBAN`) —
        setores de uma mesma TMA/CTR contam como o mesmo "dono"."""
        return re.sub(r"_\d+$", "", designator)

    def _point_airspace_designators(self, pos: LonLat) -> set[str]:
        """Designators (ctr/atz/tma, normalizados) de airspaces OFICIAIS que
        contêm `pos` — dado real (`ST_Contains`), não raio."""
        rows = self._rows(SQL_POINT_AIRSPACE_DESIGNATORS, {"lon": pos.lon, "lat": pos.lat})
        return {self._normalize_designator(d) for _typ, d in rows}

    def _chart_home_designator(self, chart: str) -> tuple[Optional[str], bool]:
        """"Carta dona" de uma carta REA: o designator (ctr/atz > tma) cuja
        área cobre a maior fatia dos waypoints da própria carta. Cacheado por
        instância (dado estável dentro de uma execução).

        Retorna (designator, ambíguo). `ambíguo=True` quando nenhum tipo
        atinge RELEVANCE_COVERAGE_THRESHOLD — ainda assim devolve o melhor
        disponível (nunca fica sem dono); ver TAREFA_cartas_relevantes.md.
        """
        if chart in self._chart_home_cache:
            return self._chart_home_cache[chart]

        total = self._rows(SQL_CHART_WAYPOINT_COUNT, {"chart": chart})[0][0]
        home: Optional[str] = None
        ambiguous = True
        if total:
            by_type: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
            for wid, typ, designator in self._rows(SQL_CHART_WAYPOINT_AIRSPACES, {"chart": chart}):
                by_type[typ][self._normalize_designator(designator)].add(wid)

            for typ in ("ctr", "atz", "tma"):
                candidates = sorted(by_type.get(typ, {}).items(),
                                    key=lambda kv: -len(kv[1]))
                for designator, ids in candidates:
                    if len(ids) / total >= RELEVANCE_COVERAGE_THRESHOLD:
                        home, ambiguous = designator, False
                        break
                if home is not None:
                    break

            if home is None:
                best = None
                for typ_designators in by_type.values():
                    for designator, ids in typ_designators.items():
                        frac = len(ids) / total
                        if best is None or frac > best[1]:
                            best = (designator, frac)
                if best is not None:
                    home = best[0]

        result = (home, ambiguous)
        self._chart_home_cache[chart] = result
        return result

    def relevant_charts(self, candidate_charts: list[str], origin_icao: str, dest_icao: str,
                        origin_pos: LonLat, dest_pos: LonLat) -> tuple[list[str], dict]:
        """Das cartas candidatas (raio, `discover_charts`), mantém só as que
        pertencem de fato a origem OU destino — carta dona (por airspace
        oficial) contém a ponta. Sem isso, uma carta só "de passagem" (não é
        de nenhuma das pontas) nunca entra no grafo: não atraca (Família 2)
        nem vira obrigatória via owes (Família 1) — ela nem chega a existir
        pro Dijkstra, sem precisar tocar em dijkstra.py.

        Sempre inclui a carta documentada em portoes_rea.json (`carta_de`)
        pra cada ponta, quando existir — salvaguarda pros 52 aeródromos com
        portão publicado: a carta deles nunca some por causa do critério
        geométrico.
        """
        origin_desigs = self._point_airspace_designators(origin_pos)
        dest_desigs = self._point_airspace_designators(dest_pos)
        relevant: set[str] = set()
        diag: dict = {}
        for chart in candidate_charts:
            home, ambiguous = self._chart_home_designator(chart)
            hit_origin = home is not None and home in origin_desigs
            hit_dest = home is not None and home in dest_desigs
            is_relevant = hit_origin or hit_dest
            diag[chart] = {"home": home, "ambiguous": ambiguous,
                          "hit_origin": hit_origin, "hit_dest": hit_dest}
            if is_relevant:
                relevant.add(chart)
        for icao in (origin_icao, dest_icao):
            forced = carta_de(icao)
            if forced:
                relevant.add(forced)
                diag.setdefault(forced, {}).setdefault("forced_by_portao", set()).add(icao)
        return sorted(relevant), diag

    # ------------------------------------------------------------------ graph
    def build_subgraph(self, origin_icao: str, dest_icao: str,
                       chart_radius_nm: float = 60.0,
                       link_radius_nm: float = 30.0,
                       pista_origem: Optional[str] = None,
                       pista_destino: Optional[str] = None) -> tuple[RouteGraph, dict]:
        # TAREFA_pista_obrigatoria_e_vento_default.md: checagem OFFLINE (só o
        # JSON de portões, sem tocar no banco) antes de qualquer query — se a
        # direção só tem regra condicionada a cabeceira e a pista não veio,
        # o motor recusa a rota com um retorno estruturado (nunca calcula com
        # portão "meio aplicado", nunca cai silenciosamente no mínimo-local).
        faltando: list = []
        aerodromos: dict = {}
        if direcao_exige_pista(origin_icao, "partida") and pista_origem is None:
            faltando.append("pista_origem"); aerodromos["pista_origem"] = origin_icao
        if direcao_exige_pista(dest_icao, "destino") and pista_destino is None:
            faltando.append("pista_destino"); aerodromos["pista_destino"] = dest_icao
        if faltando:
            raise PistaObrigatoriaError(faltando, aerodromos)

        # link_radius_nm é mantido por compatibilidade da assinatura, mas o
        # modelo de arestas sintéticas agora decide entrada/saída por "está em
        # TMA REA?" (k-vizinhos) em vez de um raio fixo — ver add_synthetic_edges.
        g = RouteGraph()

        # Aeródromos resolvidos diretamente do banco (geom nativo).
        origin = self.fetch_aerodrome(origin_icao)
        dest = self.fetch_aerodrome(dest_icao)
        g.add_node(origin)
        g.add_node(dest)

        charts = self.discover_charts([origin.pos, dest.pos], chart_radius_nm)

        # TAREFA_cartas_relevantes.md: das cartas candidatas (raio), só carrega
        # no grafo as que pertencem de fato à origem OU ao destino (airspace
        # oficial, não raio) — uma carta só "de passagem" (ex.: REA Brasília
        # na rota SNMH->SBVT) nunca entra; não atraca nem vira obrigatória.
        relevant, relevance_diag = self.relevant_charts(
            charts, origin_icao, dest_icao, origin.pos, dest.pos)

        if relevant:
            for _id, name, chart, lon, lat, dist_border_nm, inside in self._rows(
                SQL_WAYPOINTS_BY_CHARTS, {"charts": relevant}
            ):
                bs = border_score(
                    float(dist_border_nm) if dist_border_nm is not None else None,
                    bool(inside) if inside is not None else None)
                g.add_node(Node(id=_id, name=name, pos=LonLat(lon, lat),
                                kind="waypoint", chart=chart, border_score=bs))

            for (_id, src, tgt, corridor, mandatory, lo, hi,
                 heading, cls, geom_json, w) in self._rows(
                SQL_CONNECTIONS_BY_CHARTS, {"charts": relevant}
            ):
                if src in g.nodes and tgt in g.nodes:
                    # Edge recebe corridor (name), is_mandatory e o traçado real
                    # (geom) — usados pela classificação visual e pelo portão
                    # geométrico (anti-cruzamento) em add_synthetic_edges.
                    geom = _parse_linestring(geom_json)
                    g.add_edge(Edge(src, tgt, float(w), corridor=corridor,
                                    connection_id=_id,
                                    is_mandatory=bool(mandatory),
                                    lower_limit=lo, higher_limit=hi,
                                    heading=(float(heading) if heading is not None else None),
                                    geom=geom))

        # TAREFA_portoes.md PASSO B: portão obrigatório de entrada/saída do
        # documento (data/portoes_rea.json), quando existir pra este ICAO —
        # resolvido AQUI (não em graphmodel.py, que fica agnóstico ao
        # documento) porque só aqui se conhece o ICAO de cada ponta. None
        # quando o aeródromo não tem regra: comportamento inalterado.
        # `pista_origem`/`pista_destino` (TAREFA_pista.md) são opcionais e
        # só têm efeito nos poucos aeródromos com regra por cabeceira — sem
        # informar (ou informando uma sem regra), o resultado é o mesmo None
        # de antes.
        origin_forced = resolver_pontos_obrigatorios(g, origin_icao, "partida", dest_icao,
                                                      pista_origem)
        dest_forced = resolver_pontos_obrigatorios(g, dest_icao, "destino", origin_icao,
                                                    pista_destino)

        diag = g.add_synthetic_edges(origin.id, dest.id,
                                     origin_forced=origin_forced, dest_forced=dest_forced)

        if (origin_forced is not None or dest_forced is not None) \
                and not g._reaches(origin.id, dest.id):
            raise PortaoDesconectadoError(
                f"forçar portão obrigatório em {origin_icao}->{dest_icao} desconecta a "
                f"rota (sem caminho a partir do(s) ponto(s) obrigatório(s) do documento)"
            )

        meta = {"charts": charts, "relevant_charts": relevant,
                "charts_dropped": sorted(set(charts) - set(relevant)),
                "relevance_diag": relevance_diag,
                "origin_id": origin.id, "dest_id": dest.id,
                "synthetic_diagnostics": diag,
                "origin_gate_ids": set(origin_forced) if origin_forced else None,
                "dest_gate_ids": set(dest_forced) if dest_forced else None}
        return g, meta