"""MetalPlay web interface — runs in your browser, no extra dependencies."""

from __future__ import annotations

import json
import mimetypes
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from metalplay import __version__, paths
from metalplay.bottle import manager as bottles
from metalplay.config import Config
from metalplay.launcher import run as launcher
from metalplay.runtime import dxmt
from metalplay.runtime.installer import install_brew_wine_stable, install_free_runtime, setup_all
from metalplay.runtime.wine import check_rosetta, detect_installed_runtimes, get_runtime, system_info
from metalplay.steam import (
    STEAM_BOTTLE_NAME,
    launch_game,
    setup as steam_setup,
    status as steam_status,
)
from metalplay.gui.cache import TTLCache
from metalplay.gui.revision import GUI_API_REVISION
from metalplay.steam.client import is_installed, set_game_graphics

WEB_DIR = Path(__file__).parent / "static"
DEFAULT_PORT = 8765


def _icon_path() -> Path | None:
    """Best icon for native window / dock."""
    for name in ("icon-128.png", "icon-32.png", "favicon.png"):
        path = WEB_DIR / name
        if path.is_file():
            return path
    return None


# Shared log buffer for the activity panel
_log_lines: list[str] = []
_log_lock = threading.Lock()
_job_lock = threading.Lock()
_job_state = {"running": False}
_steam_op_lock = threading.Lock()
_steam_op_busy = False
_status_cache = TTLCache[dict](8.0)
_steam_light_cache = TTLCache[dict](5.0)
_steam_full_cache = TTLCache[dict](15.0)
_controller_cache = TTLCache[dict](3.0)


def _log(msg: str) -> None:
    with _log_lock:
        _log_lines.append(msg)
        if len(_log_lines) > 200:
            del _log_lines[:100]
    _status_cache.clear()
    _steam_light_cache.clear()
    _steam_full_cache.clear()
    _controller_cache.clear()


def _status() -> dict:
    info = system_info()
    runtimes = detect_installed_runtimes()
    return {
        "version": __version__,
        "api_revision": GUI_API_REVISION,
        "macos": info["macos"],
        "arch": info["arch"],
        "rosetta": check_rosetta() if info["arch"] == "arm64" else True,
        "dxmt": dxmt.is_installed(),
        "runtimes": [
            {"name": r.name, "version": r.version(), "metal": r.is_metal_capable(), "path": str(r.root)}
            for r in runtimes
        ],
        "bottles": [
            {"name": n, "graphics": m.graphics if m else "?", "path": str(p)}
            for n, p, m in bottles.list_bottles()
        ],
    }


def _tune_status() -> dict:
    """Fast tune summary — avoids system_profiler on every poll."""
    from metalplay.tune.apply import HARDWARE_FILE, load_applied_profile

    applied = load_applied_profile()
    chip = (applied or {}).get("chip", "this Mac")
    tier = (applied or {}).get("tier", "")
    game_resolution = (applied or {}).get("game_resolution", "")
    if HARDWARE_FILE.is_file() and not game_resolution:
        try:
            hw = json.loads(HARDWARE_FILE.read_text())
            chip = hw.get("chip", chip)
            tier = hw.get("tier", tier)
            game_resolution = hw.get("recommended_game_resolution", game_resolution)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "applied": applied is not None,
        "chip": chip,
        "tier": tier,
        "game_resolution": game_resolution,
    }


def _serialize_controller(item) -> dict:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(item):
        return asdict(item)
    if isinstance(item, dict):
        return item
    return {
        "name": str(getattr(item, "name", item)),
        "kind": getattr(item, "kind", "generic"),
        "connection": getattr(item, "connection", "unknown"),
        "connected": bool(getattr(item, "connected", True)),
    }


