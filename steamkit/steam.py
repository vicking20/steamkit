import os
import shlex
import shutil
import subprocess
import time
import zlib
from pathlib import Path

from . import binvdf

STEAM_ROOTS = [
    "~/.steam/steam",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/data/Steam",  # Flatpak
    "C:/Program Files (x86)/Steam",
]


def steam_root(home: Path | None = None) -> Path:
    for p in STEAM_ROOTS:
        path = Path(p.replace("~", str(home))) if home and p.startswith("~") else Path(p).expanduser()
        if (path / "userdata").is_dir():
            return path.resolve()
    raise FileNotFoundError("Steam installation not found")


def user_ids(root: Path | None = None) -> list[str]:
    root = root or steam_root()
    return sorted(d.name for d in (root / "userdata").iterdir() if d.name.isdigit() and d.name != "0")


def shortcuts_path(user: str, root: Path | None = None) -> Path:
    return (root or steam_root()) / "userdata" / user / "config" / "shortcuts.vdf"


def is_running() -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout.lower()
        return "steam.exe" in out
    return subprocess.run(["pgrep", "-x", "steam"], capture_output=True).returncode == 0


def shutdown(timeout: float = 60, as_user: str | None = None) -> None:
    """Ask Steam to exit; Steam overwrites shortcuts.vdf on exit, so writes must happen after."""
    if not is_running():
        return
    steam = shutil.which("steam") or "steam"
    cmd = [steam, "-shutdown"]
    if as_user:
        cmd = ["runuser", "-u", as_user, "--"] + cmd
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.time() + timeout
    while time.time() < end:
        if not is_running():
            return
        time.sleep(1)
    raise TimeoutError("Steam did not exit")


def shortcut_appid(exe: str, name: str) -> int:
    """Unsigned 32-bit app id Steam assigns to a shortcut (used for grid art and compatdata)."""
    return (zlib.crc32((exe + name).encode()) & 0xFFFFFFFF) | 0x80000000


def to_signed(n: int) -> int:
    return n - (1 << 32) if n >= 1 << 31 else n


def load_shortcuts(user: str, root: Path | None = None) -> dict:
    p = shortcuts_path(user, root)
    if not p.exists():
        return {}
    return binvdf.loads(p.read_bytes()).get("shortcuts", {})


def save_shortcuts(user: str, shortcuts: dict, root: Path | None = None) -> None:
    if is_running():
        raise RuntimeError("Steam is running; close it first or it will overwrite changes")
    p = shortcuts_path(user, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        shutil.copy2(p, p.with_suffix(".vdf.bak"))
    ordered = {str(i): v for i, v in enumerate(shortcuts.values())}
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(binvdf.dumps({"shortcuts": ordered}))
    tmp.replace(p)


def make_shortcut(name: str, exe: Path, start_dir: Path | None = None, launch_options: str = "") -> dict:
    exe_q = f'"{exe}"'
    appid = shortcut_appid(exe_q, name)
    return {
        "appid": to_signed(appid),
        "AppName": name,
        "Exe": exe_q,
        "StartDir": f'"{start_dir or exe.parent}"',
        "icon": "",
        "ShortcutPath": "",
        "LaunchOptions": launch_options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "tags": {"0": "SteamKit"},
    }


def add_shortcuts(user: str, new: list[dict], root: Path | None = None) -> list[dict]:
    current = load_shortcuts(user, root)
    existing = {s.get("appid") for s in current.values()}
    added = [s for s in new if s["appid"] not in existing]
    merged = list(current.values()) + added
    save_shortcuts(user, {str(i): s for i, s in enumerate(merged)}, root)
    return added


def remove_shortcuts(user: str, appids: set[int], root: Path | None = None) -> int:
    current = load_shortcuts(user, root)
    keep = [s for s in current.values() if s.get("appid") not in appids]
    save_shortcuts(user, {str(i): s for i, s in enumerate(keep)}, root)
    return len(current) - len(keep)


def quote(p) -> str:
    return shlex.quote(str(p))
