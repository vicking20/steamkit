import argparse
import os
import sys
import time
from pathlib import Path

from . import lan, mounts, registry, saves, scanner, steam


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


def _lan_node(args) -> "lan.Node":
    base = Path(os.environ.get("XDG_DATA_HOME") or "~/.local/share").expanduser() / "steamkit"
    parent = Path(args.saves_parent).expanduser()
    return lan.Node(base / "lan.json", parent, http_port=args.port, discovery_port=args.discovery_port)


def _lan_peer(node, args) -> str:
    """Scan the network, then let the user pick a paired device (or pass --peer NAME)."""
    found = node.discover(hosts=tuple(args.host or ()))
    paired = [p for p in found if p["paired"]]
    if args.peer:
        paired = [p for p in paired if args.peer.lower() in p["name"].lower()]
    if not paired:
        sys.exit("No paired device found. Run `steamkit lan pair` on both devices first.")
    if len(paired) == 1:
        return paired[0]["id"]
    for i, p in enumerate(paired):
        print(f"  {i}) {p['name']} ({p['host']})")
    return paired[int(ask("Device", "0"))]["id"]


def cmd_lan_serve(args):
    node = _lan_node(args)
    node.start()
    print(f"Listening as '{node.state.name}' on port {node.http_port}. Ctrl+C to stop.", flush=True)
    if args.pair:
        print(f"Pairing code (valid {lan.PAIR_TTL // 60} minutes): {node.start_pairing()}", flush=True)
    try:
        while True:
            time.sleep(1)
    finally:
        node.stop()


def cmd_lan_scan(args):
    node = _lan_node(args)
    found = node.discover(hosts=tuple(args.host or ()))
    if not found:
        print("No SteamKit devices found. Run `steamkit lan serve` on the other device.")
    for p in found:
        print(f"  {p['name']:<24} {p['host']}:{p['port']}  {'paired' if p['paired'] else 'not paired'}")


def cmd_lan_pair(args):
    node = _lan_node(args)
    if args.show_code:
        node.start()
        print(f"Enter this code on the other device (valid {lan.PAIR_TTL // 60} minutes): {node.start_pairing()}",
              flush=True)
        try:
            while node.pairing_open():
                time.sleep(1)
        finally:
            node.stop()
        print("Paired." if node.state.peers else "Pairing closed.")
        return
    found = [p for p in node.discover(hosts=tuple(args.host or ())) if not p["paired"]]
    if not found:
        sys.exit("No unpaired device found. Run `steamkit lan pair --show-code` on the other device.")
    for i, p in enumerate(found):
        print(f"  {i}) {p['name']} ({p['host']})")
    p = found[int(ask("Device", "0"))]
    try:
        r = node.pair_with(p["host"], p["port"], ask("Code shown on that device"))
    except lan.PairingError as e:
        sys.exit(f"Pairing failed: {e}")
    print(f"Paired with {r['name']}.")


def cmd_lan_track(args):
    """Pick which save folders to keep in sync. Folders that look like game saves are marked with *.
    Folders that only exist on a paired device are listed too, so a new device can start from theirs."""
    node = _lan_node(args)
    rows = {c["entry"]: {**c, "where": "here"} for c in (node.root.is_dir() and lan.candidates(node.root) or [])}
    try:
        peer = _lan_peer(node, args)
        for c in node.remote_candidates(peer):
            if c["entry"] in rows:
                rows[c["entry"]]["where"] = "both"
            else:
                rows[c["entry"]] = {**c, "where": node.state.peers[peer]["name"]}
    except SystemExit:
        print("(No paired device reachable; listing this device's folders only.)")
    cands = sorted(rows.values(), key=lambda c: (not c["likely"], c["entry"].lower()))
    if not cands:
        sys.exit("No save folders found here or on a paired device. Turn on shared saves and launch a game first.")
    tracked = set(node.state.tracked)
    for i, c in enumerate(cands):
        print(f"  {i:3}) [{'x' if c['entry'] in tracked else ' '}] {'*' if c['likely'] else ' '} {c['entry']:<52} "
              f"{c['files']:>4} files  {time.strftime('%Y-%m-%d', time.localtime(c['mtime']))}  {c['where']}")
    print("  * = looks like a game's saves.")
    chosen = parse_selection(ask("Folders to track (e.g. 0,2,5-9, all, none)", "none"), len(cands))
    if ask("Replace the tracked list with this selection? (n = add to it) (y/n)", "n").lower() == "y":
        tracked = set()
    tracked |= {cands[i]["entry"] for i in chosen}
    print(f"Tracking {len(node.set_tracked(sorted(tracked)))} folder(s).")


