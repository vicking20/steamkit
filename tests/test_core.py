import os
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from steamkit import artwork, binvdf, compat, mounts, scanner, steam, textvdf


class TestBinVdf(unittest.TestCase):
    def test_roundtrip(self):
        obj = {"shortcuts": {"0": {"appid": -123, "AppName": "Game", "tags": {"0": "x"}}}}
        self.assertEqual(binvdf.loads(binvdf.dumps(obj)), obj)


class TestSteam(unittest.TestCase):
    def test_appid_known_value(self):
        a = steam.shortcut_appid('"/games/x.exe"', "X")
        self.assertTrue(a & 0x80000000)
        self.assertLess(steam.to_signed(a), 0)

    def test_shortcut_serializes(self):
        s = steam.make_shortcut("Game", Path("/g/Game/game.exe"))
        out = binvdf.loads(binvdf.dumps({"shortcuts": {"0": s}}))
        self.assertEqual(out["shortcuts"]["0"]["Exe"], '"/g/Game/game.exe"')


class TestScanner(unittest.TestCase):
    def test_filters_junk_and_ranks(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            for rel in ["Hollow Knight/hollow_knight.exe", "Hollow Knight/UnityCrashHandler64.exe",
                        "Hollow Knight/unins000.exe", "Celeste/_CommonRedist/vcredist_x64.exe",
                        "Celeste/Celeste.exe", "Celeste/tools/editor.exe"]:
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"x")
            groups = scanner.best_per_game(scanner.scan(root))
            self.assertEqual(list(groups), ["Celeste", "Hollow Knight"])
            self.assertEqual(groups["Celeste"][0].exe.name, "Celeste.exe")
            self.assertEqual([c.exe.name for c in groups["Hollow Knight"]], ["hollow_knight.exe"])

    def test_picking_game_folder_names_by_folder_not_exe(self):
        with TemporaryDirectory() as d:
            game = Path(d) / "Resident Evil 6"
            game.mkdir()
            (game / "BH6.exe").write_bytes(b"x")
            groups = scanner.best_per_game(scanner.scan(game))
            self.assertEqual(list(groups), ["Resident Evil 6"])

    def test_depth_limit_prunes(self):
        with TemporaryDirectory() as d:
            deep = Path(d) / "Game" / "a" / "b" / "c" / "d" / "e" / "f"
            deep.mkdir(parents=True)
            (deep / "deep.exe").write_bytes(b"x")
            (Path(d) / "Game" / "game.exe").write_bytes(b"x")
            names = [c.exe.name for c in scanner.scan(Path(d), max_depth=4)]
            self.assertEqual(names, ["game.exe"])


class TestMounts(unittest.TestCase):
    def test_fstab_line(self):
        d = mounts.Drive("sdb1", "abcd-1234", "ntfs", "Games", "1T", None)
        line = mounts.fstab_line(d, "/mnt/Games", 1000, 1000)
        self.assertIn("ntfs3", line)
        self.assertIn("nofail", line)
        self.assertIn("uid=1000", line)

    def test_in_fstab_recognises_gnome_disks_entries(self):
        from unittest import mock
        with TemporaryDirectory() as d:
            f = Path(d) / "fstab"
            f.write_text("/dev/disk/by-uuid/ABCD-1234 /mnt/x exfat nosuid,nodev,nofail 0 0\n"
                         "# UUID=dead-beef /mnt/y ext4 defaults 0 0\n")
            with mock.patch.object(mounts, "FSTAB", f):
                self.assertTrue(mounts.in_fstab("abcd-1234"))
                self.assertFalse(mounts.in_fstab("dead-beef"))  # commented out


class TestArtwork(unittest.TestCase):
    def test_clean(self):
        self.assertEqual(artwork.clean("Hollow_Knight v1.5.78 [FitGirl Repack]"), "Hollow Knight")
        self.assertEqual(artwork.clean("DarkSoulsIII"), "Dark Souls III")

    def test_folder_first_then_exe(self):
        terms = artwork.candidates("Sekiro Shadows Die Twice", Path("/g/Sekiro Shadows Die Twice/sekiro.exe"))
        self.assertEqual(terms[0], "Sekiro Shadows Die Twice")
        self.assertIn("sekiro", terms)


