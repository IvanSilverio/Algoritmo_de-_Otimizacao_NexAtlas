"""
terrain.py — leitor do dataset de TERRENO (JetStreamDataTile) para a V3.

NÃO altera o motor. É o módulo que a V3 usará para obter elevação do terreno
em qualquer (lon, lat), lendo os tiles do CDN online (pacote _fb).

Espelha exatamente a lógica do read-tiles.js (Web Mercator + fixed-point),
mas em Python e sobre HTTP, com cache de tiles em memória.

Decoder FlatBuffer mínimo (sem flatc): o formato é simples e determinístico.
- Metadata: lida pela ORDEM de declaração dos campos (ver ORDER abaixo).
  >>> Essa ordem é a única premissa a confirmar com o terrain_probe.js. <<<
- Tile: tem um único campo (data: [ubyte]) => slot 0, sem ambiguidade.

Uso:
    t = Terrain()                 # baixa metadata 1x
    elev = t.elevation(-43.95, -19.85)   # elevação no ponto (unidade do dataset)
    emax = t.max_along([(lon1,lat1),(lon2,lat2)], step_nm=0.5)  # pior caso num trecho
"""
from __future__ import annotations
import math
import struct
import urllib.request
from dataclasses import dataclass

BASE_URL = "https://jetstream-data-cdn.nexatlas.com/bra/terrain_fb"

# ---------------------------------------------------------------------------
# Mini-decoder FlatBuffer (little-endian)
# ---------------------------------------------------------------------------
def _u32(b, o): return struct.unpack_from("<I", b, o)[0]
def _i32(b, o): return struct.unpack_from("<i", b, o)[0]
def _u16(b, o): return struct.unpack_from("<H", b, o)[0]

class _Table:
    """Acesso genérico a uma tabela FlatBuffer via vtable."""
    def __init__(self, buf: bytes, pos: int):
        self.buf = buf
        self.pos = pos
        self.vtable = pos - _i32(buf, pos)  # soffset aponta pra trás

    def _field_pos(self, slot: int) -> int | None:
        vt_bytes = _u16(self.buf, self.vtable)
        off_pos = self.vtable + 4 + slot * 2
        if off_pos - self.vtable >= vt_bytes:
            return None
        field_off = _u16(self.buf, off_pos)
        return self.pos + field_off if field_off != 0 else None

    def scalar_i32(self, slot, default=0):
        p = self._field_pos(slot)
        return _i32(self.buf, p) if p is not None else default

    def scalar_u16(self, slot, default=0):
        p = self._field_pos(slot)
        return _u16(self.buf, p) if p is not None else default

    def scalar_u8(self, slot, default=0):
        p = self._field_pos(slot)
        return self.buf[p] if p is not None else default

    def string(self, slot):
        p = self._field_pos(slot)
        if p is None:
            return None
        start = p + _u32(self.buf, p)
        n = _u32(self.buf, start)
        return self.buf[start + 4: start + 4 + n].decode("utf-8")

    def vector_pos_len(self, slot):
        p = self._field_pos(slot)
        if p is None:
            return None, 0
        start = p + _u32(self.buf, p)
        return start + 4, _u32(self.buf, start)

    def vector_i32(self, slot):
        start, n = self.vector_pos_len(slot)
        return [_i32(self.buf, start + 4 * i) for i in range(n)] if start else []

    def vector_u8(self, slot):
        start, n = self.vector_pos_len(slot)
        return list(self.buf[start: start + n]) if start else []

    def vector_f32(self, slot):
        start, n = self.vector_pos_len(slot)
        return [struct.unpack_from("<f", self.buf, start + 4 * i)[0] for i in range(n)] if start else []

    def vector_i64(self, slot):
        start, n = self.vector_pos_len(slot)
        return [struct.unpack_from("<q", self.buf, start + 8 * i)[0] for i in range(n)] if start else []

    def vector_bytes(self, slot):
        start, n = self.vector_pos_len(slot)
        return (self.buf[start: start + n], n) if start else (b"", 0)