def cmd_lan_sync(args):
    node = _lan_node(args)
    if not node.state.tracked:
        sys.exit("Nothing tracked yet. Run `steamkit lan track`.")
    if args.include_settings != (not node.state.skip_settings):
        node.state.skip_settings = not args.include_settings
        node.state.save()
    peer = _lan_peer(node, args)
    r = node.sync(peer, args.mode)
    print(f"Pulled {r['pulled']}, pushed {r['pushed']} file(s); {r['overwritten']} replaced; "
          f"{r['skipped']} settings file(s) left alone.")
    if r["backup"]:
        print(f"Backup before overwriting: {r['backup']}")
    for c in r["conflicts"]:
        print(f"  conflict (same time, different content, left alone): {c}")


def cmd_saves_import(args):
    parent = Path(args.saves_parent).expanduser()
    r = saves.import_tree(parent, Path(args.source), args.into, args.contents, args.dry_run,
                          skip=None if args.include_settings else lan.is_settings_file)
    tag = "[dry-run] would " if args.dry_run else ""
    print(f"{tag}import into {r['dest']}: {len(r['new'])} new, {len(r['replaced'])} replaced (newer), "
          f"{len(r['same'])} identical, {len(r['kept_newer'])} kept (existing is newer or same age), "
          f"{len(r['skipped'])} settings files left out")
    for label in ("new", "replaced", "kept_newer"):
        for rel in r[label][:15]:
            print(f"   {label:<10} {rel}")
        if len(r[label]) > 15:
            print(f"   {label:<10} … and {len(r[label]) - 15} more")
    if r["backup"]:
        print(f"Backup before replacing: {r['backup']}")


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

    sv = sub.add_parser("saves", help="Work with the shared saves folder")
    sv.add_argument("--saves-parent", default="~", help="Folder containing SteamKitSaves")
    svs = sv.add_subparsers(dest="saves_cmd", required=True)
    x = svs.add_parser("import", help="Copy saves from a folder (e.g. from a Windows PC) into the shared saves")
    x.add_argument("source", help="Folder to import, e.g. ~/Documents/NFSTR")
    x.add_argument("--into", default="Documents", help="Saves subfolder: Documents, 'Saved Games', AppData/Roaming…")
    x.add_argument("--contents", action="store_true", help="Merge the folder's contents instead of the folder itself "
                   "(use for a whole Windows Documents folder)")
    x.add_argument("--dry-run", action="store_true")
    x.add_argument("--include-settings", action="store_true", help="Also import config/ini/graphics files")
    x.set_defaults(func=cmd_saves_import)

    l = sub.add_parser("lan", help="Sync save folders with other SteamKit devices on your network")
    l.add_argument("--saves-parent", default="~", help="Folder containing SteamKitSaves")
    l.add_argument("--port", type=int, default=lan.HTTP_PORT)
    l.add_argument("--discovery-port", type=int, default=lan.DISCOVERY_PORT)
    l.add_argument("--host", action="append", help="Extra address to look for a device (if broadcast is blocked)")
    l.add_argument("--peer", help="Part of a paired device's name, to pick it without a prompt")
    ls = l.add_subparsers(dest="lan_cmd", required=True)
    x = ls.add_parser("serve", help="Make this device visible and available for syncing")
    x.add_argument("--pair", action="store_true", help="Also open pairing and print a code")
    x.set_defaults(func=cmd_lan_serve)
    ls.add_parser("scan", help="List SteamKit devices on the network").set_defaults(func=cmd_lan_scan)
    x = ls.add_parser("pair", help="Pair with another device (once)")
    x.add_argument("--show-code", action="store_true", help="Show a code here instead of entering one")
    x.set_defaults(func=cmd_lan_pair)
    ls.add_parser("track", help="Choose which save folders to sync").set_defaults(func=cmd_lan_track)
    x = ls.add_parser("sync", help="Sync tracked folders with a paired device")
    x.add_argument("--mode", choices=lan.MODES, default="newest", help="pull, push, or newest file wins both ways")
    x.add_argument("--include-settings", action="store_true",
                   help="Also sync config/ini/graphics files (skipped by default; remembered)")
    x.set_defaults(func=cmd_lan_sync)

    args = p.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print()
        sys.exit(130)


if __name__ == "__main__":
    main()
