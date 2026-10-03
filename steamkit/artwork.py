import json
import os
import ssl
import urllib.error
import time
import re
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://www.steamgriddb.com/api/v2"

GRID, HERO, LOGO, WIDE = 0, 1, 2, 3
ENDPOINTS = {
    GRID: ("grids", {"dimensions": "600x900"}),
    WIDE: ("grids", {"dimensions": "920x430,460x215"}),
    HERO: ("heroes", {}),
    LOGO: ("logos", {}),
}

NOISE = re.compile(
    r"(\[.*?\]|\(.*?\)|\{.*?\}|\bv?\d+(\.\d+){1,}\w*\b|\b(goty|game of the year|definitive|"
    r"deluxe|complete|edition|repack|fitgirl|dodi|elamigos|gog|steamrip|portable|x64|x86|win64|"
    r"win32|shipping|launcher|dx11|dx12|vulkan)\b)",
    re.I,
)


def clean(name: str) -> str:
    s = re.sub(r"_+", " ", name)
    s = NOISE.sub(" ", s)
    s = re.sub(r"\.+", " ", s)
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)  # CamelCase -> Camel Case
    s = re.sub(r"[-–:]+\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def candidates(title: str, exe: Path) -> list[str]:
    """Search terms in priority order: game folder, exe's own folder, exe name."""
    seen, out = set(), []
    for raw in (title, exe.parent.name, exe.stem):
        for term in (clean(raw), raw):
            if term and len(term) > 1 and term.lower() not in seen:
                seen.add(term.lower())
                out.append(term)
    return out


class ArtError(Exception):
    pass


# Bundled Pythons (e.g. Decky's) often can't find the system CA store, so point at it explicitly.
CA_FILES = ["/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
            "/etc/ssl/cert.pem", "/etc/ca-certificates/extracted/tls-ca-bundle.pem"]
_ctx_cache: ssl.SSLContext | None = None


def _ctx() -> ssl.SSLContext:
    global _ctx_cache
    if _ctx_cache is None:
        ca = next((f for f in CA_FILES if os.path.exists(f)), None)
        _ctx_cache = ssl.create_default_context(cafile=ca)
    return _ctx_cache


def _open(req: urllib.request.Request, timeout: int):
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=_ctx())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ArtError("Invalid SteamGridDB key") from e
        raise
    except urllib.error.URLError as e:
        raise ArtError(f"Can't reach SteamGridDB: {e.reason}") from e


def _get(path: str, key: str, params: dict | None = None):
    url = f"{API}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": "SteamKit"})
    try:
        with _open(req, 15) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise ArtError(f"SteamGridDB error {e.code}") from e
    return data.get("data") if data.get("success") else None


def search(key: str, term: str, limit: int = 8) -> list[dict]:
    hits = _get(f"search/autocomplete/{urllib.parse.quote(term)}", key) or []
    out = []
    for h in hits[:limit]:
        year = ""
        if h.get("release_date"):
            year = time.strftime("%Y", time.gmtime(h["release_date"]))
        out.append({"id": h["id"], "name": h["name"], "year": year})
    return out


def find_game(key: str, terms: list[str]) -> dict | None:
    for term in terms:
        hits = _get(f"search/autocomplete/{urllib.parse.quote(term)}", key)  # ArtError propagates
        if hits:
            return {"id": hits[0]["id"], "name": hits[0]["name"], "matched": term}
    return None


def _download(url: str) -> bytes:
    with _open(urllib.request.Request(url, headers={"User-Agent": "SteamKit"}), 30) as r:
        return r.read()


def fetch_icon(key: str, game_id: int) -> tuple[bytes, str] | None:
    """Shortcut icon (PNG preferred; Steam also takes ICO). Steam shows it after a restart."""
    for mimes in ("image/png", "image/vnd.microsoft.icon"):
        items = _get(f"icons/game/{game_id}", key, {"mimes": mimes, "types": "static"})
        for item in items or []:
            url = item.get("url", "")
            try:
                return _download(url), "ico" if url.lower().endswith(".ico") else "png"
            except Exception:
                continue
    return None


def fetch_assets(key: str, game_id: int) -> dict[int, tuple[bytes, str]]:
    out = {}
    for kind, (endpoint, params) in ENDPOINTS.items():
        items = _get(f"{endpoint}/game/{game_id}", key, {**params, "types": "static"})
        for item in items or []:
            url = item.get("url", "")
            ext = url.rsplit(".", 1)[-1].lower()
            if ext not in {"png", "jpg", "jpeg", "webp"}:
                continue
            try:
                out[kind] = (_download(url), "jpg" if ext == "jpeg" else ext)
                break
            except Exception:
                continue
    return out


# Filenames Steam reads from userdata/<user>/config/grid/ for a given (unsigned) appid.
GRID_NAMES = {GRID: "{id}p", WIDE: "{id}", HERO: "{id}_hero", LOGO: "{id}_logo"}


def write_grid(grid_dir: Path, appid: int, assets: dict[int, tuple[bytes, str]]) -> list[Path]:
    grid_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for kind, (data, ext) in assets.items():
        stem = GRID_NAMES[kind].format(id=appid)
        for old in grid_dir.glob(stem + ".*"):
            old.unlink()
        out = grid_dir / f"{stem}.{ext}"
        out.write_bytes(data)
        written.append(out)
    return written


def remove_grid(grid_dir: Path, appid: int) -> None:
    for stem in GRID_NAMES.values():
        for f in grid_dir.glob(stem.format(id=appid) + ".*"):
            f.unlink()
