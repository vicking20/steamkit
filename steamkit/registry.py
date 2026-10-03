import json
import os
from pathlib import Path


_override: Path | None = None


def set_path(path: Path) -> None:
    global _override
    _override = Path(path)


def _path() -> Path:
    if _override:
        _override.parent.mkdir(parents=True, exist_ok=True)
        return _override
    base = os.environ.get("XDG_DATA_HOME") or os.environ.get("APPDATA") or "~/.local/share"
    p = Path(base).expanduser() / "steamkit" / "registry.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict:
    p = _path()
    return json.loads(p.read_text()) if p.exists() else {"games": {}}


def save(data: dict) -> None:
    p = _path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def record(user: str, shortcut: dict) -> None:
    data = load()
    key = str(shortcut["appid"] & 0xFFFFFFFF)
    data["games"][key] = {"user": user, "name": shortcut["AppName"], "exe": shortcut["Exe"].strip('"')}
    save(data)


def forget(appid_unsigned: int) -> None:
    data = load()
    data["games"].pop(str(appid_unsigned), None)
    save(data)