class TestCompat(unittest.TestCase):
    def test_official_names(self):
        self.assertEqual(compat.official_name("Proton 9.0 (Beta)"), "proton_9")
        self.assertEqual(compat.official_name("Proton 6.3"), "proton_63")
        self.assertEqual(compat.official_name("Proton - Experimental"), "proton_experimental")
        self.assertIsNone(compat.official_name("Proton EasyAntiCheat Runtime"))

    def test_set_tools_preserves_config(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / "config").mkdir()
            (root / "config/config.vdf").write_text(
                '"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"valve"\n\t\t{\n\t\t\t"Steam"\n'
                '\t\t\t{\n\t\t\t\t"AutoUpdateWindowEnabled"\t\t"0"\n\t\t\t}\n\t\t}\n\t}\n}\n')
            compat.set_tools(root, {3000000001: "proton_9"})
            data = textvdf.loads((root / "config/config.vdf").read_text())
            steam_node = data["InstallConfigStore"]["Software"]["valve"]["Steam"]
            self.assertEqual(steam_node["AutoUpdateWindowEnabled"], "0")
            self.assertEqual(steam_node["CompatToolMapping"]["3000000001"]["name"], "proton_9")


if __name__ == "__main__":
    unittest.main()


class TestApplyFlow(unittest.TestCase):
    """Mirrors what the Decky backend does after Steam has exited."""

    def test_add_set_tool_write_art_then_remove(self):
        from unittest import mock
        with TemporaryDirectory() as d, mock.patch.object(steam, "is_running", return_value=False):
            root = Path(d)
            (root / "userdata/123/config").mkdir(parents=True)
            sc = steam.make_shortcut("Celeste", Path("/g/Celeste/Celeste.exe"))
            added = steam.add_shortcuts("123", [sc], root)
            self.assertEqual(len(steam.add_shortcuts("123", [sc], root)), 0)  # no duplicates
            appid = added[0]["appid"] & 0xFFFFFFFF
            compat.set_tools(root, {appid: "proton_9"})
            grid = root / "userdata/123/config/grid"
            artwork.write_grid(grid, appid, {artwork.GRID: (b"img", "png"), artwork.HERO: (b"img", "jpg")})
            self.assertTrue((grid / f"{appid}p.png").exists())
            self.assertTrue((grid / f"{appid}_hero.jpg").exists())

            steam.remove_shortcuts("123", {steam.to_signed(appid)}, root)
            compat.set_tools(root, {appid: ""})
            artwork.remove_grid(grid, appid)
            self.assertEqual(steam.load_shortcuts("123", root), {})
            self.assertEqual(list(grid.iterdir()), [])


