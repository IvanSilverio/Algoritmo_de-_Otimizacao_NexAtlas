#!/usr/bin/env python3
"""Parser de `regras_rea_primeiros_ultimos_pontos.md` -> `data/portoes_rea.json`.

Lê o documento de portões obrigatórios de entrada/saída de aeródromo (por
carta REA) e gera um JSON estruturado, um objeto por ICAO:

    "SBBH": {
      "chart": "REA Belo Horizonte",
      "partida": [ {"pontos": ["CEASA", "TAQUARAÇU"], "pistas": ["13"]} ],
      "destino": [ {"pontos": ["CEASA"]} ]
    }

`partida`/`destino` são listas de regras; cada regra tem `pontos` e, quando
houver: `pistas`, `extra_se_outro_extremo` (pontos ADICIONAIS quando o outro
extremo da rota for aquele ICAO — aditivo, nunca substitui) e `so_com_rea`
(regra só vale quando a rota usa a malha REA).

Ver TAREFA_portoes.md (PASSO A) para o esquema completo e as 4 variações de
formato cobertas. Rode como script: escreve `data/portoes_rea.json` e imprime
um resumo (contagens + linhas fora do padrão, que devem ser zero).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DOC_PATH = Path(__file__).resolve().parent / "regras_rea_primeiros_ultimos_pontos.md"
OUT_PATH = Path(__file__).resolve().parent / "data" / "portoes_rea.json"

CONECTORES = {"de", "do", "da", "dos", "das", "e"}


def _titlecase_regiao(titulo: str) -> str:
    """'RIO DE JANEIRO' -> 'Rio de Janeiro' (str.title() dá 'Rio De Janeiro' —
    capitaliza conectores também). Só a 1ª palavra nunca vira conector."""
    palavras = titulo.strip().split()
    out = []
    for i, p in enumerate(palavras):
        pl = p.lower()
        out.append(pl if (i > 0 and pl in CONECTORES) else pl[:1].upper() + pl[1:])
    return " ".join(out)


REGIAO_RE = re.compile(r"^##\s*REGI[ÃA]O:\s*(.+?)\s*$")
AERODROMO_RE = re.compile(r"^###\s*([A-Z0-9]{4})\s*$")
HEADER_RE = re.compile(r"^\*\*(Partida|Destino)\s+([A-Z0-9]{4})(.*?):\*\*\s*(.*)$")
COND_OUTRO_RE = re.compile(r"^ com (destino|origem) ([A-Z0-9]{4})$")
PISTA_PREFIXO_RE = re.compile(r"^pistas?\s+", re.IGNORECASE)
PONTOS_RE = re.compile(r"\*\*(.+?)\*\*")
EXTRA_INLINE_RE = re.compile(
    r"\s*Se\s+(?:o\s+destino|a\s+origem)\s+for\s+\*\*([A-Z0-9]{4})\*\*,\s*"
    r"\*\*(.+?)\*\*\s*também pode ser utilizad[ao]\.?\s*$"
)
TEMPLATE_A_RE = {
    "Partida": re.compile(r"^Utilizar\s+(.+?)\s+como\s+primeiro ponto após partir do aeródromo\.\s*$"),
    "Destino": re.compile(r"^Utilizar\s+(.+?)\s+como\s+último ponto antes de chegar ao aeródromo\.\s*$"),
}
TEMPLATE_B_RE = {
    "Partida": re.compile(r"^Ingressar na REA pela posição\s+\*\*(.+?)\*\*\.\s*$"),
    "Destino": re.compile(r"^Abandonar a REA pela posição\s+\*\*(.+?)\*\*\s+antes de prosseguir para o aeródromo\.\s*$"),
}


def _parse_suffix(suffix: str, linha_num: int, linha_raw: str, nao_reconhecidas: list) -> dict:
    """Sufixo entre o ICAO e ':' no cabeçalho: '', ', pista(s) ...',
    ' com destino/origem ICAO2' ou ', quando a rota utilizar REA'."""
    info = {"pistas": None, "outro_tipo": None, "outro_icao": None, "so_com_rea": False}
    s = suffix.strip()
    if not s:
        return info
    if s.startswith(","):
        s2 = s.lstrip(",").strip()
        if s2.lower() == "quando a rota utilizar rea":
            info["so_com_rea"] = True
        elif PISTA_PREFIXO_RE.match(s2):
            rest = PISTA_PREFIXO_RE.sub("", s2).replace(" ou ", ",")
            info["pistas"] = [p.strip() for p in rest.split(",") if p.strip()]
        else:
            nao_reconhecidas.append((linha_num, linha_raw, f"sufixo de cabeçalho não reconhecido: {s!r}"))
    else:
        m = COND_OUTRO_RE.match(suffix)
        if m:
            info["outro_tipo"], info["outro_icao"] = m.group(1), m.group(2)
        else:
            nao_reconhecidas.append((linha_num, linha_raw, f"sufixo de cabeçalho não reconhecido: {suffix!r}"))
    return info


def parse_documento(texto: str) -> tuple[dict, list, list]:
    """Retorna (resultado_por_icao, linhas_nao_reconhecidas, notas)."""
    regras_brutas: list[dict] = []
    nao_reconhecidas: list = []
    notas: list = []
    icaos_vistos: dict[str, str] = {}  # icao -> chart

    chart_atual = None
    icao_atual = None

    for i, linha in enumerate(texto.split("\n"), start=1):
        s = linha.strip()
        if not s:
            continue

        m = REGIAO_RE.match(s)
        if m:
            chart_atual = "REA " + _titlecase_regiao(m.group(1))
            icao_atual = None
            continue

        m = AERODROMO_RE.match(s)
        if m:
            icao_atual = m.group(1)
            if chart_atual is None:
                nao_reconhecidas.append((i, s, f"aeródromo {icao_atual} fora de qualquer '## REGIÃO:'"))
            else:
                icaos_vistos[icao_atual] = chart_atual
            continue

        m = HEADER_RE.match(s)
        if not m:
            nao_reconhecidas.append((i, s, "linha fora do padrão geral (esperado '## REGIÃO', '### ICAO' ou '**Partida/Destino ...**')"))
            continue

        direcao_pt, icao_header, suffix, body = m.groups()
        if icao_atual is None or icao_header != icao_atual:
            nao_reconhecidas.append((i, s, f"cabeçalho de {icao_header} fora do bloco '### {icao_header}' esperado"))
            continue

        suf = _parse_suffix(suffix, i, s, nao_reconhecidas)

        extra_inline = None
        body_sem_extra = body
        me = EXTRA_INLINE_RE.search(body)
        if me:
            extra_inline = {me.group(1): [me.group(2)]}
            body_sem_extra = body[:me.start()].rstrip()

        pontos = None
        ta = TEMPLATE_A_RE[direcao_pt].match(body_sem_extra)
        if ta:
            pontos = PONTOS_RE.findall(ta.group(1))
        else:
            tb = TEMPLATE_B_RE[direcao_pt].match(body_sem_extra)
            if tb:
                pontos = [tb.group(1)]

        if pontos is None:
            nao_reconhecidas.append((i, s, "corpo da regra não bate com nenhum template conhecido (Utilizar ... / Ingressar-Abandonar ...)"))
            continue

        regras_brutas.append({
            "icao": icao_atual,
            "direcao": "partida" if direcao_pt == "Partida" else "destino",
            "pontos": pontos,
            "pistas": suf["pistas"],
            "outro_tipo": suf["outro_tipo"],
            "outro_icao": suf["outro_icao"],
            "so_com_rea": suf["so_com_rea"],
            "extra_inline": extra_inline,
            "linha": i,
        })

    # 2ª passada: agrupa por (icao, direcao); dobra as regras "com destino/
    # origem ICAO2" (condição no CABEÇALHO) na regra-base equivalente, como
    # extra_se_outro_extremo (aditivo) — ver TAREFA_portoes.md PASSO A.
    resultado: dict[str, dict] = {
        icao: {"chart": chart, "partida": [], "destino": []}
        for icao, chart in icaos_vistos.items()
    }

    from collections import defaultdict
    grupos: dict[tuple, list] = defaultdict(list)
    for r in regras_brutas:
        grupos[(r["icao"], r["direcao"])].append(r)

    for (icao, direcao), regras in grupos.items():
        base_regras = [r for r in regras if r["outro_icao"] is None]
        cond_regras = [r for r in regras if r["outro_icao"] is not None]

        saida = []
        for r in base_regras:
            extra: dict = {}
            if r["extra_inline"]:
                for k, v in r["extra_inline"].items():
                    extra.setdefault(k, []).extend(p for p in v if p not in extra.get(k, []))
            saida.append({
                "pontos": r["pontos"],
                "pistas": r["pistas"],
                "so_com_rea": r["so_com_rea"],
                "extra": extra,
            })

        for r in cond_regras:
            candidatos = [b for b in saida if b["pistas"] == r["pistas"]]
            if len(candidatos) == 1 and set(candidatos[0]["pontos"]) <= set(r["pontos"]):
                base = candidatos[0]
                delta = [p for p in r["pontos"] if p not in base["pontos"]]
                base["extra"].setdefault(r["outro_icao"], [])
                base["extra"][r["outro_icao"]].extend(p for p in delta if p not in base["extra"][r["outro_icao"]])
                notas.append(
                    f"{icao}/{direcao} (linha {r['linha']}): regra 'com {r['outro_tipo']} {r['outro_icao']}' "
                    f"dobrada em extra_se_outro_extremo={{'{r['outro_icao']}': {delta}}} sobre a regra-base {base['pontos']}"
                )
            else:
                saida.append({
                    "pontos": r["pontos"],
                    "pistas": r["pistas"],
                    "so_com_rea": r["so_com_rea"],
                    "extra": {},
                })
                notas.append(
                    f"{icao}/{direcao} (linha {r['linha']}): regra 'com {r['outro_tipo']} {r['outro_icao']}' "
                    f"NÃO reduzida a extra (base compatível não encontrada de forma única) — "
                    f"mantida como regra independente; revisar manualmente."
                )

        saida_final = []
        for item in saida:
            final = {"pontos": item["pontos"]}
            if item["pistas"]:
                final["pistas"] = item["pistas"]
            if item["so_com_rea"]:
                final["so_com_rea"] = True
            if item["extra"]:
                final["extra_se_outro_extremo"] = item["extra"]
            saida_final.append(final)
        resultado[icao][direcao] = saida_final

    return resultado, nao_reconhecidas, notas


def main() -> int:
    texto = DOC_PATH.read_text(encoding="utf-8")
    resultado, nao_reconhecidas, notas = parse_documento(texto)
    resultado_ordenado = dict(sorted(resultado.items()))

    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(resultado_ordenado, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    n_partida = sum(len(v["partida"]) for v in resultado.values())
    n_destino = sum(len(v["destino"]) for v in resultado.values())
    print(f"Aeródromos: {len(resultado)}")
    print(f"Regras de partida: {n_partida}   Regras de destino: {n_destino}")
    print(f"JSON escrito em: {OUT_PATH}")
    print()
    print(f"Notas (regras condicionais dobradas em extra_se_outro_extremo): {len(notas)}")
    for n in notas:
        print(f"  - {n}")
    print()
    print(f"Linhas NÃO reconhecidas: {len(nao_reconhecidas)}")
    for linha_num, raw, motivo in nao_reconhecidas:
        print(f"  linha {linha_num}: {motivo}\n    {raw!r}")

    return 1 if nao_reconhecidas else 0


if __name__ == "__main__":
    sys.exit(main())
