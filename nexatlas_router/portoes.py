"""Portões obrigatórios de entrada/saída de aeródromo (TAREFA_portoes.md).

Carrega `data/portoes_rea.json` (gerado por `parse_portoes.py` a partir de
`regras_rea_primeiros_ultimos_pontos.md`) e resolve os pontos do documento
para nós da malha REA já carregada no grafo — por NOME + CARTA (há 10 nomes
homônimos entre cartas; sem a carta a resolução é ambígua).

Decisão do Ivan (o documento é a fonte da verdade): se um ponto não resolve
para EXATAMENTE 1 nó, ou se forçar o portão desconecta a rota, FALHA — nunca
relaxa para o k-mais-próximos nem inventa o ponto.

Pista/cabeceira (TAREFA_pista.md, 20/08/26): em poucos aeródromos (SBBH,
SBNT, SBJR, SBRJ, SBJD, SBMT, SDCO) a regra depende da cabeceira em uso —
cada regra do JSON pode trazer `pistas` (lista).

TAREFA_pista_obrigatoria_e_vento_default.md (26/08/26) — decisão da reunião
com o Vinícius: pista deixou de ser sempre opcional. Quando uma DIREÇÃO
(partida/destino) só tem regra(s) condicionada(s) a cabeceira (nenhuma regra
"geral" cobre o caso — ver `direcao_exige_pista`), a pista vira OBRIGATÓRIA
pra essa direção: sem ela, `db.build_subgraph` levanta `PistaObrigatoriaError`
(retorno estruturado, nunca uma rota calculada com portão "meio aplicado").
Quando a pista informada NÃO casa nenhuma regra daquele aeródromo, ou quando
o aeródromo/direção não tem regra nenhuma condicionada a cabeceira, o
comportamento é o de sempre: nunca vira erro, nunca inventa união de
cabeceiras como fallback (ver `pontos_obrigatorios`). O motor espera a
string CANÔNICA da cabeceira, exatamente como está no JSON (ex.: "13",
"16L") — normalizar formas livres ("pista 13", "decolar da 13") é
responsabilidade de quem chama (orquestrador).
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


class PistaObrigatoriaError(PortaoError):
    """Direção com regra só condicionada a cabeceira, e a pista não foi
    informada (TAREFA_pista_obrigatoria_e_vento_default.md). Subclasse de
    `PortaoError` de propósito: os runners de bateria já classificam
    qualquer `PortaoError` como falha EXPLICADA (ERRO_PORTAO), não erro
    silencioso — ver CLAUDE.md §3/§4.

    `.to_dict()` é o retorno estruturado que o chamador (CLI, orquestrador,
    gabarito) deve usar em vez de propagar a exceção crua ao usuário final:
    `{"status": "faltou_input", "faltando": [...], "aerodromos": {...},
    "mensagem": "..."}`. `faltando` sempre na ordem ["pista_origem",
    "pista_destino"] quando os dois faltam.
    """

    _ACAO = {"pista_origem": "decolagem", "pista_destino": "pouso"}

    def __init__(self, faltando: list[str], aerodromos: dict[str, str]):
        self.faltando = list(faltando)
        self.aerodromos = dict(aerodromos)
        partes = [f"{aerodromos[campo]} tem regra de cabeceira; informe a "
                  f"pista de {self._ACAO[campo]}" for campo in faltando]
        self.mensagem = "; ".join(partes) + " para calcular a rota."
        super().__init__(self.mensagem)

    def to_dict(self) -> dict:
        return {"status": "faltou_input", "faltando": list(self.faltando),
                "aerodromos": dict(self.aerodromos), "mensagem": self.mensagem}


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


def _regra_aplicavel(r: dict, pista: Optional[str]) -> bool:
    """Uma regra vale se NÃO tem `pistas` (regra geral, sempre aplica) OU se
    tem `pistas` e a cabeceira informada está nessa lista (comparação sem
    diferenciar caixa/espaços). Sem `pista` informada, só passam as regras
    sem `pistas` — TAREFA_pista.md: nunca inventa fallback de cabeceira."""
    rp = r.get("pistas")
    if not rp:
        return True
    if pista is None:
        return False
    alvo = pista.strip().upper()
    return alvo in {p.strip().upper() for p in rp}


def pontos_obrigatorios(icao: str, direcao: str,
                        outro_extremo_icao: Optional[str],
                        pista: Optional[str] = None) -> Optional[list]:
    """Nomes dos pontos válidos de `direcao` ('partida'/'destino') para
    `icao`: união dos pontos das regras APLICÁVEIS (cobre "X ou Y") mais os
    pontos ADICIONAIS de `extra_se_outro_extremo` quando o outro extremo da
    rota bate. `pista` é a cabeceira informada pelo piloto para este extremo
    (opcional; ex.: "13") — ver `_regra_aplicavel` (TAREFA_pista.md).

    None se o aeródromo não tem regra nessa direção, OU se nenhuma regra é
    aplicável (aeródromo só tem regra por cabeceira e a informada não bate,
    ou não foi informada) — em ambos os casos cai no mecanismo geral de
    mínimo-local/coerência (comportamento inalterado, nunca é erro)."""
    entry = _carregar().get(icao)
    if not entry:
        return None
    regras = entry.get(direcao) or []
    aplicaveis = [r for r in regras if _regra_aplicavel(r, pista)]
    if not aplicaveis:
        return None
    pontos: list = []
    for r in aplicaveis:
        for p in r["pontos"]:
            if p not in pontos:
                pontos.append(p)
        extra = r.get("extra_se_outro_extremo") or {}
        if outro_extremo_icao and outro_extremo_icao in extra:
            for p in extra[outro_extremo_icao]:
                if p not in pontos:
                    pontos.append(p)
    return pontos


def direcao_exige_pista(icao: str, direcao: str) -> bool:
    """True se TODAS as regras de `icao`/`direcao` são condicionadas a
    cabeceira (`pistas`) — ou seja, nenhuma regra "geral" cobre essa direção,
    então sem `pista` informada `pontos_obrigatorios` cairia em None (nenhuma
    regra aplicável) e o motor rodaria sem NENHUM portão forçado nessa ponta.
    TAREFA_pista_obrigatoria_e_vento_default.md: nesse caso a pista deixa de
    ser opcional — `db.build_subgraph` deve recusar a rota (ver
    `PistaObrigatoriaError`) em vez de cair silenciosamente no mínimo-local.

    Só olha o JSON (sem banco) — checagem é por DIREÇÃO (partida OU destino),
    nunca pelo aeródromo como um todo: um aeródromo pode exigir pista só numa
    ponta (ex.: SBBH exige na partida, mas a regra de destino é geral)."""
    regras = (_carregar().get(icao) or {}).get(direcao) or []
    return bool(regras) and all(r.get("pistas") for r in regras)


def aerodromo_exige_pista(icao: str) -> bool:
    """True se `icao` tem alguma regra (partida OU destino) condicionada a
    cabeceira (`pistas`) — TAREFA_pista.md: o ORQUESTRADOR usa isso pra
    decidir se pergunta a cabeceira ao piloto. O algoritmo em si nunca
    pergunta nada e nunca falha por falta de pista; só responde essa
    consulta e aplica a pista se ela vier (ver `pontos_obrigatorios`)."""
    entry = _carregar().get(icao)
    if not entry:
        return False
    return any(r.get("pistas") for direcao in ("partida", "destino")
               for r in (entry.get(direcao) or []))


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
                                 outro_extremo_icao: Optional[str],
                                 pista: Optional[str] = None) -> Optional[list]:
    """Resolve pontos_obrigatorios() para IDs de nó do `graph` (waypoint com
    nome e carta batendo). None se o aeródromo não tem regra aplicável pra
    essa direção/pista (TAREFA_pista.md — cai no mecanismo geral, nunca é
    erro). Levanta PortaoResolucaoError se algum ponto não resolver para
    exatamente 1 nó (comparação de nome sem diferenciar caixa — documento e
    banco usam maiúsculas —, com fallback de qualificador "PORTÃO"/"(REA)"
    em níveis, ver _NIVEIS_FALLBACK)."""
    nomes = pontos_obrigatorios(icao, direcao, outro_extremo_icao, pista)
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
