import os
import shutil
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT_NAME = "SteamKitSaves"

# Path inside drive_c/users/<user>/ -> folder inside Saves/
USER_FOLDERS = {
    "Documents": "Documents",
    "Saved Games": "Saved Games",
    "AppData/Roaming": "AppData/Roaming",
    "AppData/Local": "AppData/Local",
    "AppData/LocalLow": "AppData/LocalLow",
}
# Path inside drive_c/users/ -> folder inside Saves/ (repacks' Steam emulators save here)
SHARED_FOLDERS = {"Public/Documents": "Public Documents"}

# Caches that bloat backups and are safe to lose.
BACKUP_EXCLUDE = {"temp", "d3dscache", "nvidia", "nvidia corporation", "microsoft", "crashdumps",
                  "crashreportclient", "deriveddatacache", "shadercache", "webcache"}


def root_dir(parent: Path) -> Path:
    return Path(parent) / ROOT_NAME


def saves_dir(parent: Path) -> Path:
    return root_dir(parent) / "Saves"


def backups_dir(parent: Path) -> Path:
    return root_dir(parent) / "Backups"


@dataclass
class LinkResult:
    linked: list[str] = field(default_factory=list)     # links created this run
    moved: list[str] = field(default_factory=list)      # save entries moved into Saves/
    conflicts: list[str] = field(default_factory=list)  # entries kept side by side
    skipped: list[str] = field(default_factory=list)    # e.g. symlinks to the user's real ~/Documents


def _merge_into(src: Path, dst: Path, label: str, res: LinkResult) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for entry in list(src.iterdir()):
        target = dst / entry.name
        if not target.exists() and not target.is_symlink():
            shutil.move(str(entry), str(target))
            res.moved.append(str(target))
        elif entry.is_dir() and not entry.is_symlink() and target.is_dir():
            _merge_into(entry, target, label, res)
            if not any(entry.iterdir()):
                entry.rmdir()
        else:
            alt = dst / f"{entry.name} (from {label})"
            n = 2
            while alt.exists():
                alt = dst / f"{entry.name} (from {label} {n})"
                n += 1
            shutil.move(str(entry), str(alt))
            res.conflicts.append(str(alt))


def _link_one(path: Path, target: Path, label: str, res: LinkResult, owner: tuple[int, int] | None) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        if path.resolve() == target.resolve():
            return
        # Desktop Wine links Documents to the user's real ~/Documents: repoint, never move their files.
        old = os.readlink(path)
        if ROOT_NAME not in old:  # an old SteamKitSaves location is just repointed silently
            res.skipped.append(f"{path} -> {old} (repointed, contents left in place)")
        path.unlink()
    elif path.is_dir():
        _merge_into(path, target, label, res)
        shutil.rmtree(path)  # only empty dirs / leftovers remain after the merge
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(target, target_is_directory=True)
    if owner:
        os.lchown(path, *owner)
    res.linked.append(str(path))


def link_prefix(drive_c: Path, parent: Path, label: str, owner: tuple[int, int] | None = None) -> LinkResult:
    """Point a prefix's save folders at Saves/. Idempotent; safe to run on every Steam start."""
    res = LinkResult()
    users = Path(drive_c) / "users"
    if not users.is_dir():
        return res
    saves = saves_dir(parent)
    for user in users.iterdir():
        if user.name == "Public" or not user.is_dir() or user.is_symlink():
            continue
        for rel, dest in USER_FOLDERS.items():
            _link_one(user / rel, saves / dest, label, res, owner)
    for rel, dest in SHARED_FOLDERS.items():
        _link_one(users / rel, saves / dest, label, res, owner)
    if owner:
        _chown_tree(root_dir(parent), owner)
    return res


def _chown_tree(path: Path, owner: tuple[int, int]) -> None:
    for dirpath, dirnames, filenames in os.walk(path):
        os.lchown(dirpath, *owner)
        for n in dirnames + filenames:
            os.lchown(os.path.join(dirpath, n), *owner)


def is_linked(drive_c: Path, parent: Path) -> bool:
    users = Path(drive_c) / "users"
    docs = [u / "Documents" for u in users.iterdir() if u.name != "Public"] if users.is_dir() else []
    return bool(docs) and all(d.is_symlink() and d.resolve() == (saves_dir(parent) / "Documents").resolve() for d in docs)