def _root(buf: bytes) -> _Table:
    return _Table(buf, _u32(buf, 0))


# Mapeamento dos slots do MetaData, pela ORDEM DE DECLARAÇÃO em
# JetStreamDataTile.fbs (schema oficial do formato — mesma MetaData para
# terreno, vento etc.):
#   slot0  version (u8)              slot1  type (str, "fb")
#   slot2  id (str)                  slot3  zooms ([u8]!)
#   slot4  width (u16)               slot5  height (u16)
#   slot6  channel_count (u8)        slot7  altitudes ([float]!)
#   slot8  timestamps ([uint64])     slot9  data_type (str)
#   slot10 fixed_point_precision (u8)
# (Um mapeamento anterior, decodificado por tentativa sem o .fbs, tinha
# slot0/slot10 trocados e lia zooms/altitudes com a largura de elemento
# errada — inofensivo até agora só porque terreno/vento têm 1 zoom só e o
# terreno 1 altitude só, ambos == 0 em qualquer largura de leitura.)
_META = dict(
    version=0, type=1, id=2, zooms=3, width=4,
    height=5, channel_count=6, altitudes=7, timestamps=8, data_type=9,
    fixed_point_precision=10,
)

_DTYPE = {"int8": ("b", 1), "int16": ("h", 2), "int32": ("i", 4)}


@dataclass
class TerrainMeta:
    zooms: list
    levels: list
    timestamps: list
    width: int
    height: int
    channels: int
    data_type: str
    fixed_point: int
    type: str


def _http_get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


