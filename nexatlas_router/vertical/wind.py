"""wind.py — leitor do dataset de VENTO (JetStreamDataTile) para a V3.

Mesmo formato do terrain.py: metadata + tiles FlatBuffer, Web Mercator, z=2.
Reaproveita o mini-decoder de lá (`_Table`/`_root`/`_http_get`/`_DTYPE`/`_META`
— mesmo schema "JetStreamDataTile" usado pelo terreno, ver VENTO_CDN_REFERENCIA.md).
Diferenças do vento: base `wind_fb`, 2 canais (u=leste, v=norte, m/s), 12
níveis de altitude (não igualmente espaçados) e vários timestamps (previsão
de 3 em 3 horas).

Ao contrário do `Terrain`, o construtor NUNCA lança: se o CDN falhar, a
instância fica "indisponível" e `vento_em()` devolve (0,0) — o perfil vertical
nunca quebra por causa do vento (ver TAREFA_vento.md).
"""
from __future__ import annotations

import datetime as dt
import math
import struct
from dataclasses import dataclass
from typing import Optional

from .terrain import _Table, _root, _http_get, _DTYPE, _META

BASE_URL = "https://jetstream-data-cdn.nexatlas.com/bra/wind_fb"
KT_PER_MS = 1.94384


@dataclass
class WindMeta:
    zooms: list
    levels: list           # altitudes disponíveis (ft) — NÃO igualmente espaçadas
    timestamps: list       # unix UTC, previsão de 3 em 3h (~5 dias à frente)
    width: int
    height: int
    channels: int
    data_type: str
    fixed_point: int        # ver Wind.scale