def _exclude(info: tarfile.TarInfo):
    parts = {p.lower() for p in Path(info.name).parts}
    return None if parts & BACKUP_EXCLUDE else info


def backup(parent: Path, note: str = "") -> Path:
    out_dir = backups_dir(parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    name = f"{stamp}{'_' + note if note else ''}.tar.gz"
    out = out_dir / name
    tmp = out.with_suffix(".tmp")
    with tarfile.open(tmp, "w:gz") as tar:
        tar.add(saves_dir(parent), arcname="Saves", filter=_exclude)
    tmp.replace(out)
    return out


def list_backups(parent: Path) -> list[dict]:
    d = backups_dir(parent)
    if not d.is_dir():
        return []
    items = sorted(d.glob("*.tar.gz"), reverse=True)
    return [{"name": p.name, "size": p.stat().st_size, "time": p.stat().st_mtime} for p in items]


def restore(parent: Path, name: str) -> Path:
    """Replace Saves/ with a backup. Takes a safety backup of the current state first."""
    src = backups_dir(parent) / name
    if not src.is_file() or src.parent != backups_dir(parent):
        raise FileNotFoundError(name)
    safety = backup(parent, "before-restore")
    saves = saves_dir(parent)
    staging = root_dir(parent) / ".restoring"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    with tarfile.open(src, "r:gz") as tar:
        try:
            tar.extractall(staging, filter="data")
        except TypeError:  # Python without extraction filters; our own archives only
            tar.extractall(staging)
    shutil.rmtree(saves)
    (staging / "Saves").replace(saves)
    shutil.rmtree(staging)
    return safety


def move_root(old_parent: Path, new_parent: Path) -> Path:
    """Move the whole SteamKitSaves folder; callers must re-run link_prefix to repoint links."""
    old, new = root_dir(old_parent), root_dir(new_parent)
    if new.exists():
        raise FileExistsError(f"{new} already exists")
    Path(new_parent).mkdir(parents=True, exist_ok=True)
    if old.exists():
        shutil.move(str(old), str(new))
    return new


def _sha(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def import_tree(parent: Path, src: Path, into: str = "Documents", contents: bool = False,
                dry_run: bool = False, owner: tuple[int, int] | None = None, skip=None) -> dict:
    """Copy a folder of saves (e.g. from a Windows PC) into Saves/<into>. Never deletes anything.

    By default the folder itself is copied (src/NFSTR -> Saves/Documents/NFSTR); with contents=True its
    children are merged in instead (use for a whole Windows 'Documents' folder). Files that differ are
    replaced only when the incoming one is newer, after a backup; otherwise the existing file is kept.
    skip(rel_path) -> True leaves a file out (used to keep a PC's graphics settings off a handheld)."""
    src = Path(src).expanduser().resolve()
    if into not in set(USER_FOLDERS.values()) | set(SHARED_FOLDERS.values()):
        raise ValueError(f"unknown save folder: {into}")
    if not src.is_dir():
        raise FileNotFoundError(f"{src} is not a folder")
    saves = saves_dir(parent)
    if src == saves or saves in src.parents or src in saves.parents:
        raise ValueError("pick a folder outside SteamKitSaves")
    dest = saves / into if contents else saves / into / src.name
    res = {"dest": str(dest), "new": [], "replaced": [], "same": [], "kept_newer": [], "skipped": [], "backup": None}
    todo = []
    for dirpath, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if not (Path(dirpath) / d).is_symlink()]
        for name in files:
            f = Path(dirpath) / name
            if f.is_symlink():
                continue
            rel = f.relative_to(src)
            if skip and skip(rel.as_posix()):
                res["skipped"].append(rel.as_posix())
                continue
            target = dest / rel
            if not target.exists():
                res["new"].append(rel.as_posix())
            elif _sha(f) == _sha(target):
                res["same"].append(rel.as_posix())
                continue
            elif f.stat().st_mtime > target.stat().st_mtime:
                res["replaced"].append(rel.as_posix())
            else:
                res["kept_newer"].append(rel.as_posix())
                continue
            todo.append((f, target))
    if dry_run or not todo:
        return res
    if res["replaced"]:
        saves.mkdir(parents=True, exist_ok=True)
        res["backup"] = backup(parent, "before-import").name
    for f, target in todo:
        created = []
        p = target.parent
        while not p.exists():
            created.append(p)
            p = p.parent
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        if owner:
            for c in [*created, target]:
                os.lchown(c, *owner)
    return res