class TestSaves(unittest.TestCase):
    def _prefix(self, base: Path, user="steamuser") -> Path:
        drive_c = base / "pfx/drive_c"
        (drive_c / f"users/{user}/AppData/Roaming").mkdir(parents=True)
        (drive_c / f"users/{user}/Documents").mkdir(parents=True)
        (drive_c / "users/Public/Documents").mkdir(parents=True)
        return drive_c

    def test_link_moves_existing_saves_and_is_idempotent(self):
        from steamkit import saves
        with TemporaryDirectory() as d:
            base, parent = Path(d), Path(d) / "home"
            drive_c = self._prefix(base)
            game_save = drive_c / "users/steamuser/Documents/My Games/Blur/save.dat"
            game_save.parent.mkdir(parents=True)
            game_save.write_text("progress")

            res = saves.link_prefix(drive_c, parent, "Blur")
            central = saves.saves_dir(parent) / "Documents/My Games/Blur/save.dat"
            self.assertEqual(central.read_text(), "progress")
            self.assertTrue((drive_c / "users/steamuser/Documents").is_symlink())
            self.assertTrue((drive_c / "users/Public/Documents").is_symlink())
            self.assertEqual(game_save.read_text(), "progress")  # still reachable through the link
            self.assertTrue(saves.is_linked(drive_c, parent))
            self.assertGreater(len(res.linked), 0)
            self.assertEqual(saves.link_prefix(drive_c, parent, "Blur").linked, [])

    def test_two_prefixes_share_saves_and_conflicts_are_kept(self):
        from steamkit import saves
        with TemporaryDirectory() as d:
            parent = Path(d) / "home"
            a = self._prefix(Path(d) / "a")
            b = self._prefix(Path(d) / "b")
            (a / "users/steamuser/Documents/cfg.ini").write_text("A")
            (b / "users/steamuser/Documents/cfg.ini").write_text("B")
            saves.link_prefix(a, parent, "GameA")
            saves.link_prefix(b, parent, "GameB")
            docs = saves.saves_dir(parent) / "Documents"
            self.assertEqual((docs / "cfg.ini").read_text(), "A")
            self.assertEqual((docs / "cfg.ini (from GameB)").read_text(), "B")
            (a / "users/steamuser/Documents/new.sav").write_text("x")
            self.assertTrue((b / "users/steamuser/Documents/new.sav").exists())

    def test_desktop_wine_documents_symlink_is_repointed_not_emptied(self):
        from steamkit import saves
        with TemporaryDirectory() as d:
            real_docs = Path(d) / "Documents"
            real_docs.mkdir()
            (real_docs / "taxes.pdf").write_text("keep me")
            drive_c = Path(d) / ".wine/drive_c"
            (drive_c / "users/deck").mkdir(parents=True)
            (drive_c / "users/deck/Documents").symlink_to(real_docs)
            res = saves.link_prefix(drive_c, Path(d) / "home", "desktop")
            self.assertTrue((real_docs / "taxes.pdf").exists())
            self.assertEqual(len(res.skipped), 1)

    def test_backup_restore_roundtrip(self):
        from steamkit import saves
        with TemporaryDirectory() as d:
            parent = Path(d)
            f = saves.saves_dir(parent) / "Documents/game.sav"
            f.parent.mkdir(parents=True)
            f.write_text("v1")
            (saves.saves_dir(parent) / "AppData/Local/D3DSCache").mkdir(parents=True)
            (saves.saves_dir(parent) / "AppData/Local/D3DSCache/big.bin").write_text("cache")
            name = saves.backup(parent).name
            f.write_text("v2")
            saves.restore(parent, name)
            self.assertEqual(f.read_text(), "v1")
            self.assertFalse((saves.saves_dir(parent) / "AppData/Local/D3DSCache").exists())
            self.assertTrue(any("before-restore" in b["name"] for b in saves.list_backups(parent)))


