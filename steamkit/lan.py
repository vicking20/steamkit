"""Sync save folders between SteamKit devices on the local network. Standard library only.

Devices find each other with a UDP broadcast, pair once with a 6-digit code, then exchange
files over HTTP signed with a shared key. Only folders the user tracks are synced, and files
are never deleted.
"""
import hashlib
import hmac
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath

from . import saves

DISCOVERY_PORT = 47621
HTTP_PORT = 47622
MAGIC = b"STEAMKIT-LAN/1"
PAIR_TTL = 120          # seconds a pairing code stays valid
PAIR_ATTEMPTS = 5       # wrong codes before the code is thrown away
MAX_SKEW = 120          # seconds of clock difference tolerated on signed requests
MAX_BODY = 512 << 20
MODES = ("pull", "push", "newest")

# Folders (relative to Saves/) that tracked entries must live inside, never the folder itself.
TOP_FOLDERS = sorted(set(saves.USER_FOLDERS.values()) | set(saves.SHARED_FOLDERS.values()), key=len, reverse=True)
# Wine/Windows folders that are never game saves.
JUNK = saves.BACKUP_EXCLUDE | {"wine", "packages", "programs", "connecteddevicesplatform", "history", "fontcache",
                               "downloaded installations", "comms", "publishers", "placeholdertilelogofolder"}


# --- paths and manifests -------------------------------------------------------------------------

def _clean_parts(rel: str) -> tuple[str, ...]:
    if not rel or rel.startswith("/") or "\0" in rel or "\\" in rel:
        raise ValueError(f"bad path: {rel!r}")
    parts = PurePosixPath(rel).parts
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"bad path: {rel!r}")
    return parts


def _walk_checked(base: Path, parts: tuple[str, ...]) -> Path:
    cur = base
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            raise ValueError("symlinks are not synced")
    return cur


def entry_path(root: Path, entry: str) -> Path:
    """Saves/ folder for a tracked entry like 'Documents/My Games/Game'."""
    parts = _clean_parts(entry)
    if not any(entry.startswith(t + "/") for t in TOP_FOLDERS):
        raise ValueError("entry must be inside a save folder")
    return _walk_checked(root, parts)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(root: Path, entry: str) -> dict[str, dict]:
    base = entry_path(root, entry)
    out: dict[str, dict] = {}
    if not base.is_dir():
        return out
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not (Path(dirpath) / d).is_symlink()]
        for name in files:
            p = Path(dirpath) / name
            if p.is_symlink() or not p.is_file():
                continue
            st = p.stat()
            out[p.relative_to(base).as_posix()] = {"size": st.st_size, "mtime": int(st.st_mtime), "sha": _sha(p)}
    return out


def _is_likely(rel: str) -> bool:
    low = rel.lower()
    if low.startswith(("documents/my games/", "saved games/", "appdata/locallow/")) or "save" in low:
        return True
    return low.startswith("documents/") and low.count("/") == 1


def candidates(root: Path) -> list[dict]:
    """Folders that could hold one game's saves, with a guess at which ones really do."""
    out = []
    for top in TOP_FOLDERS:
        base = root / top
        if not base.is_dir() or base.is_symlink():
            continue
        for d in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if not d.is_dir() or d.is_symlink() or d.name.startswith(".") or d.name.lower() in JUNK:
                continue
            subs = [d]
            if top == "Documents" and d.name.lower() == "my games":
                subs = [s for s in sorted(d.iterdir()) if s.is_dir() and not s.is_symlink()]
            for s in subs:
                if s.name.lower() in JUNK:
                    continue
                files = size = newest = 0
                for dirpath, dirs, names in os.walk(s):
                    dirs[:] = [x for x in dirs if not (Path(dirpath) / x).is_symlink()]
                    for n in names:
                        try:
                            st = (Path(dirpath) / n).stat()
                        except OSError:
                            continue
                        files += 1
                        size += st.st_size
                        newest = max(newest, int(st.st_mtime))
                if not files:
                    continue
                rel = s.relative_to(root).as_posix()
                out.append({"entry": rel, "files": files, "size": size, "mtime": newest, "likely": _is_likely(rel)})
    out.sort(key=lambda c: (not c["likely"], c["entry"].lower()))
    return out


# --- sync planning -------------------------------------------------------------------------------

