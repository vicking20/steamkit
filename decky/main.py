import asyncio
import base64
import json
import os
import pwd
import time
from pathlib import Path

import decky

from steamkit import artwork, compat, lan, mounts, registry, saves, scanner, steam

SETTINGS = Path(decky.DECKY_PLUGIN_SETTINGS_DIR) / "settings.json"
registry.set_path(Path(decky.DECKY_PLUGIN_SETTINGS_DIR) / "registry.json")


def _settings() -> dict:
    return json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}


def _save_settings(data: dict) -> None:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(data, indent=2))


def _user_ids() -> tuple[int, int]:
    pw = pwd.getpwnam(decky.DECKY_USER)
    return pw.pw_uid, pw.pw_gid


def _root() -> Path:
    return steam.steam_root(Path(decky.DECKY_USER_HOME))


def _chown(*paths: Path) -> None:
    """Files we write as root must stay owned by the user, or Steam can't update them."""
    uid, gid = _user_ids()
    for p in paths:
        if p.exists():
            os.chown(p, uid, gid)
            if p.is_dir():
                for child in p.iterdir():
                    os.chown(child, uid, gid)


def _save_icon(appid: int, data: bytes, ext: str) -> str:
    """Icons are referenced by path from the shortcut, so they need a stable, user-readable home."""
    d = Path(decky.DECKY_PLUGIN_RUNTIME_DIR) / "icons"
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob(f"{appid}.*"):
        old.unlink()
    out = d / f"{appid}.{ext}"
    out.write_bytes(data)
    os.chmod(d, 0o755)
    os.chmod(out, 0o644)
    return str(out)


def _gaming_mode() -> bool:
    """Gamescope session: Steam is relaunched instantly on exit, so restart-and-write can't work."""
    for comm in Path("/proc").glob("[0-9]*/comm"):
        try:
            if comm.read_text().startswith("gamescope"):
                return True
        except OSError:
            continue
    return False


def _saves_parent() -> Path:
    return Path(_settings().get("saves_parent") or decky.DECKY_USER_HOME)


def _prefixes_in_use() -> set[str]:
    used = set()
    for env in Path("/proc").glob("[0-9]*/environ"):
        try:
            for var in env.read_bytes().split(b"\0"):
                if var.startswith((b"STEAM_COMPAT_DATA_PATH=", b"WINEPREFIX=")):
                    used.add(os.path.realpath(var.split(b"=", 1)[1].decode(errors="replace")))
        except OSError:
            continue
    return used


def _prefixes() -> list[dict]:
    root = _root()
    out = []
    for appid, g in registry.load()["games"].items():
        data = root / "steamapps" / "compatdata" / appid
        out.append({"label": g["name"], "data": data, "drive_c": data / "pfx" / "drive_c"})
    if _settings().get("include_desktop_wine", True):
        wine = Path(decky.DECKY_USER_HOME) / ".wine"
        out.append({"label": "Desktop Wine", "data": wine, "drive_c": wine / "drive_c"})
    return out


_node: lan.Node | None = None


def _lan() -> lan.Node:
    global _node
    if _node is None:
        _node = lan.Node(Path(decky.DECKY_PLUGIN_SETTINGS_DIR) / "lan.json", _saves_parent(), _user_ids(),
                         busy=lambda: bool(_prefixes_in_use()))
    _node.set_parent(_saves_parent())
    return _node


def _wait_for_steam_exit(timeout=60) -> None:
    steam.shutdown(timeout=timeout, as_user=decky.DECKY_USER)
    time.sleep(2)  # let Steam finish flushing its own files before we write ours