class Wind:
    def __init__(self, base_url: str = BASE_URL):
        self.base = base_url
        self._tiles: dict[tuple, object] = {}
        self.falhas = 0                        # nº de vento_em() que caíram p/ (0,0)
        self.meta: Optional[WindMeta] = None
        self.erro: Optional[str] = None
        try:
            raw = _http_get(f"{self.base}/metadata.fb")
            m = _root(raw)
            self.meta = WindMeta(
                zooms=m.vector_u8(_META["zooms"]),
                levels=m.vector_f32(_META["altitudes"]),
                timestamps=m.vector_i64(_META["timestamps"]),
                width=m.scalar_u16(_META["width"]),
                height=m.scalar_u16(_META["height"]),
                channels=m.scalar_u8(_META["channel_count"]),
                data_type=m.string(_META["data_type"]),
                fixed_point=m.scalar_u8(_META["fixed_point_precision"]),
            )
            self.z = max(self.meta.zooms)
            # ESCALA = 10**fixed_point_precision (slot 10 do MetaData — ver
            # JetStreamDataTile.fbs e o mapeamento _META em terrain.py). Pro
            # vento dá 10**2=100 — confirmado numa sondagem ao vivo (~30 kt a
            # 18.200 ft sobre SP; física plausível pra aquele nível; /10 daria
            # ~300 kt e /1000 daria ~3 kt, nenhum dos dois plausível).
            self.scale = 10 ** self.meta.fixed_point
        except Exception as e:
            self.erro = str(e)

    def disponivel(self) -> bool:
        return self.meta is not None

    # ---- Web Mercator (idêntico ao terrain.py) ----
    def _tile_pixel(self, lon, lat):
        n = 1 << self.z
        x = ((lon + 180.0) / 360.0) * n
        lat_r = math.radians(lat)
        merc_y = math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi
        y = ((1.0 - merc_y) / 2.0) * n
        tx, ty = int(math.floor(x)), int(math.floor(y))
        px = int((x - tx) * self.meta.width)
        py = int((y - ty) * self.meta.height)
        return tx, ty, px, py

    @staticmethod
    def _nearest(value: float, options: list):
        return min(options, key=lambda o: abs(o - value))

    def _tile_samples(self, ts, level, tx, ty):
        key = (ts, level, tx, ty)
        cached = self._tiles.get(key)
        if cached is not None:
            return cached
        url = f"{self.base}/{int(ts)}/{int(level)}/{self.z}/{tx}/{ty}.fb"
        raw = _http_get(url)
        data, _ = _root(raw).vector_bytes(0)   # Tile.data => slot 0
        fmt, size = _DTYPE[self.meta.data_type]
        arr = struct.unpack(f"<{len(data)//size}{fmt}", data[: (len(data)//size)*size])
        self._tiles[key] = arr
        return arr

    def vento_em(self, lon: float, lat: float, altitude_ft: float,
                hora_unix: float) -> tuple[float, float]:
        """(u_kt, v_kt) no ponto/altitude/hora mais próximos disponíveis.

        Nunca lança: devolve (0.0, 0.0) se o CDN estiver indisponível ou o
        tile/pixel falhar, incrementando `falhas` para o chamador sinalizar
        UM aviso agregado (em vez de um aviso por trecho do perfil)."""
        if not self.disponivel():
            self.falhas += 1
            return 0.0, 0.0
        try:
            level = self._nearest(altitude_ft, self.meta.levels)
            ts = self._nearest(hora_unix, self.meta.timestamps)
            tx, ty, px, py = self._tile_pixel(lon, lat)
            arr = self._tile_samples(ts, level, tx, ty)
            W, C = self.meta.width, self.meta.channels
            idx = (py * W + px) * C
            u_ms = arr[idx] / self.scale
            v_ms = arr[idx + 1] / self.scale
            return u_ms * KT_PER_MS, v_ms * KT_PER_MS
        except Exception:
            self.falhas += 1
            return 0.0, 0.0


def ground_speed(rumo_perna_deg: float, tas_kt: float, u_kt: float,
                 v_kt: float) -> tuple[float, float, float]:
    """Triângulo do vento (TAREFA_vento.md §2).

    `(u,v)` é a velocidade do ar (leste, norte); `rumo_perna_deg` é o rumo
    VERDADEIRO da perna (u/v são referenciados ao norte verdadeiro). Devolve
    `(groundspeed_kt, componente_cauda(+)/proa(-)_kt, ângulo_de_deriva_graus)`.
    """
    r = math.radians(rumo_perna_deg)
    w_par = u_kt * math.sin(r) + v_kt * math.cos(r)      # cauda (+) / proa (-)
    w_perp = u_kt * math.cos(r) - v_kt * math.sin(r)     # través
    if tas_kt <= 0:
        return 0.0, w_par, 0.0
    gs = w_par + math.sqrt(max(0.0, tas_kt * tas_kt - w_perp * w_perp))
    deriva = math.degrees(math.asin(max(-1.0, min(1.0, w_perp / tas_kt))))
    return gs, w_par, deriva


_FORMATOS_DATA_HORA = ["%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"]
_FORMATOS_SO_HORA = ["%H:%M:%S", "%H:%M"]

EXEMPLOS_HORA = ("'15/08/2026 14:30', '15/08/2026' (00:00), "
                 "'14:30' (hoje, UTC), '2026-08-15T14:30:00', ou unix (1786363200)")


def parse_hora_utc(valor) -> float:
    """Converte a hora de partida informada (input do usuário) para unix UTC
    (segundos). SEMPRE UTC, sem conversão de fuso — quem digita já pensa em
    UTC. Aceita, em ordem de tentativa:
      1. número, ou string numérica (unix, em segundos) — ex.: 1786363200
      2. data no formato BR — "15/08/2026 14:30", "15/08/2026 14:30:00" ou
         só "15/08/2026" (sem hora = 00:00 UTC daquele dia)
      3. só hora — "14:30" ou "14:30:00" — assume o dia de HOJE em UTC
      4. ISO-8601 — "2026-08-15T14:30:00" ou com "Z"
    Levanta ValueError (com os exemplos acima na mensagem) se `valor` for
    vazio/None ou não casar com nenhum desses formatos."""
    if valor is None:
        raise ValueError("hora de partida vazia")
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip()
    if not s:
        raise ValueError("hora de partida vazia")

    try:
        return float(s)                          # unix como string
    except ValueError:
        pass

    for fmt in _FORMATOS_DATA_HORA:
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            continue

    hoje = dt.datetime.now(dt.timezone.utc).date()
    for fmt in _FORMATOS_SO_HORA:
        try:
            hora = dt.datetime.strptime(s, fmt).time()
            return dt.datetime.combine(hoje, hora, tzinfo=dt.timezone.utc).timestamp()
        except ValueError:
            continue

    try:
        iso = s[:-1] + "+00:00" if s.endswith("Z") else s
        d = dt.datetime.fromisoformat(iso)
    except ValueError:
        raise ValueError(f"hora de partida não reconhecida: {valor!r}. "
                          f"Exemplos aceitos: {EXEMPLOS_HORA}.")
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.timestamp()