def plan(local: dict, remote: dict, mode: str) -> dict:
    """What to copy each way. Files that exist on both sides with different content are 'overwrite';
    equal timestamps with different content in 'newest' mode are conflicts and left alone."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    pull, push, conflicts = [], [], []
    for rel in sorted(set(local) | set(remote)):
        l, r = local.get(rel), remote.get(rel)
        if l and r and l["sha"] == r["sha"]:
            continue
        if mode == "pull":
            if r:
                pull.append(rel)
        elif mode == "push":
            if l:
                push.append(rel)
        elif not l:
            pull.append(rel)
        elif not r:
            push.append(rel)
        elif r["mtime"] > l["mtime"]:
            pull.append(rel)
        elif l["mtime"] > r["mtime"]:
            push.append(rel)
        else:
            conflicts.append(rel)
    overwrite = [rel for rel in pull if rel in local] + [rel for rel in push if rel in remote]
    return {"pull": pull, "push": push, "conflicts": conflicts, "overwrite": overwrite}


def write_file(base: Path, rel: str, data: bytes, mtime: int, owner: tuple[int, int] | None) -> None:
    """Write below base, refusing to follow symlinks, keeping the source's mtime."""
    parts = _clean_parts(rel)
    target = _walk_checked(base, parts)
    created = []
    p = base
    while not p.exists():  # a fresh device has none of the game's folders yet
        created.append(p)
        p = p.parent
    cur = base
    for part in parts[:-1]:
        cur = cur / part
        if not cur.exists():
            created.append(cur)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".steamkit-tmp")
    tmp.write_bytes(data)
    os.utime(tmp, (mtime, mtime))
    tmp.replace(target)
    if owner:
        for p in [*created, target]:
            os.lchown(p, *owner)


# --- state ---------------------------------------------------------------------------------------