class Plugin:
    async def get_settings(self) -> dict:
        s = _settings()
        return {
            "sgdb_key": s.get("sgdb_key", ""),
            "compat_tool": s.get("compat_tool", "proton_experimental"),
            "last_dir": s.get("last_dir", decky.DECKY_USER_HOME),
            # Live API in Gaming Mode; the restart method only works when Steam stays closed (desktop).
            "mode": "live" if _gaming_mode() else s.get("mode", "restart"),
            "gaming_mode": _gaming_mode(),
            "shared_saves": s.get("shared_saves", False),
            "include_desktop_wine": s.get("include_desktop_wine", True),
        }

    async def set_setting(self, key: str, value) -> None:
        s = _settings()
        s[key] = value
        _save_settings(s)

    async def list_drives(self) -> list[dict]:
        out = []
        for d in mounts.list_drives():
            out.append({**d.__dict__, "configured": mounts.in_fstab(d.uuid),
                        "suggested": mounts.default_mountpoint(d)})
        return out

    async def mount_drive(self, uuid: str, mountpoint: str) -> dict:
        drive = next((d for d in mounts.list_drives() if d.uuid == uuid), None)
        if not drive:
            return {"ok": False, "error": "Drive not found"}
        if mounts.in_fstab(uuid):
            return {"ok": False, "error": "Already configured"}
        try:
            uid, gid = _user_ids()
            line = mounts.install(drive, mountpoint, uid, gid, as_root=True)
            return {"ok": True, "line": line}
        except Exception as e:
            decky.logger.exception("mount failed")
            err = getattr(e, "stderr", None) or str(e)
            return {"ok": False, "error": err.strip()}

    async def scan(self, directory: str) -> list[dict]:
        s = _settings()
        s["last_dir"] = directory
        s["recent_dirs"] = ([directory] + [d for d in s.get("recent_dirs", []) if d != directory])[:5]
        _save_settings(s)
        groups = scanner.best_per_game(scanner.scan(Path(directory)))
        added = {g["exe"] for g in registry.load()["games"].values()}
        return [
            {"name": name, "exes": [{"path": str(c.exe), "already": str(c.exe) in added} for c in cands]}
            for name, cands in groups.items()
        ]

    async def list_locations(self) -> list[dict]:
        out, seen = [], set()

        def add(path, label):
            p = str(path)
            if p not in seen and Path(p).is_dir():
                seen.add(p)
                out.append({"path": p, "label": label})

        for d in _settings().get("recent_dirs", []):
            add(d, f"Recent: {Path(d).name}")
        for d in mounts.list_drives():
            if d.mountpoint:
                add(d.mountpoint, f"Drive: {d.label or d.name}")
        media = Path("/run/media") / decky.DECKY_USER
        if media.is_dir():
            for d in sorted(media.iterdir()):
                add(d, f"Drive: {d.name}")
        try:
            for lib in compat.library_dirs(_root())[1:]:
                add(lib, f"Steam library: {lib}")
        except Exception:
            pass
        return out

    async def list_compat_tools(self) -> list[dict]:
        tools = compat.list_tools(_root(), Path(decky.DECKY_USER_HOME))
        return tools + [{"id": "", "label": "None (set later)", "installed": True}]

    # Runs in the backend: the frontend goes away while Steam restarts.
    _job: dict = {"state": "idle"}

    async def job_status(self) -> dict:
        return self._job

    async def clear_job(self) -> None:
        if self._job.get("state") in ("done", "error"):
            Plugin._job = {"state": "idle"}

    async def apply_games(self, user: str, games: list[dict], tool: str) -> dict:
        if self._job.get("state") == "running":
            return {"ok": False, "error": "A job is already running"}
        Plugin._job = {"state": "running", "step": "Fetching artwork", "done": 0, "total": len(games)}
        asyncio.get_event_loop().run_in_executor(None, self._apply_sync, user, games, tool)
        return {"ok": True}

    def _apply_sync(self, user: str, games: list[dict], tool: str) -> None:
        try:
            key = _settings().get("sgdb_key")
            shortcuts, art, notes = [], {}, []
            for i, g in enumerate(games):
                Plugin._job.update(step=f"Artwork: {g['name']}", done=i)
                sc = steam.make_shortcut(g["name"], Path(g["exe"]))
                shortcuts.append(sc)
                if key:
                    try:
                        match = artwork.find_game(key, artwork.candidates(g["name"], Path(g["exe"])))
                    except artwork.ArtError as e:
                        notes.append(str(e))
                        key = None  # same error for every game; stop trying
                        continue
                    if match:
                        appid = sc["appid"] & 0xFFFFFFFF
                        art[appid] = artwork.fetch_assets(key, match["id"])
                        icon = artwork.fetch_icon(key, match["id"])
                        if icon:
                            sc["icon"] = _save_icon(appid, *icon)
                        if match["matched"] != g["name"]:
                            notes.append(f"{g['name']}: art matched '{match['matched']}'")
                    else:
                        notes.append(f"{g['name']}: no artwork found")

            Plugin._job.update(step="Restarting Steam", done=len(games))
            _wait_for_steam_exit()

            root = _root()
            added = steam.add_shortcuts(user, shortcuts, root)
            ids = [s["appid"] & 0xFFFFFFFF for s in added]
            if tool:
                compat.set_tools(root, {a: tool for a in ids})
            grid = root / "userdata" / user / "config" / "grid"
            for a in ids:
                if a in art:
                    artwork.write_grid(grid, a, art[a])
            _chown(steam.shortcuts_path(user, root), compat.config_path(root), grid)
            for s in added:
                registry.record(user, s)
            Plugin._job = {"state": "done", "added": len(added), "skipped": len(shortcuts) - len(added), "notes": notes}
        except Exception as e:
            decky.logger.exception("apply failed")
            Plugin._job = {"state": "error", "error": str(e)}

    async def remove_games(self, appids: list[int]) -> dict:
        if self._job.get("state") == "running":
            return {"ok": False, "error": "A job is already running"}
        Plugin._job = {"state": "running", "step": "Restarting Steam"}
        asyncio.get_event_loop().run_in_executor(None, self._remove_sync, appids)
        return {"ok": True}

    def _remove_sync(self, appids: list[int]) -> None:
        try:
            _wait_for_steam_exit()
            root = _root()
            games = registry.load()["games"]
            by_user: dict[str, set[int]] = {}
            for a in appids:
                by_user.setdefault(games.get(str(a), {}).get("user", ""), set()).add(a)
            for user, ids in by_user.items():
                if user:
                    steam.remove_shortcuts(user, {steam.to_signed(a) for a in ids}, root)
                    grid = root / "userdata" / user / "config" / "grid"
                    for a in ids:
                        artwork.remove_grid(grid, a)
                    _chown(steam.shortcuts_path(user, root))
            compat.set_tools(root, {a: "" for a in appids})
            _chown(compat.config_path(root))
            for a in appids:
                registry.forget(a)
            Plugin._job = {"state": "done", "removed": len(appids)}
        except Exception as e:
            decky.logger.exception("remove failed")
            Plugin._job = {"state": "error", "error": str(e)}

    async def fetch_art(self, title: str, exe: str, appid: int) -> dict:
        key = _settings().get("sgdb_key")
        if not key:
            return {"found": False, "reason": "no_key"}
        terms = artwork.candidates(title, Path(exe))
        try:
            game = await asyncio.get_event_loop().run_in_executor(None, artwork.find_game, key, terms)
        except artwork.ArtError as e:
            decky.logger.error("artwork for %s: %s", title, e)
            return {"found": False, "reason": "error", "error": str(e)}
        if not game:
            return {"found": False, "reason": "no_match", "tried": terms}
        return await self.art_by_id(game["id"], appid, game)

    async def search_art(self, term: str) -> dict:
        key = _settings().get("sgdb_key")
        if not key:
            return {"hits": [], "error": "No SteamGridDB key"}
        try:
            hits = await asyncio.get_event_loop().run_in_executor(None, artwork.search, key, term.strip())
            return {"hits": hits}
        except artwork.ArtError as e:
            return {"hits": [], "error": str(e)}

    async def art_by_id(self, game_id: int, appid: int, game: dict | None = None) -> dict:
        key = _settings().get("sgdb_key")
        if not key:
            return {"found": False, "reason": "no_key"}

        def run():
            assets = artwork.fetch_assets(key, game_id)
            icon = artwork.fetch_icon(key, game_id)
            return assets, (_save_icon(appid, *icon) if icon else "")

        try:
            assets, icon_path = await asyncio.get_event_loop().run_in_executor(None, run)
        except artwork.ArtError as e:
            decky.logger.error("artwork %s: %s", game_id, e)
            return {"found": False, "reason": "error", "error": str(e)}
        return {
            "found": bool(assets or icon_path),
            "name": (game or {}).get("name", ""),
            "matched": (game or {}).get("matched", ""),
            "icon": icon_path,
            "assets": [{"type": t, "ext": ext, "data": base64.b64encode(b).decode()}
                       for t, (b, ext) in assets.items()],
        }

    async def log_error(self, msg: str) -> None:
        decky.logger.error("frontend: %s", msg)

    async def record_added(self, appid: int, user: str, name: str, exe: str) -> None:
        registry.record(user, {"appid": appid, "AppName": name, "Exe": exe})

    async def list_added(self) -> list[dict]:
        return [{"appid": int(k), **v} for k, v in registry.load()["games"].items()]

    async def forget(self, appid: int) -> None:
        registry.forget(appid)

    def _link_all(self) -> dict:
        if not _settings().get("shared_saves", False):
            return {"disabled": True}
        parent, owner = _saves_parent(), _user_ids()
        busy = _prefixes_in_use()
        linked = moved = conflicts = waiting = running = 0
        notes = []
        for p in _prefixes():
            if not p["drive_c"].is_dir():
                waiting += 1  # Proton creates the prefix on first launch
                continue
            if os.path.realpath(p["data"]) in busy or os.path.realpath(p["data"] / "pfx") in busy:
                running += 1
                continue
            try:
                r = saves.link_prefix(p["drive_c"], parent, p["label"], owner)
            except Exception as e:
                decky.logger.exception("linking %s failed", p["label"])
                notes.append(f"{p['label']}: {e}")
                continue
            linked += bool(r.linked)
            moved += len(r.moved)
            conflicts += len(r.conflicts)
            notes += r.skipped
        return {"linked": linked, "moved": moved, "conflicts": conflicts,
                "waiting": waiting, "running": running, "notes": notes}

    async def saves_info(self) -> dict:
        parent = _saves_parent()
        games = []
        for p in _prefixes():
            exists = p["drive_c"].is_dir()
            games.append({"label": p["label"], "exists": exists,
                          "linked": exists and saves.is_linked(p["drive_c"], parent)})
        return {"root": str(saves.root_dir(parent)), "games": games, "backups": saves.list_backups(parent)}

    async def link_saves(self) -> dict:
        return await asyncio.get_event_loop().run_in_executor(None, self._link_all)

    async def backup_saves(self) -> dict:
        def run():
            parent = _saves_parent()
            saves.saves_dir(parent).mkdir(parents=True, exist_ok=True)
            out = saves.backup(parent)
            _chown(saves.root_dir(parent), saves.backups_dir(parent), out)
            return {"ok": True, "name": out.name, "size": out.stat().st_size}
        try:
            return await asyncio.get_event_loop().run_in_executor(None, run)
        except Exception as e:
            decky.logger.exception("backup failed")
            return {"ok": False, "error": str(e)}

    async def restore_saves(self, name: str) -> dict:
        if _prefixes_in_use():
            return {"ok": False, "error": "Close running games before restoring"}
        def run():
            parent = _saves_parent()
            safety = saves.restore(parent, name)
            saves._chown_tree(saves.root_dir(parent), _user_ids())
            return {"ok": True, "safety": safety.name}
        try:
            return await asyncio.get_event_loop().run_in_executor(None, run)
        except Exception as e:
            decky.logger.exception("restore failed")
            return {"ok": False, "error": str(e)}

    async def set_saves_parent(self, new_parent: str) -> dict:
        if _prefixes_in_use():
            return {"ok": False, "error": "Close running games first"}
        def run():
            saves.move_root(_saves_parent(), Path(new_parent))
            s = _settings()
            s["saves_parent"] = new_parent
            _save_settings(s)
            result = self._link_all()  # repoint every link at the new location
            return {"ok": True, "root": str(saves.root_dir(Path(new_parent))), **result}
        try:
            return await asyncio.get_event_loop().run_in_executor(None, run)
        except Exception as e:
            decky.logger.exception("move failed")
            return {"ok": False, "error": str(e)}


    # --- LAN sync ---------------------------------------------------------------------------

    async def lan_info(self) -> dict:
        n = _lan()
        s = _settings()
        return {"enabled": bool(s.get("lan_sync")) and bool(s.get("shared_saves")), "running": n.running,
                "name": n.state.name, "pairing_open": n.pairing_open(), "tracked": n.state.tracked,
                "peers": [{"id": i, "name": p["name"]} for i, p in n.state.peers.items()]}

    async def set_lan(self, on: bool) -> dict:
        s = _settings()
        s["lan_sync"] = on
        _save_settings(s)
        n = _lan()
        try:
            n.start() if on and s.get("shared_saves") else n.stop()
        except OSError as e:
            decky.logger.exception("lan start failed")
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    async def lan_set_name(self, name: str) -> None:
        n = _lan()
        n.state.name = name.strip() or n.state.name
        n.state.save()

    async def lan_pair_code(self) -> dict:
        n = _lan()
        if not n.running:
            return {"ok": False, "error": "Turn on LAN sync first"}
        return {"ok": True, "code": n.start_pairing(), "seconds": lan.PAIR_TTL}

    async def lan_scan(self) -> list[dict]:
        n = _lan()
        return await asyncio.get_event_loop().run_in_executor(None, n.discover)

    async def lan_pair(self, host: str, port: int, code: str) -> dict:
        n = _lan()
        try:
            r = await asyncio.get_event_loop().run_in_executor(None, n.pair_with, host, port, code)
            return {"ok": True, **r}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def lan_unpair(self, peer_id: str) -> None:
        _lan().unpair(peer_id)

    async def lan_folders(self, peer_id: str) -> dict:
        """Folders here and on the peer, merged: where each one is, and which look like game saves."""
        n = _lan()
        def run():
            rows = {c["entry"]: {**c, "where": "here"} for c in (lan.candidates(n.root) if n.root.is_dir() else [])}
            error = ""
            if peer_id:
                try:
                    for c in n.remote_candidates(peer_id):
                        if c["entry"] in rows:
                            rows[c["entry"]]["where"] = "both"
                        else:
                            rows[c["entry"]] = {**c, "where": "there"}
                except Exception as e:
                    error = f"Could not reach the other device: {e}"
            for entry in n.state.tracked:
                rows.setdefault(entry, {"entry": entry, "files": 0, "size": 0, "mtime": 0, "likely": False, "where": "none"})
            return {"folders": sorted(rows.values(), key=lambda c: (not c["likely"], c["entry"].lower())),
                    "tracked": n.state.tracked, "error": error}
        return await asyncio.get_event_loop().run_in_executor(None, run)

    async def lan_set_tracked(self, entries: list[str]) -> dict:
        try:
            return {"ok": True, "tracked": _lan().set_tracked(entries)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    async def lan_sync(self, peer_id: str, mode: str) -> dict:
        n = _lan()
        def run():
            try:
                return {"ok": True, **n.sync(peer_id, mode)}
            except Exception as e:
                decky.logger.exception("lan sync failed")
                return {"ok": False, "error": str(e)}
        return await asyncio.get_event_loop().run_in_executor(None, run)

    async def _main(self):
        decky.logger.info("SteamKit loaded")
        asyncio.get_event_loop().run_in_executor(None, self._link_all)
        s = _settings()
        if s.get("lan_sync") and s.get("shared_saves"):
            try:
                _lan().start()
            except OSError:
                decky.logger.exception("lan start failed")

    async def _unload(self):
        if _node:
            _node.stop()

    async def _uninstall(self):
        pass