class Terrain:
    def __init__(self, base_url: str = BASE_URL, clearance_ft: float = 1000.0,
                 elevation_in_meters: bool = True):
        """
        elevation_in_meters: CONFIRMADO True — o terreno vem em METROS
            (int16 + fixed_point=1; em pés os picos brasileiros estourariam o
            int16). A saída de elevation() é convertida para PÉS.
        clearance_ft: margem de terreno (piso de cruzeiro = terreno + margem).
        """
        self.base = base_url
        self.clearance_ft = clearance_ft
        self.to_ft = 3.28084 if elevation_in_meters else 1.0
        self._tiles: dict[tuple, object] = {}
        raw = _http_get(f"{self.base}/metadata.fb")
        m = _root(raw)
        self.meta = TerrainMeta(
            zooms=m.vector_u8(_META["zooms"]),
            levels=m.vector_f32(_META["altitudes"]),
            timestamps=m.vector_i64(_META["timestamps"]),
            width=m.scalar_u16(_META["width"]),
            height=m.scalar_u16(_META["height"]),
            channels=m.scalar_u8(_META["channel_count"]),
            data_type=m.string(_META["data_type"]),
            fixed_point=m.scalar_u8(_META["fixed_point_precision"]),
            type=m.string(_META["type"]),
        )
        self.z = max(self.meta.zooms)
        self.level = max(self.meta.levels)
        self.ts = self.meta.timestamps[0]
        # ESCALA = 10**fixed_point_precision (slot 10, ver _META acima). Pro
        # terreno dá 10**0 = 1 — bate com o valor já validado (SBMT raw=723 ≈
        # 722 m; SBBH raw=788 ≈ 789 m); o número não mudou, só a forma de
        # chegar nele (antes líamos por acidente o slot errado — version, que
        # também vale 1 — e hardcodávamos escala=1; agora lemos o campo certo).
        self.scale = 10 ** self.meta.fixed_point

    # ---- Web Mercator (idêntico ao read-tiles.js) ----
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

    def _tile_samples(self, tx, ty):
        key = (tx, ty)
        cached = self._tiles.get(key)
        if cached is not None:
            return cached
        url = f"{self.base}/{self.ts}/{int(self.level)}/{self.z}/{tx}/{ty}.fb"
        raw = _http_get(url)
        data, _ = _root(raw).vector_bytes(0)  # Tile.data => slot 0
        fmt, size = _DTYPE[self.meta.data_type]
        arr = struct.unpack(f"<{len(data)//size}{fmt}", data[: (len(data)//size)*size])
        self._tiles[key] = arr
        return arr

    def elevation(self, lon, lat, channel=0, radius_px: int = 0) -> float:
        """
        Elevação do terreno no ponto, em PÉS.
        radius_px: se >0, retorna o MÁXIMO numa janela (2r+1)x(2r+1) de pixels
        ao redor do ponto. Em z=10 (~150 m/pixel) isso evita subestimar picos
        agudos (subamostragem). Para folga de terreno, use radius_px>=1.
        """
        tx, ty, px, py = self._tile_pixel(lon, lat)
        W, H, C = self.meta.width, self.meta.height, self.meta.channels
        if radius_px <= 0:
            arr = self._tile_samples(tx, ty)
            return (arr[(py * W + px) * C + channel] / self.scale) * self.to_ft
        best = -1e9
        for dy in range(-radius_px, radius_px + 1):
            for dx in range(-radius_px, radius_px + 1):
                gx, gy = px + dx, py + dy  # pode cruzar borda de tile
                ttx, tty, ppx, ppy = tx, ty, gx, gy
                if gx < 0: ttx, ppx = tx - 1, gx + W
                elif gx >= W: ttx, ppx = tx + 1, gx - W
                if gy < 0: tty, ppy = ty - 1, gy + H
                elif gy >= H: tty, ppy = ty + 1, gy - H
                try:
                    arr = self._tile_samples(ttx, tty)
                    v = arr[(ppy * W + ppx) * C + channel]
                    best = max(best, v)
                except Exception:
                    continue
        return (best / self.scale) * self.to_ft

    def max_along(self, path, step_nm: float = 0.5, radius_px: int = 1) -> float:
        """Maior elevação (pés) amostrando ao longo de uma polilinha [(lon,lat),...].
        radius_px>=1 por padrão para não subestimar terreno entre pixels."""
        best = -1e9
        for (lon1, lat1), (lon2, lat2) in zip(path, path[1:]):
            d = _haversine_nm(lat1, lon1, lat2, lon2)
            steps = max(1, int(d / step_nm))
            for k in range(steps + 1):
                f = k / steps
                lon = lon1 + (lon2 - lon1) * f
                lat = lat1 + (lat2 - lat1) * f
                best = max(best, self.elevation(lon, lat, radius_px=radius_px))
        return best

    def floor_along(self, path, step_nm: float = 0.5) -> float:
        """Piso de cruzeiro = maior terreno no trecho + margem (pés)."""
        return self.max_along(path, step_nm) + self.clearance_ft


def _haversine_nm(lat1, lon1, lat2, lon2):
    R = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


if __name__ == "__main__":
    # Rode na SUA maquina (o CDN e publico). Confirma o parsing do metadata e
    # imprime a elevacao (metros / pes). Escala = 10**fixed_point (slot 10).
    t = Terrain(elevation_in_meters=True)
    m = t.meta
    print("=== METADATA ===")
    print(f"zooms={m.zooms} levels={m.levels} timestamps={m.timestamps}")
    print(f"width={m.width} height={m.height} channels={m.channels} "
          f"data_type={m.data_type} fixed_point={m.fixed_point} "
          f"(escala=10**{m.fixed_point}={t.scale:g}) type={m.type}")
    print(f"escala usada = {t.scale:g}  (raw int16 ja em METROS)")

    print("\n=== ELEVACAO ===")
    refs = [("SBMT (~722 m)", -46.6375, -23.509167),
            ("SBBH (~789 m)", -43.950556, -19.851944),
            ("Pico da Bandeira reg. (~2892 m)", -41.7947, -20.4322),
            ("Litoral RJ (~0 m)", -43.1875, -22.9711)]
    for nome, lon, lat in refs:
        e1 = t.elevation(lon, lat)                 # pixel unico
        e5 = t.elevation(lon, lat, radius_px=2)    # max vizinhanca 5x5
        print(f"{nome}: {e1/3.28084:.0f} m / {e1:.0f} ft"
              f"   (max vizinhanca 5x5: {e5/3.28084:.0f} m / {e5:.0f} ft)")