class State:
    """Device identity, paired peers and tracked folders, kept in one JSON file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        data = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.id = data.get("id") or secrets.token_hex(8)
        self.name = data.get("name") or socket.gethostname()
        self.peers: dict[str, dict] = data.get("peers", {})   # id -> {name, key, host, port}
        self.tracked: list[str] = data.get("tracked", [])
        self.save()

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"id": self.id, "name": self.name, "peers": self.peers,
                                       "tracked": self.tracked}, indent=2))
            os.chmod(tmp, 0o600)  # holds pairing keys
            tmp.replace(self.path)


def _sign(key: bytes, method: str, target: str, ts: str, body_sha: str) -> str:
    return hmac.new(key, f"{method}\n{target}\n{ts}\n{body_sha}".encode(), hashlib.sha256).hexdigest()


def _stretch(code: str, nonce: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", code.encode(), nonce.encode(), 100_000)


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


class PairingError(Exception):
    pass


# --- node: server + client -----------------------------------------------------------------------

class Node:
    def __init__(self, state_path: Path, parent: Path, owner: tuple[int, int] | None = None,
                 http_port: int = HTTP_PORT, discovery_port: int = DISCOVERY_PORT, busy=None):
        self.state = State(state_path)
        self.parent = Path(parent)
        self.owner = owner
        self.busy = busy  # callable -> True while a game is running; writes are refused then
        self.http_port = http_port
        self.discovery_port = discovery_port
        self._httpd: ThreadingHTTPServer | None = None
        self._udp: socket.socket | None = None
        self._pair: dict | None = None
        self._last_backup = 0.0
        self._lock = threading.Lock()

    # paths
    @property
    def root(self) -> Path:
        return saves.saves_dir(self.parent)

    def set_parent(self, parent: Path) -> None:
        self.parent = Path(parent)

    # lifecycle
    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> None:
        if self.running:
            return
        self._httpd = ThreadingHTTPServer(("", self.http_port), _make_handler(self))
        self._httpd.daemon_threads = True
        self.http_port = self._httpd.server_address[1]  # differs from the request only for port 0 (tests)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.bind(("", self.discovery_port))
        self.discovery_port = udp.getsockname()[1]
        self._udp = udp
        threading.Thread(target=self._answer_discovery, args=(udp,), daemon=True).start()

    def stop(self) -> None:
        self._pair = None
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._udp:
            self._udp.close()
            self._udp = None

    def _answer_discovery(self, udp: socket.socket) -> None:
        while True:
            try:
                data, addr = udp.recvfrom(512)
            except OSError:
                return  # socket closed
            if data == MAGIC + b"?":
                reply = json.dumps({"id": self.state.id, "name": self.state.name, "port": self.http_port})
                try:
                    udp.sendto(MAGIC + b"!" + reply.encode(), addr)
                except OSError:
                    pass

    # pairing, host side
    def start_pairing(self) -> str:
        code = f"{secrets.randbelow(10 ** 6):06d}"
        self._pair = {"code": code, "expires": time.time() + PAIR_TTL, "attempts": 0}
        return code

    def pairing_open(self) -> bool:
        p = self._pair
        return bool(p) and p["expires"] > time.time() and p["attempts"] < PAIR_ATTEMPTS

    def _accept_pair(self, body: dict) -> dict:
        with self._lock:
            p = self._pair
            if not self.pairing_open():
                raise PairingError("pairing is not open on this device")
            stretched = _stretch(p["code"], body["nonce"])
            expected = hmac.new(stretched, f"pair|{body['id']}".encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, body["proof"]):
                p["attempts"] += 1
                raise PairingError("wrong code")
            self._pair = None
            key = secrets.token_bytes(32)
            pad = hmac.new(stretched, b"key", hashlib.sha256).digest()
            self.state.peers[body["id"]] = {"name": body["name"], "key": key.hex(), "host": "", "port": 0}
            self.state.save()
            return {"id": self.state.id, "name": self.state.name, "key": _xor(key, pad).hex(),
                    "proof": hmac.new(stretched, b"srv|" + key.hex().encode(), hashlib.sha256).hexdigest()}

    # discovery and pairing, client side
    def discover(self, timeout: float = 1.5, hosts: tuple[str, ...] = ()) -> list[dict]:
        """Devices answering on the LAN. 'hosts' adds explicit addresses for networks that block broadcast."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.2)
        for target in ("255.255.255.255", *hosts):
            try:
                sock.sendto(MAGIC + b"?", (target, self.discovery_port))
            except OSError:
                pass
        found: dict[str, dict] = {}
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data.startswith(MAGIC + b"!"):
                continue
            try:
                info = json.loads(data[len(MAGIC) + 1:])
            except ValueError:
                continue
            if info["id"] == self.state.id:
                continue
            peer = {"id": info["id"], "name": info["name"], "host": addr[0], "port": int(info["port"]),
                    "paired": info["id"] in self.state.peers}
            found[info["id"]] = peer
            if peer["paired"]:
                self.state.peers[info["id"]].update(host=peer["host"], port=peer["port"], name=peer["name"])
        sock.close()
        if found:
            self.state.save()
        return sorted(found.values(), key=lambda p: p["name"].lower())

    def pair_with(self, host: str, port: int, code: str) -> dict:
        nonce = secrets.token_hex(8)
        stretched = _stretch(code.strip(), nonce)
        body = {"id": self.state.id, "name": self.state.name, "nonce": nonce,
                "proof": hmac.new(stretched, f"pair|{self.state.id}".encode(), hashlib.sha256).hexdigest()}
        try:
            resp = json.loads(_http("POST", f"http://{host}:{port}/pair", json.dumps(body).encode()))
        except urllib.error.HTTPError as e:
            with e:
                raise PairingError(json.loads(e.read() or b"{}").get("error", f"HTTP {e.code}"))
        key = _xor(bytes.fromhex(resp["key"]), hmac.new(stretched, b"key", hashlib.sha256).digest())
        check = hmac.new(stretched, b"srv|" + key.hex().encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(check, resp["proof"]):
            raise PairingError("the other device did not prove it knows the code")
        self.state.peers[resp["id"]] = {"name": resp["name"], "key": key.hex(), "host": host, "port": int(port)}
        self.state.save()
        return {"id": resp["id"], "name": resp["name"]}

    def unpair(self, peer_id: str) -> None:
        self.state.peers.pop(peer_id, None)
        self.state.save()

    # tracked folders
    def set_tracked(self, entries: list[str]) -> list[str]:
        clean = []
        for e in entries:
            entry_path(self.root, e)  # validates
            if e not in clean:
                clean.append(e)
        self.state.tracked = clean
        self.state.save()
        return clean

    # client calls to a paired peer
    def _call(self, peer_id: str, method: str, target: str, body: bytes = b"") -> bytes:
        peer = self.state.peers.get(peer_id)
        if not peer or not peer.get("host"):
            raise ConnectionError("peer not found on the network; run a scan first")
        ts = str(int(time.time()))
        body_sha = hashlib.sha256(body).hexdigest()
        headers = {"X-Peer": self.state.id, "X-Ts": ts, "X-Body-Sha256": body_sha,
                   "X-Sig": _sign(bytes.fromhex(peer["key"]), method, target, ts, body_sha)}
        return _http(method, f"http://{peer['host']}:{peer['port']}{target}", body if method == "PUT" else None, headers)

    def remote_candidates(self, peer_id: str) -> list[dict]:
        return json.loads(self._call(peer_id, "GET", "/candidates"))

    def remote_manifest(self, peer_id: str, entry: str) -> dict:
        return json.loads(self._call(peer_id, "GET", "/manifest?" + urllib.parse.urlencode({"entry": entry})))

    def sync(self, peer_id: str, mode: str, entries: list[str] | None = None) -> dict:
        """Sync tracked folders (or the given ones) with a paired peer. Backs up before overwriting."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if self.busy and self.busy():
            raise RuntimeError("Close running games before syncing")
        report = {"pulled": 0, "pushed": 0, "overwritten": 0, "conflicts": [], "entries": {}, "backup": None}
        for entry in entries if entries is not None else list(self.state.tracked):
            local = manifest(self.root, entry)
            remote = self.remote_manifest(peer_id, entry)
            p = plan(local, remote, mode)
            if p["overwrite"] and not report["backup"]:
                self.root.mkdir(parents=True, exist_ok=True)
                report["backup"] = saves.backup(self.parent, "before-lan-sync").name
            base = entry_path(self.root, entry)
            q = urllib.parse.urlencode
            for rel in p["pull"]:
                data = self._call(peer_id, "GET", "/file?" + q({"entry": entry, "f": rel}))
                if hashlib.sha256(data).hexdigest() != remote[rel]["sha"]:
                    raise IOError(f"{entry}/{rel} changed during transfer; try again")
                write_file(base, rel, data, remote[rel]["mtime"], self.owner)
            for rel in p["push"]:
                data = (base / rel).read_bytes()
                self._call(peer_id, "PUT", "/file?" + q({"entry": entry, "f": rel, "mtime": local[rel]["mtime"]}), data)
            report["pulled"] += len(p["pull"])
            report["pushed"] += len(p["push"])
            report["overwritten"] += len(p["overwrite"])
            report["conflicts"] += [f"{entry}/{c}" for c in p["conflicts"]]
            report["entries"][entry] = {"pulled": len(p["pull"]), "pushed": len(p["push"])}
        return report

    def _backup_for_remote_write(self) -> None:
        """Called on the receiving side before a pushed file replaces an existing one."""
        with self._lock:
            if time.time() - self._last_backup > 600:
                self.root.mkdir(parents=True, exist_ok=True)
                saves.backup(self.parent, "before-lan-sync")
                self._last_backup = time.time()


# --- http plumbing -------------------------------------------------------------------------------

def _http(method: str, url: str, body: bytes | None, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def _make_handler(node: Node):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, payload: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode())

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("body too large")
            return self.rfile.read(n) if n else b""

        def _authed(self, body: bytes) -> bool:
            peer = node.state.peers.get(self.headers.get("X-Peer", ""))
            ts = self.headers.get("X-Ts", "")
            if not peer or not ts.isdigit() or abs(time.time() - int(ts)) > MAX_SKEW:
                return False
            body_sha = hashlib.sha256(body).hexdigest()
            if body_sha != self.headers.get("X-Body-Sha256"):
                return False
            want = _sign(bytes.fromhex(peer["key"]), self.command, self.path, ts, body_sha)
            return hmac.compare_digest(want, self.headers.get("X-Sig", ""))

        def _handle(self) -> None:
            try:
                body = self._body()
                url = urllib.parse.urlsplit(self.path)
                qs = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
                if url.path == "/pair" and self.command == "POST":
                    try:
                        return self._json(200, node._accept_pair(json.loads(body)))
                    except PairingError as e:
                        return self._json(403, {"error": str(e)})
                if not self._authed(body):
                    return self._json(401, {"error": "not paired"})
                root = node.root
                if url.path == "/candidates" and self.command == "GET":
                    return self._json(200, candidates(root))
                if url.path == "/manifest" and self.command == "GET":
                    return self._json(200, manifest(root, qs["entry"]))
                if url.path == "/file":
                    base = entry_path(root, qs["entry"])
                    target = _walk_checked(base, _clean_parts(qs["f"]))
                    if self.command == "GET":
                        if not target.is_file():
                            return self._json(404, {"error": "no such file"})
                        return self._send(200, target.read_bytes(), "application/octet-stream")
                    if self.command == "PUT":
                        if node.busy and node.busy():
                            return self._json(409, {"error": "a game is running on the other device"})
                        if target.exists():
                            node._backup_for_remote_write()
                        write_file(base, qs["f"], body, int(qs["mtime"]), node.owner)
                        return self._json(200, {"ok": True})
                self._json(404, {"error": "unknown request"})
            except (ValueError, KeyError) as e:
                self._json(400, {"error": str(e)})
            except Exception as e:  # keep the server alive whatever a request does
                self._json(500, {"error": str(e)})

        do_GET = do_POST = do_PUT = _handle

    return Handler