def _controller_status() -> dict:
    empty: dict = {
        "available": False,
        "controllers": [],
        "doctor": {"ok": True, "warnings": [], "checks": [], "messages": []},
        "profiles": [],
    }
    try:
        from metalplay.controller.compat import doctor as controller_doctor
        from metalplay.controller.detect import list_controllers
        from metalplay.controller.profiles import controller_profile_for
    except ImportError:
        empty["doctor"]["messages"] = ["Controller module not available"]
        return empty

    try:
        runtimes = detect_installed_runtimes()
        runtime = get_runtime(Config.load().wine_runtime) or (runtimes[0] if runtimes else None)
        bottle = bottles.bottle_path(STEAM_BOTTLE_NAME)

        controllers = [_serialize_controller(c) for c in list_controllers()]

        doctor_result = controller_doctor(bottle, runtime, quick=True)
        if not isinstance(doctor_result, dict):
            doctor_result = {"ok": True, "checks": [str(doctor_result)], "recommendations": []}
        doctor_result = {
            "ok": doctor_result.get("ok", True),
            "checks": doctor_result.get("checks", []),
            "warnings": doctor_result.get("recommendations", doctor_result.get("warnings", [])),
            "messages": doctor_result.get("messages", []),
        }

        config = Config.load()
        profiles: list[dict] = []
        try:
            from metalplay.steam.library import list_games

            for game in list_games(bottle, config):
                if game.install_path is None:
                    continue
                profile = controller_profile_for(game.app_id, config)
                profiles.append(
                    {
                        "app_id": game.app_id,
                        "name": game.name,
                        "steam_input": bool(profile.get("steam_input", False)),
                        "prefer": profile.get("prefer", "xinput"),
                        "installed": True,
                    }
                )
        except Exception:
            pass

        return {
            "available": True,
            "controllers": controllers,
            "doctor": doctor_result,
            "profiles": profiles,
        }
    except Exception as exc:
        empty["doctor"]["messages"] = [str(exc)]
        return empty


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        pass  # quiet

    def _json(self, data: dict, code: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass  # client closed early (tab refresh / poll cancel)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            return self._api_get(parsed.path)
        return self._serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        body = self._read_body()
        routes = {
            "/api/setup": self._api_setup,
            "/api/install/gcenx": self._api_install_gcenx,
            "/api/install/brew": self._api_install_brew,
            "/api/bottle/create": self._api_bottle_create,
            "/api/bottle/delete": self._api_bottle_delete,
            "/api/launch": self._api_launch,
            "/api/steam/setup": self._api_steam_setup,
            "/api/steam/launch": self._api_steam_launch,
            "/api/steam/stop": self._api_steam_stop,
            "/api/steam/run": self._api_steam_run,
            "/api/steam/set-graphics": self._api_steam_set_graphics,
            "/api/tune/apply": self._api_tune_apply,
            "/api/controller/set-profile": self._api_controller_set_profile,
        }
        handler = routes.get(parsed.path)
        if handler:
            try:
                result = handler(body)
                self._json({"ok": True, **result})
            except Exception as exc:
                import traceback
                _log(f"Error: {exc}")
                _log(traceback.format_exc())
                self._json({"ok": False, "error": str(exc)}, 500)
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def _api_get(self, path: str) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if path == "/api/status":
            self._json(_status_cache.get(_status))
        elif path == "/api/log":
            since = int(qs.get("since", ["0"])[0])
            with _log_lock:
                total = len(_log_lines)
                lines = _log_lines[since:] if since < total else []
            self._json({"lines": lines, "total": total})
        elif path == "/api/steam/status":
            bottle = bottles.bottle_path(STEAM_BOTTLE_NAME)
            light = qs.get("light", ["0"])[0] == "1"
            cache = _steam_light_cache if light else _steam_full_cache
            self._json(cache.get(lambda: steam_status(bottle, light=light)))
        elif path == "/api/steam/rockstar-logs":
            self._json(self._rockstar_logs())
        elif path == "/api/tune/status":
            self._json(_tune_status())
        elif path == "/api/controller/status":
            if qs.get("force", ["0"])[0] == "1":
                _controller_cache.clear()
            self._json(_controller_cache.get(_controller_status))
        else:
            self._json({"error": "not found"}, 404)

    def _rockstar_logs(self) -> dict:
        from pathlib import Path

        from metalplay.compat.rockstar_cef import diagnose_launch_failure, rockstar_log_paths, tail_log

        bottle = bottles.bottle_path(STEAM_BOTTLE_NAME)
        paths = rockstar_log_paths(bottle)
        stub = Path(paths["stub_log"]) if paths.get("stub_log") else None
        launcher = Path(paths["launcher_log"]) if paths.get("launcher_log") else None
        stub_lines = tail_log(stub) if stub else []
        launcher_lines = tail_log(launcher) if launcher else []
        return {
            "paths": paths,
            "stub_log": stub_lines,
            "launcher_log": launcher_lines,
            "diagnosis": diagnose_launch_failure(stub_lines, launcher_lines),
        }

    def _api_setup(self, _: dict) -> dict:
        _log("Starting free quick setup...")
        result = setup_all(callback=_log)
        _log(f"Setup complete — Wine {result['version']}")
        return {"message": "Setup complete", **result}

    def _api_install_gcenx(self, _: dict) -> dict:
        _log("Installing Gcenx Wine...")
        runtime = install_free_runtime(prefer="gcenx", callback=_log)
        if dxmt.is_installed():
            dxmt.install_into_wine(runtime)
        _log("Gcenx Wine ready.")
        return {"message": "Gcenx Wine installed", "version": runtime.version()}

    def _api_install_brew(self, _: dict) -> dict:
        _log("Installing Wine Stable via Homebrew...")
        runtime = install_brew_wine_stable(callback=_log)
        if runtime and dxmt.is_installed():
            dxmt.install_into_wine(runtime)
        _log("Wine Stable ready." if runtime else "Install failed.")
        return {"message": "Wine Stable installed" if runtime else "Failed"}

    def _api_bottle_create(self, body: dict) -> dict:
        name = body.get("name", "gaming").strip()
        runtime = get_runtime(Config.load().wine_runtime) or detect_installed_runtimes()[0]
        _log(f"Creating bottle '{name}'...")
        bottles.create(name, runtime, graphics="dxmt")
        config = Config.load()
        config.default_bottle = name
        config.save()
        _log(f"Bottle '{name}' created.")
        return {"message": f"Bottle '{name}' created"}

    def _api_bottle_delete(self, body: dict) -> dict:
        name = body.get("name", "")
        bottles.remove(name)
        _log(f"Deleted bottle '{name}'.")
        return {"message": f"Deleted '{name}'"}

    def _api_launch(self, body: dict) -> dict:
        exe = body.get("exe", "")
        bottle_name = body.get("bottle", "")
        graphics = body.get("graphics", "dxmt")
        runtime = get_runtime(Config.load().wine_runtime) or detect_installed_runtimes()[0]
        bottle = bottles.bottle_path(bottle_name)
        _log(f"Launching {Path(exe).name}...")
        code = launcher.launch(runtime, bottle, exe, config=Config.load(), graphics=graphics)
        _log(f"Exited with code {code}")
        return {"code": code}

    def _run_background(self, label: str, fn) -> dict:
        with _job_lock:
            if _job_state["running"]:
                msg = "Another operation is already running — wait for it to finish."
                _log(msg)
                return {"message": msg, "started": False}
            _job_state["running"] = True

        def worker() -> None:
            try:
                fn()
            except Exception as exc:
                import traceback
                _log(f"Error: {exc}")
                _log(traceback.format_exc())
            finally:
                with _job_lock:
                    _job_state["running"] = False

        threading.Thread(target=worker, daemon=True).start()
        _log(f"Started: {label}")
        return {"message": f"{label} started", "started": True}

    def _run_detached(self, label: str, fn) -> dict:
        """Start a long-running task without holding the setup/install job lock."""

        def worker() -> None:
            try:
                fn()
            except Exception as exc:
                import traceback
                _log(f"Error: {exc}")
                _log(traceback.format_exc())

        threading.Thread(target=worker, daemon=True).start()
        _log(label)
        return {"message": label, "started": True}

    def _run_steam_op(self, label: str, fn) -> dict:
        """Single-flight guard for Steam launch / game start (prevents kill races)."""
        global _steam_op_busy
        with _steam_op_lock:
            if _steam_op_busy:
                msg = (
                    "Steam is busy — a launch or stop is already in progress. "
                    "Wait ~30s before clicking again."
                )
                _log(msg)
                return {"message": msg, "started": False, "busy": True}
            _steam_op_busy = True

        def worker() -> None:
            global _steam_op_busy
            try:
                fn()
            except Exception as exc:
                import traceback
                _log(f"Error: {exc}")
                _log(traceback.format_exc())
            finally:
                with _steam_op_lock:
                    _steam_op_busy = False

        threading.Thread(target=worker, daemon=True).start()
        _log(label)
        return {"message": label, "started": True}

    def _api_steam_setup(self, _: dict) -> dict:
        return self._run_background("Steam setup", lambda: steam_setup(callback=_log))

    def _api_steam_launch(self, _: dict) -> dict:
        from metalplay.compat.process import count_steam_ui_windows, focus_steam_window, is_steam_running
        from metalplay.steam.client import ensure_steam_ui, is_installed

        runtimes = detect_installed_runtimes()
        runtime = get_runtime(Config.load().wine_runtime) or (runtimes[0] if runtimes else None)
        if not runtime:
            raise RuntimeError("No Wine runtime. Use Home → Quick Setup first.")
        bottle = bottles.bottle_path(STEAM_BOTTLE_NAME)
        if not is_installed(bottle):
            raise RuntimeError("Steam not installed. Click Setup Steam (Full) first.")

        if is_steam_running() and count_steam_ui_windows(force=True) > 0 and focus_steam_window():
            msg = "Windows Steam (Wine) window brought to front."
            _log(msg)
            return {"message": msg, "started": False, "focused": True}

        def job() -> None:
            ensure_steam_ui(runtime, bottle, Config.load(), callback=_log)

        return self._run_steam_op("Launching Windows Steam client…", job)

    def _api_steam_stop(self, _: dict) -> dict:
        from metalplay.steam.client import stop_client

        global _steam_op_busy
        with _steam_op_lock:
            if _steam_op_busy:
                msg = "Cannot stop Steam while a launch is in progress — wait a few seconds."
                _log(msg)
                return {"message": msg, "stopped": 0, "busy": True}
            _steam_op_busy = True
        try:
            runtimes = detect_installed_runtimes()
            runtime = get_runtime(Config.load().wine_runtime) or (runtimes[0] if runtimes else None)
            if not runtime:
                raise RuntimeError("No Wine runtime.")
            bottle = bottles.bottle_path(STEAM_BOTTLE_NAME)
            n = stop_client(runtime, bottle, callback=_log)
            return {
                "message": f"Stopped {n} Steam process(es)." if n else "Steam was not running.",
                "stopped": n,
            }
        finally:
            with _steam_op_lock:
                _steam_op_busy = False

    def _api_steam_run(self, body: dict) -> dict:
        app_id = str(body.get("app_id", ""))
        if not app_id:
            raise ValueError("app_id is required")
        graphics = body.get("graphics", "auto")
        runtimes = detect_installed_runtimes()
        runtime = get_runtime(Config.load().wine_runtime) or (runtimes[0] if runtimes else None)
        if not runtime:
            raise RuntimeError("No Wine runtime. Use Home → Quick Setup first.")
        bottle = bottles.bottle_path(STEAM_BOTTLE_NAME)

        def job() -> None:
            _log(f"Preparing Windows Steam launch for game {app_id} [{graphics}]…")
            code = launch_game(
                runtime, bottle, app_id, Config.load(), graphics=graphics, callback=_log,
            )
            if code == 0:
                _log(
                    f"Steam launch handoff finished (exit 0). "
                    "If the game did not appear, open Steam → Library → Play."
                )
            else:
                _log(f"Steam/game launch exited with code {code}")

        return self._run_steam_op(f"Launching Steam game {app_id} [{graphics}]…", job)

    def _api_steam_set_graphics(self, body: dict) -> dict:
        app_id = str(body.get("app_id", ""))
        graphics = body.get("graphics", "dxmt")
        name = body.get("name", "")
        set_game_graphics(app_id, graphics, name=name)
        _log(f"Game {app_id} graphics → {graphics}")
        return {"message": f"Set {app_id} to {graphics}"}

    def _api_tune_apply(self, _: dict) -> dict:
        from metalplay.tune import apply_tune

        def job() -> None:
            apply_tune(callback=_log)

        return self._run_background("Optimize Mac", job)

    def _api_controller_set_profile(self, body: dict) -> dict:
        app_id = str(body.get("app_id", ""))
        if not app_id:
            raise ValueError("app_id is required")
        steam_input = bool(body.get("steam_input", False))
        prefer = str(body.get("prefer", "xinput"))
        name = str(body.get("name", ""))

        try:
            from metalplay.controller.profiles import set_controller_profile

            set_controller_profile(app_id, steam_input=steam_input, prefer=prefer, name=name)
        except ImportError:
            config = Config.load()
            profile = config.game_profiles.setdefault(app_id, {})
            profile["steam_input"] = steam_input
            profile["prefer"] = prefer
            if name:
                profile["name"] = name
            config.save()

        _log(f"Controller profile {app_id}: steam_input={steam_input}, prefer={prefer}")
        _controller_cache.clear()
        return {"message": f"Controller profile saved for {app_id}"}

    def _serve_static(self, path: str) -> None:
        if path in ("/favicon.ico", "/favicon.png"):
            path = "/icon-32.png"
        if path in ("/", ""):
            path = "/index.html"
        file_path = WEB_DIR / path.lstrip("/")
        if not file_path.is_file() or WEB_DIR not in file_path.resolve().parents:
            self.send_error(404)
            return
        content = file_path.read_bytes()
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        if path.endswith(".html"):
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(content)


def create_server(port: int) -> ThreadingHTTPServer:
    class _ReuseServer(ThreadingHTTPServer):
        allow_reuse_address = True

    return _ReuseServer(("127.0.0.1", port), Handler)


def _fetch_api_revision(port: int) -> int | None:
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=0.5) as resp:
            data = json.loads(resp.read().decode())
            rev = data.get("api_revision")
            return int(rev) if rev is not None else None
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _stop_stale_gui_servers() -> None:
    import subprocess
    import time

    subprocess.run(["pkill", "-f", "metalplay gui"], capture_output=True)
    time.sleep(1.5)


