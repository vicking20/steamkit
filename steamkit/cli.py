import argparse
import os
import sys
from pathlib import Path

from . import mounts, registry, scanner, steam


def ask(prompt: str, default: str = "") -> str:
    v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return v or default


def pick_user(arg: str | None) -> str:
    users = steam.user_ids()
    if arg:
        return arg
    if not users:
        sys.exit("No Steam users found. Log into Steam at least once.")
    if len(users) == 1:
        return users[0]
    for i, u in enumerate(users):
        print(f"  {i}) {u}")
    return users[int(ask("Steam user", "0"))]


def parse_selection(text: str, n: int) -> list[int]:
    """'all', '1,3,5-8' -> indices."""
    if text.strip().lower() in {"a", "all"}:
        return list(range(n))
    out = set()
    for part in text.replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return sorted(i for i in out if 0 <= i < n)


def cmd_mount(args):
    drives = [d for d in mounts.list_drives() if not mounts.in_fstab(d.uuid)]
    if not drives:
        print("No unconfigured drives found (all are already in /etc/fstab).")
        return
    for i, d in enumerate(drives):
        print(f"  {i}) /dev/{d.name}  {d.size:>7}  {d.fstype:<6} {d.label or '(no label)'}  "
              f"{'mounted at ' + d.mountpoint if d.mountpoint else ''}")
    if not args.yes:
        print("\nNote: ext4/btrfs work best with Proton; NTFS/exFAT often break games.")
    for i in parse_selection(ask("Drives to mount at boot (e.g. 0,2 or all)"), len(drives)):
        d = drives[i]
        mp = ask(f"Mount point for {d.name}", mounts.default_mountpoint(d))
        line = mounts.install(d, mp, os.getuid(), os.getgid(), dry_run=args.dry_run)
        print(f"{'[dry-run] ' if args.dry_run else ''}fstab: {line}")
    if not args.dry_run:
        print("Done. Drives will now mount automatically at boot, including in Gaming Mode.")


def cmd_add(args):
    user = pick_user(args.user)
    groups = scanner.best_per_game(scanner.scan(Path(args.directory), args.depth))
    if not groups:
        sys.exit("No game executables found.")
    names = list(groups)
    for i, name in enumerate(names):
        best = groups[name][0]
        extra = f" (+{len(groups[name]) - 1} other exes)" if len(groups[name]) > 1 else ""
        print(f"  {i:3}) {name:<40} {best.exe.name}{extra}")
    chosen = parse_selection(ask("Games to add (e.g. 0,2,5-9 or all)"), len(names))

    new = []
    for i in chosen:
        name, cands = names[i], groups[names[i]]
        exe = cands[0].exe
        if args.pick_exe and len(cands) > 1:
            for j, c in enumerate(cands):
                print(f"      {j}) {c.exe.relative_to(Path(args.directory).expanduser().resolve())}")
            exe = cands[int(ask(f"    Exe for {name}", "0"))].exe
        new.append(steam.make_shortcut(name, exe))

    if args.dry_run:
        for s in new:
            print(f"[dry-run] {s['AppName']}: {s['Exe']}  appid={s['appid'] & 0xFFFFFFFF}")
        return
    if steam.is_running():
        if ask("Steam must be closed to edit shortcuts. Close it now? (y/n)", "y").lower() != "y":
            sys.exit("Aborted.")
        steam.shutdown()
    added = steam.add_shortcuts(user, new)
    for s in added:
        registry.record(user, s)
    print(f"Added {len(added)} game(s); skipped {len(new) - len(added)} already present. Restart Steam to see them.")


def cmd_list(args):
    games = registry.load()["games"]
    if not games:
        print("No games added by SteamKit yet.")
    for appid, g in sorted(games.items(), key=lambda kv: kv[1]["name"].lower()):
        print(f"  {appid}  {g['name']:<40} {g['exe']}")


def cmd_remove(args):
    games = registry.load()["games"]
    items = sorted(games.items(), key=lambda kv: kv[1]["name"].lower())
    if not items:
        sys.exit("No games added by SteamKit.")
    for i, (appid, g) in enumerate(items):
        print(f"  {i:3}) {g['name']}")
    chosen = [items[i] for i in parse_selection(ask("Games to remove from Steam"), len(items))]
    if not chosen:
        return
    if steam.is_running():
        if ask("Steam must be closed. Close it now? (y/n)", "y").lower() != "y":
            sys.exit("Aborted.")
        steam.shutdown()
    by_user: dict[str, set[int]] = {}
    for appid, g in chosen:
        by_user.setdefault(g["user"], set()).add(steam.to_signed(int(appid)))
    for user, ids in by_user.items():
        steam.remove_shortcuts(user, ids)
    for appid, _ in chosen:
        registry.forget(int(appid))
    print(f"Removed {len(chosen)} game(s). Game files were not touched.")


def main(argv=None):
    p = argparse.ArgumentParser(prog="steamkit", description="Manage drives and non-Steam games")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mount", help="Mount external drives automatically at boot")
    m.add_argument("--dry-run", action="store_true")
    m.add_argument("-y", "--yes", action="store_true")
    m.set_defaults(func=cmd_mount)

    a = sub.add_parser("add", help="Scan a folder and bulk-add games as non-Steam shortcuts")
    a.add_argument("directory")
    a.add_argument("--user", help="Steam userdata id")
    a.add_argument("--depth", type=int, default=5)
    a.add_argument("--pick-exe", action="store_true", help="Choose the exe manually per game")
    a.add_argument("--dry-run", action="store_true")
    a.set_defaults(func=cmd_add)

    sub.add_parser("list", help="List games added by SteamKit").set_defaults(func=cmd_list)
    sub.add_parser("remove", help="Remove SteamKit-added games from Steam").set_defaults(func=cmd_remove)

    args = p.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print()
        sys.exit(130)


if __name__ == "__main__":
    main()
