import struct

MAP, STR, INT, END = b"\x00", b"\x01", b"\x02", b"\x08"


def _cstr(data, i):
    j = data.index(b"\x00", i)
    return data[i:j].decode("utf-8", "replace"), j + 1


def loads(data: bytes) -> dict:
    def parse(i):
        out = {}
        while True:
            t = data[i:i + 1]
            i += 1
            if t == END or t == b"":
                return out, i
            key, i = _cstr(data, i)
            if t == MAP:
                out[key], i = parse(i)
            elif t == STR:
                out[key], i = _cstr(data, i)
            elif t == INT:
                out[key] = struct.unpack_from("<i", data, i)[0]
                i += 4
            else:
                raise ValueError(f"unsupported VDF type {t!r} at {i - 1}")
    return parse(0)[0]


def dumps(obj: dict) -> bytes:
    out = bytearray()
    for k, v in obj.items():
        key = str(k).encode() + b"\x00"
        if isinstance(v, dict):
            out += MAP + key + dumps(v)
        elif isinstance(v, bool) or isinstance(v, int):
            out += INT + key + struct.pack("<i", int(v))
        else:
            out += STR + key + str(v).encode() + b"\x00"
    return bytes(out + END)
