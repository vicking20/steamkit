import os
import re
from dataclasses import dataclass
from pathlib import Path

JUNK = re.compile(
    r"(unins\d*|uninstall|setup|install|vc_?redist|vcredist|dxsetup|dxwebsetup|directx|"
    r"crashhandler|crashreport|crashpad|unitycrashhandler|ue4prereq|uerprereq|prereq|"
    r"easyanticheat|eac_?launcher|battleye|be_?service|dotnet|ndp\d|oalinst|physx|"
    r"updater|update|patcher|config|settings|launcherhelper|cefprocess|helper|"
    r"quicksfv|touchup|7z|winrar|notification_helper)",
    re.I,
)
SKIP_DIRS = {"_commonredist", "redist", "redistributables", "directx", "support", "__installer",
             "_redist", "prerequisites", "dotnet", "vcredist", "engine", "binaries_old"}


@dataclass
class Candidate:
    name: str          # Guessed game title (top-level folder name)
    exe: Path
    size: int

    @property
    def game_dir(self) -> Path:
        return self.exe.parent


def _is_junk(exe: Path) -> bool:
    return bool(JUNK.search(exe.stem))


def scan(root: Path, max_depth: int = 5) -> list[Candidate]:
    root = root.expanduser().resolve()
    found: list[Candidate] = []
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        depth = len(here.relative_to(root).parts)
        dirnames[:] = [] if depth >= max_depth else [
            d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")
            and not d.lower().endswith(("_data", ".app"))  # Unity data / mac bundles hold no game exes
        ]
        for f in filenames:
            if not f.lower().endswith(".exe"):
                continue
            exe = here / f
            if _is_junk(exe):
                continue
            rel = exe.relative_to(root)
            title = rel.parts[0] if len(rel.parts) > 1 else root.name
            try:
                size = exe.stat().st_size
            except OSError:
                continue
            found.append(Candidate(name=title, exe=exe, size=size))
    return found


def best_per_game(cands: list[Candidate]) -> dict[str, list[Candidate]]:
    groups: dict[str, list[Candidate]] = {}
    for c in cands:
        groups.setdefault(c.name, []).append(c)

    def norm(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())

    for name, items in groups.items():
        items.sort(key=lambda c: (
            norm(c.exe.stem) not in norm(name) and norm(name) not in norm(c.exe.stem),
            len(c.exe.parts),
            -c.size,
        ))
    return dict(sorted(groups.items(), key=lambda kv: kv[0].lower()))