def is_metalplay_server(port: int) -> bool:
    """True if something on this port looks like our GUI API."""
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=0.5) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode())
            return "version" in data and "runtimes" in data
    except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError, ValueError):
        return False


def find_metalplay_port(start: int = DEFAULT_PORT, count: int = 10) -> int | None:
    for port in range(start, start + count):
        if is_metalplay_server(port):
            return port
    return None


def _open_ui(url: str, *, native_window: bool, open_browser: bool) -> None:
    if native_window:
        from metalplay.gui.native import open_native_window

        open_native_window(url, icon_path=_icon_path())
        return
    print(f"MetalPlay web UI → {url}")
    if open_browser:
        webbrowser.open(url)
    print("Press Ctrl+C in this terminal to stop the server (if you started it).")


def _start_server(port: int) -> tuple[ThreadingHTTPServer, int]:
    """Bind an HTTP server, trying the next ports if the default is taken."""
    last_err: OSError | None = None
    for candidate in range(port, port + 12):
        try:
            return create_server(candidate), candidate
        except OSError as exc:
            if exc.errno != 48:  # Address already in use
                raise
            if is_metalplay_server(candidate):
                raise RuntimeError(
                    f"MetalPlay is already running on port {candidate}. "
                    "Close the existing window or run: pkill -f 'metalplay gui'"
                ) from exc
            last_err = exc
    if last_err:
        raise last_err
    raise OSError("No free port found for MetalPlay GUI")


