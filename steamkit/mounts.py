import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

FSTAB = Path("/etc/fstab")
LINUX_FS = {"ext4", "ext3", "btrfs", "xfs", "f2fs"}
NEEDS_UID = {"ntfs", "ntfs3", "exfat", "vfat"}


@dataclass
class Drive:
    name: str
    uuid: str
    fstype: str
    label: str
    size: str
    mountpoint: str | None


def list_drives() -> list[Drive]:
    out = subprocess.run(
        ["lsblk", "-J", "-o", "NAME,UUID,FSTYPE,LABEL,SIZE,MOUNTPOINT"],
        capture_output=True, text=True, check=True,
    ).stdout
    drives = []

    def walk(nodes):
        for n in nodes:
            walk(n.get("children") or [])
            mp = n.get("mountpoint")
            if not n.get("uuid") or not n.get("fstype") or n["fstype"] in {"swap", "crypto_LUKS"}:
                continue
            if mp and (mp == "/" or mp.startswith(("/boot", "/efi", "/home", "/var", "/usr", "[SWAP]"))):
                continue
            drives.append(Drive(n["name"], n["uuid"], n["fstype"], n.get("label") or "", n.get("size") or "", mp))

    walk(json.loads(out)["blockdevices"])
    return drives


def in_fstab(uuid: str) -> bool:
    if not FSTAB.exists():
        return False
    pat = re.compile(rf"(UUID=|/by-uuid/){re.escape(uuid)}\b", re.I)
    return any(pat.search(l) for l in FSTAB.read_text().splitlines() if not l.lstrip().startswith("#"))


def default_mountpoint(d: Drive) -> str:
    label = re.sub(r"[^A-Za-z0-9_.-]", "_", d.label) if d.label else d.uuid[:8]
    return f"/mnt/{label}"


def fstab_line(d: Drive, mountpoint: str, uid: int, gid: int) -> str:
    fs = "ntfs3" if d.fstype == "ntfs" else d.fstype
    opts = ["defaults", "nofail", "x-systemd.device-timeout=5s"]
    if fs in LINUX_FS:
        opts.append("noatime")
    if fs in NEEDS_UID:
        opts += [f"uid={uid}", f"gid={gid}", "umask=022"]
        if fs == "ntfs3":
            opts.append("prealloc")
    return f"UUID={d.uuid}\t{mountpoint}\t{fs}\t{','.join(opts)}\t0\t0"


def install(d: Drive, mountpoint: str, uid: int, gid: int, dry_run: bool = False, as_root: bool = False) -> str:
    line = fstab_line(d, mountpoint, uid, gid)
    mp = shlex.quote(mountpoint)
    script = "; ".join([
        "set -e",
        "cp /etc/fstab /etc/fstab.steamkit.bak",
        f"mkdir -p {mp}",
        f"printf '%s\\n' {shlex.quote('# Added by SteamKit')} {shlex.quote(line)} >> /etc/fstab",
        "systemctl daemon-reload",
        f"(umount {shlex.quote('/dev/' + d.name)} 2>/dev/null || true)",
        f"mount {mp}",
        f"chown {uid}:{gid} {mp} || true",
    ])
    if not dry_run:
        cmd = ["sh", "-c", script] if as_root else ["pkexec", "sh", "-c", script]
        subprocess.run(cmd, check=True, capture_output=as_root, text=True)
    return line