class TestLan(unittest.TestCase):
    def setUp(self):
        from steamkit import lan, saves
        self.lan, self.saves = lan, saves
        self.tmp = TemporaryDirectory()
        t = Path(self.tmp.name)
        self.a = lan.Node(t / "a.json", t / "A", http_port=47701, discovery_port=47711)
        self.b = lan.Node(t / "b.json", t / "B", http_port=47702, discovery_port=47712)
        for n in (self.a, self.b):
            n.root.mkdir(parents=True)
        self.a.start()
        self.b.start()
        self.addCleanup(self.a.stop)
        self.addCleanup(self.b.stop)
        self.addCleanup(self.tmp.cleanup)

    def put(self, node, rel, text, mtime):
        p = node.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        os.utime(p, (mtime, mtime))

    def pair(self):
        code = self.a.start_pairing()
        self.b.pair_with("127.0.0.1", 47701, code)
        return next(iter(self.b.state.peers))

    def test_pairing_rejects_wrong_code_and_locks_out(self):
        self.a.start_pairing()
        for _ in range(self.lan.PAIR_ATTEMPTS):
            with self.assertRaises(self.lan.PairingError):
                self.b.pair_with("127.0.0.1", 47701, "000x00")
        self.assertFalse(self.a.pairing_open())
        self.assertEqual(self.b.state.peers, {})

    def test_unpaired_requests_are_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen("http://127.0.0.1:47701/candidates")
        self.assertEqual(cm.exception.code, 401)

    def test_pair_then_pull_and_newest(self):
        self.put(self.a, "Documents/My Games/Racer/slot1.sav", "from A", 2000)
        self.put(self.a, "Documents/My Games/Racer/shared.sav", "A newer", 3000)
        self.put(self.b, "Documents/My Games/Racer/shared.sav", "B older", 1000)
        self.put(self.b, "Documents/My Games/Racer/only_b.sav", "B only", 1500)
        pid = self.pair()
        self.b.set_tracked(["Documents/My Games/Racer"])
        r = self.b.sync(pid, "newest")
        self.assertEqual((r["pulled"], r["pushed"], r["overwritten"]), (2, 1, 1))
        self.assertIsNotNone(r["backup"])
        for node in (self.a, self.b):
            base = node.root / "Documents/My Games/Racer"
            self.assertEqual((base / "shared.sav").read_text(), "A newer")
            self.assertEqual((base / "slot1.sav").read_text(), "from A")
            self.assertEqual((base / "only_b.sav").read_text(), "B only")
        self.assertEqual(self.b.sync(pid, "newest")["pulled"], 0)

    def test_fresh_device_gets_missing_folders(self):
        self.put(self.a, "Documents/My Games/NFS/deep/p.sav", "x", 1000)
        self.assertFalse((self.b.root / "Documents").exists())
        self.b.sync(self.pair(), "pull", ["Documents/My Games/NFS"])
        self.assertEqual((self.b.root / "Documents/My Games/NFS/deep/p.sav").read_text(), "x")

    def test_pull_mode_never_pushes_and_push_never_pulls(self):
        self.put(self.a, "Saved Games/G/a.sav", "a", 1000)
        self.put(self.b, "Saved Games/G/b.sav", "b", 1000)
        pid = self.pair()
        self.b.sync(pid, "pull", ["Saved Games/G"])
        self.assertTrue((self.b.root / "Saved Games/G/a.sav").exists())
        self.assertFalse((self.a.root / "Saved Games/G/b.sav").exists())

    def test_equal_mtime_different_content_is_a_conflict(self):
        self.put(self.a, "Saved Games/G/x.sav", "one", 1000)
        self.put(self.b, "Saved Games/G/x.sav", "two", 1000)
        r = self.b.sync(self.pair(), "newest", ["Saved Games/G"])
        self.assertEqual(r["conflicts"], ["Saved Games/G/x.sav"])
        self.assertEqual((self.b.root / "Saved Games/G/x.sav").read_text(), "two")

    def test_rejects_paths_outside_save_folders(self):
        for bad in ["../x", "Documents", "Documents/../../etc", "/etc", "Other/Game", "Documents/a\\b"]:
            with self.assertRaises(ValueError, msg=bad):
                self.lan.entry_path(self.a.root, bad)

    def test_symlinks_are_not_followed(self):
        outside = Path(self.tmp.name) / "secret"
        outside.mkdir()
        (outside / "pw.txt").write_text("secret")
        game = self.a.root / "Documents/G"
        game.mkdir(parents=True)
        (game / "link").symlink_to(outside, target_is_directory=True)
        (game / "real.sav").write_text("x")
        self.assertEqual(list(self.lan.manifest(self.a.root, "Documents/G")), ["real.sav"])

    def test_candidates_mark_likely_and_skip_junk(self):
        self.put(self.a, "Documents/My Games/Racer/s.sav", "x", 1000)
        self.put(self.a, "AppData/Local/Microsoft/junk", "x", 1000)
        self.put(self.a, "AppData/Roaming/SomeLauncher/cfg", "x", 1000)
        self.put(self.a, "AppData/LocalLow/Studio/Game/s.dat", "x", 1000)
        got = {c["entry"]: c["likely"] for c in self.lan.candidates(self.a.root)}
        self.assertEqual(got, {"Documents/My Games/Racer": True, "AppData/Roaming/SomeLauncher": False,
                               "AppData/LocalLow/Studio": True})

    def test_discover_finds_running_peer(self):
        self.b.discovery_port = 47711  # b queries a's discovery port
        found = self.b.discover(timeout=1, hosts=("127.0.0.1",))
        self.assertEqual([p["id"] for p in found], [self.a.state.id])
        self.assertEqual(found[0]["port"], 47701)