def _wait_for_server(url: str, attempts: int = 40) -> bool:
    import time
    import urllib.error
    import urllib.request

    for _ in range(attempts):
        try:
            with urllib.request.urlopen(f"{url}/api/status", timeout=0.5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError, TimeoutError):
            pass
        time.sleep(0.25)
    return False


def main(
    port: int = DEFAULT_PORT,
    *,
    open_browser: bool = True,
    native_window: bool = False,
) -> None:
    paths.ensure_dirs()

    existing = find_metalplay_port(port, 1) or find_metalplay_port(DEFAULT_PORT, 12)
    if existing is not None:
        rev = _fetch_api_revision(existing)
        if rev == GUI_API_REVISION:
            url = f"http://127.0.0.1:{existing}"
            print(f"MetalPlay already running on port {existing} — opening UI")
            _open_ui(url, native_window=native_window, open_browser=open_browser)
            return
        print(
            f"MetalPlay on port {existing} is outdated (api_revision={rev}, "
            f"need {GUI_API_REVISION}) — restarting GUI server…"
        )
        _stop_stale_gui_servers()

    server, port = _start_server(port)
    url = f"http://127.0.0.1:{port}"

    if native_window:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        if not _wait_for_server(url):
            print("MetalPlay server failed to start — check ~/.metalplay/logs/gui.log")
            server.shutdown()
            return
        if port != DEFAULT_PORT:
            print(f"MetalPlay listening on port {port} (default {DEFAULT_PORT} was busy)")
        try:
            from metalplay.gui.native import open_native_window

            open_native_window(url, icon_path=_icon_path())
        finally:
            # Keep the HTTP server alive when launched from MetalPlay.app (nohup).
            if os.environ.get("METALPLAY_KEEP_SERVER") != "1":
                server.shutdown()
        return

    print(f"MetalPlay web UI → {url}")
    if port != DEFAULT_PORT:
        print(f"(port {DEFAULT_PORT} was busy — using {port})")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
