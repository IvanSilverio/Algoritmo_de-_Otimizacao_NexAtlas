"""Portões obrigatórios de entrada/saída de aeródromo (TAREFA_portoes.md).

Carrega `data/portoes_rea.json` (gerado por `parse_portoes.py` a partir de
`regras_rea_primeiros_ultimos_pontos.md`) e resolve os pontos do documento
para nós da malha REA já carregada no grafo — por NOME + CARTA (há 10 nomes
homônimos entre cartas; sem a carta a resolução é ambígua).

Decisão do Ivan (o documento é a fonte da verdade): se um ponto não resolve
para EXATAMENTE 1 nó, ou se forçar o portão desconecta a rota, FALHA — nunca
relaxa para o k-mais-próximos nem inventa o ponto.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "portoes_rea.json"

_cache: Optional[dict] = None


class PortaoError(Exception):
    """Erro ao aplicar um portão obrigatório de aeródromo (TAREFA_portoes.md)."""


class PortaoResolucaoError(PortaoError):
    """Um ponto do documento não resolveu para exatamente 1 nó da malha."""


class PortaoDesconectadoError(PortaoError):
    """Forçar o portão obrigatório deixou a rota sem caminho origem->destino."""


def _carregar() -> dict:
    global _cache
    if _cache is None:
        if _DATA_PATH.exists():
            with open(_DATA_PATH, encoding="utf-8") as f:
                _cache = json.load(f)
        else:
            _cache = {}
    return _cache


def carta_de(icao: str) -> Optional[str]:
    """Carta REA do aeródromo conforme o documento (None se sem regra)."""
    entry = _carregar().get(icao)
    return entry["chart"] if entry else None


def pontos_obrigatorios(icao: str, direcao: str,
                        outro_extremo_icao: Optional[str]) -> Optional[list]:
    """Nomes dos pontos válidos de `direcao` ('partida'/'destino') para
    `icao`: união de todas as regras dessa direção (cobre "X ou Y" e também o
    caso de várias pistas — PISTAS é pendência, ver TAREFA_portoes.md: sem o
    dado de pista em uso, usamos a união dos pontos de todas as pistas como
    válidos) mais os pontos ADICIONAIS de `extra_se_outro_extremo` quando o
    outro extremo da rota bate. None se o aeródromo não tem regra nessa
    direção — comportamento inalterado (cai no mecanismo geral de
    mínimo-local/coerência)."""
    entry = _carregar().get(icao)
    if not entry:
        return None
    regras = entry.get(direcao) or []
    if not regras:
        return None
    pontos: list = []
    for r in regras:
        for p in r["pontos"]:
            if p not in pontos:
                pontos.append(p)
        extra = r.get("extra_se_outro_extremo") or {}
        if outro_extremo_icao and outro_extremo_icao in extra:
            for p in extra[outro_extremo_icao]:
                if p not in pontos:
                    pontos.append(p)
    return pontos


# Achado na integração ao vivo (17/08): o banco às vezes cadastra o portão
# com um qualificador que o documento não usa — "CEASA" no documento pode ser
# "CEASA (PORTÃO)" no banco (43 dos 126 pontos do documento caem nisso; 9
# precisaram de correção do próprio documento — grafia divergente do banco,
# ver histórico do commit). Resolução em NÍVEIS, cada um só tentado se o
# anterior não achar candidato puro:
#   1) nome exato (sempre vence se existir — ver CAÇAPAVA/SBSJ, que tem
#      "CAÇAPAVA" E "CAÇAPAVA (PORTÃO)" coexistindo e a pura é a certa);
#   2) qualificador "PORTÃO" — sufixo OU prefixo (o banco usa as duas formas;
#      ver MARAPENDI/SBJR, cadastrado como "PORTÃO MARAPENDI");
#   3) sufixo "(REA)" — só se PORTÃO não resolveu.
# "PORTÃO" tem prioridade sobre "(REA)" (decisão do Ivan 17/08, caso
# IGARATÁ/SBSJ, que tem AS DUAS formas cadastradas): "(REA)" no banco não
# significa "é portão" — é usado também em pontos que a própria V1 já
# identificou como NÃO-portão (ex.: FLORES (REA)/MANNESMANN (REA) em Belo
# Horizonte, corredor que afasta do destino), enquanto "(PORTÃO)" é literal.
# Ivan vai levar a inconsistência de cadastro como observação pro Vinícius/
# Cristiano — não é definitivo, só a melhor leitura disponível dos dados.
_NIVEIS_FALLBACK = (
    lambda nome: (f"{nome} (PORTÃO)", f"PORTÃO {nome}"),
    lambda nome: (f"{nome} (REA)",),
)


def _candidatos_exatos(graph, nome: str, chart: str) -> list:
    alvo = nome.strip().upper()
    return [nid for nid, n in graph.nodes.items()
            if n.kind == "waypoint" and n.chart == chart
            and n.name.strip().upper() == alvo]


def resolver_pontos_obrigatorios(graph, icao: str, direcao: str,
                                 outro_extremo_icao: Optional[str]) -> Optional[list]:
    """Resolve pontos_obrigatorios() para IDs de nó do `graph` (waypoint com
    nome e carta batendo). None se o aeródromo não tem regra pra essa direção.
    Levanta PortaoResolucaoError se algum ponto não resolver para exatamente
    1 nó (comparação de nome sem diferenciar caixa — documento e banco usam
    maiúsculas —, com fallback de qualificador "PORTÃO"/"(REA)" em níveis,
    ver _NIVEIS_FALLBACK)."""
    nomes = pontos_obrigatorios(icao, direcao, outro_extremo_icao)
    if nomes is None:
        return None
    chart = carta_de(icao)
    ids: list = []
    for nome in nomes:
        candidatos = _candidatos_exatos(graph, nome, chart)
        for formas in _NIVEIS_FALLBACK:
            if candidatos:
                break
            for forma in formas(nome):
                candidatos += _candidatos_exatos(graph, forma, chart)
        if len(candidatos) != 1:
            raise PortaoResolucaoError(
                f"portão {nome} de {icao} ({direcao}): esperado 1 nó em "
                f"'{chart}', encontrados {len(candidatos)}"
            )
        ids.append(candidatos[0])
    return ids
