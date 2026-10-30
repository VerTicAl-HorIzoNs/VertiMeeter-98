"""Spawns the native engine and talks to it over a unix socket (never on the GUI thread)."""
from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import threading
import time
from pathlib import Path

from .log import log
from .plat import IS_WIN
from .pwutil import die_with_parent

ROOT = Path(__file__).resolve().parents[2]
EXE = "vertimeeter-engine" + (".exe" if IS_WIN else "")


def engine_path() -> str | None:
    cands = [os.environ.get("VERTIMEETER_ENGINE", ""),
             str(ROOT / "engine/target/release" / EXE),
             str(ROOT / "bin" / EXE)]
    return next((c for c in cands if c and os.access(c, os.X_OK)), None)


def sock_path() -> str:
    if os.environ.get("VERTIMEETER_SOCK"):
        return os.environ["VERTIMEETER_SOCK"]
    if IS_WIN:  # a file holding the engine's loopback port (the engine binds a free one)
        from .config import CONFIG_DIR  # noqa: PLC0415
        return os.path.join(CONFIG_DIR, "engine.port")
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "vertimeeter-engine.sock")


class EngineClient:
    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.sock_file = None
        self.q: "queue.Queue[dict]" = queue.Queue()
        self.meters: dict = {}
        self.ok = False
        self._run = False
        self._t: threading.Thread | None = None
        self.host = "127.0.0.1"  # the engine's own loopback control port (Windows only; Linux uses a unix socket)
        self.token = ""

    def _connect_unix(self, path: str):
        for _ in range(60):
            if os.path.exists(path):
                break
            if self.proc.poll() is not None:
                log("ERROR", "engine exited: " + (self.proc.stderr.read() or "").strip())
                return None
            time.sleep(0.1)
        s = socket.socket(socket.AF_UNIX)
        s.connect(path)
        return s.makefile("rw")

    def _connect_tcp(self, path: str):
        """Windows: the engine writes its port to `path`, and wants `AUTH <token>` first (any local program can reach
        a loopback port, so the token is what keeps other programs out of the mixer)."""
        for _ in range(100):
            if os.path.exists(path):
                break
            if self.proc.poll() is not None:
                log("ERROR", "engine exited: " + (self.proc.stderr.read() or "").strip())
                return None
            time.sleep(0.1)
        else:
            log("ERROR", "engine did not publish its control port")
            return None
        s = socket.create_connection((self.host, int(open(path).read().strip())), timeout=5)
        s.settimeout(None)
        f = s.makefile("rw")
        f.write(f"AUTH {self.token}\n")
        f.flush()
        if not json.loads(f.readline() or "{}").get("ok"):
            log("ERROR", "engine refused the control token")
            return None
        return f

    def start(self) -> bool:
        exe = engine_path()
        if not exe:
            log("ERROR", "engine binary not found - run ./install.sh (cargo build --release in engine/)")
            return False
        path = sock_path()
        env = dict(os.environ)
        env.pop("PIPEWIRE_LATENCY", None)  # never force a latency on the engine
        if IS_WIN:
            import secrets  # noqa: PLC0415
            os.makedirs(os.path.dirname(path), exist_ok=True)
            try:
                os.remove(path)
            except OSError:
                pass
            self.token = secrets.token_hex(16)
            env["VERTIMEETER_TOKEN"] = self.token
            self.proc = subprocess.Popen([exe, "tcp:" + path], env=env, stderr=subprocess.PIPE, text=True)
            self.sock_file = self._connect_tcp(path)
            if self.sock_file is None:
                return False
        else:
            self.proc = subprocess.Popen(die_with_parent([exe, path]), env=env, stderr=subprocess.PIPE, text=True)
            self.sock_file = self._connect_unix(path)
            if self.sock_file is None:
                return False
        self.ok = True
        self._run = True
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        log("OK", f"engine started (pid {self.proc.pid})")
        return True

    def send(self, **cmd) -> None:
        self.q.put(cmd)

    def call(self, timeout: float = 3.0, **cmd) -> dict:
        """Send a command from any thread and wait for the engine's reply."""
        box: dict = {"ev": threading.Event(), "r": {"ok": False, "err": "timeout"}}
        self.q.put((cmd, box))
        box["ev"].wait(timeout)
        return box["r"]

    def _rpc(self, cmd: dict) -> dict:
        self.sock_file.write(json.dumps(cmd) + "\n")
        self.sock_file.flush()
        return json.loads(self.sock_file.readline() or "{}")

    def _loop(self) -> None:
        """Commands are batched and merged (a fader drag sends hundreds of gain values; only the newest matters),
        and meters are polled on a fixed 30 Hz clock no matter how busy the command queue is."""
        last = 0.0
        while self._run:
            try:
                try:
                    first = self.q.get(timeout=0.01)
                except queue.Empty:
                    first = None
                if first is not None:
                    batch = [first]
                    while len(batch) < 256:
                        try:
                            batch.append(self.q.get_nowait())
                        except queue.Empty:
                            break
                    merged: dict = {}
                    out: list = []
                    for cmd in batch:
                        if isinstance(cmd, tuple):
                            out.append(cmd)
                        elif cmd.get("c") in ("strip", "bus") and "i" in cmd:
                            key = (cmd["c"], cmd["i"])
                            if key in merged:
                                merged[key].update(cmd)
                            else:
                                merged[key] = dict(cmd)
                                out.append(merged[key])
                        else:
                            out.append(cmd)
                    for cmd in out:
                        if isinstance(cmd, tuple):
                            cmd[1]["r"] = self._rpc(cmd[0])
                            cmd[1]["ev"].set()
                        else:
                            self._rpc(cmd)
            except Exception as e:  # noqa: BLE001
                if self._run:
                    log("ERROR", f"engine link lost: {e}")
                self.ok = False
                return
            if time.time() - last >= 0.033:
                last = time.time()
                try:
                    self.meters = self._rpc({"c": "meters"})
                except Exception as e:  # noqa: BLE001
                    if self._run:
                        log("ERROR", f"engine link lost: {e}")
                    self.ok = False
                    return

    def stop(self) -> None:
        self._run = False
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                self.proc.kill()
            self.proc = None
        try:
            os.remove(sock_path())
        except OSError:
            pass
