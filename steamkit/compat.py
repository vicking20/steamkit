import re
from pathlib import Path

from . import textvdf

# Valve tools Steam can download on demand even if not installed yet.
DOWNLOADABLE = [
    ("proton_experimental", "Proton Experimental"),
    ("proton_9", "Proton 9.0"),
    ("proton_8", "Proton 8.0"),
]


def library_dirs(root: Path) -> list[Path]:
    dirs = [root]
    lf = root / "steamapps" / "libraryfolders.vdf"
    if lf.exists():
        data = textvdf.loads(lf.read_text(errors="replace")).get("libraryfolders", {})
        for v in data.values():
            if isinstance(v, dict) and v.get("path"):
                p = Path(v["path"])
                if p.resolve() != root.resolve():
                    dirs.append(p)
    return dirs


def official_name(dirname: str) -> str | None:
    """'Proton 9.0 (Beta)' -> proton_9, 'Proton 6.3' -> proton_63, 'Proton - Experimental' -> proton_experimental."""
    low = dirname.lower()
    if "experimental" in low:
        return "proton_experimental"
    if "hotfix" in low:
        return "proton_hotfix"
    m = re.match(r"proton (\d+)\.(\d+)", low)
    if m:
        major, minor = m.groups()
        return f"proton_{major}" if minor == "0" else f"proton_{major}{minor}"
    return None


def list_tools(root: Path, home: Path) -> list[dict]:
    tools: dict[str, dict] = {}
    for lib in library_dirs(root):
        common = lib / "steamapps" / "common"
        if not common.is_dir():
            continue
        for d in sorted(common.iterdir()):
            if d.name.lower().startswith("proton") and (d / "proton").exists():
                tid = official_name(d.name)
                if tid:
                    tools[tid] = {"id": tid, "label": d.name, "installed": True}

    for base in {root / "compatibilitytools.d", home / ".steam/root/compatibilitytools.d",
                 home / ".local/share/Steam/compatibilitytools.d"}:
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir(), reverse=True):
            vdf = d / "compatibilitytool.vdf"
            if not vdf.exists():
                continue
            entries = textvdf.loads(vdf.read_text(errors="replace")).get("compatibilitytools", {}).get("compat_tools", {})
            for tid, info in entries.items():
                label = info.get("display_name", tid) if isinstance(info, dict) else tid
                tools.setdefault(tid, {"id": tid, "label": label, "installed": True})

    for tid, label in DOWNLOADABLE:
        tools.setdefault(tid, {"id": tid, "label": f"{label} (downloads on first launch)", "installed": False})

    def order(t):
        m = re.search(r"(\d+)", t["id"])
        return (not t["installed"], t["id"] != "proton_experimental", -(int(m.group(1)) if m else 0))

    return sorted(tools.values(), key=order)


def config_path(root: Path) -> Path:
    return root / "config" / "config.vdf"


def set_tools(root: Path, mapping: dict[int, str]) -> None:
    """mapping: {unsigned appid: tool id ('' clears)}. Steam must not be running."""
    p = config_path(root)
    data = textvdf.loads(p.read_text(errors="replace")) if p.exists() else {}
    node = data
    for key in ("InstallConfigStore", "Software", "Valve", "Steam", "CompatToolMapping"):
        # Steam's own key casing varies ("valve"/"Valve"); reuse whatever exists.
        existing = next((k for k in node if k.lower() == key.lower()), key)
        node = node.setdefault(existing, {})
    for appid, tool in mapping.items():
        if tool:
            node[str(appid)] = {"name": tool, "config": "", "priority": "250"}
        else:
            node.pop(str(appid), None)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(textvdf.dumps(data))
    if p.exists():
        p.replace(p.with_suffix(".vdf.steamkit.bak"))
    tmp.replace(p)
