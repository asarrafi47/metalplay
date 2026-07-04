"""Register MetalPlay bottles with CrossOver and build launch env."""

from __future__ import annotations

import re
import subprocess
import uuid
from pathlib import Path

from metalplay.runtime.wine import WineRuntime, detect_installed_runtimes

CROSSOVER_BOTTLE_NAME = "MetalPlay-Steam"
_CXBOTTLE_BIN = Path(
    "/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/cxbottle"
)


def crossover_runtime() -> WineRuntime | None:
    for runtime in detect_installed_runtimes():
        if runtime.name.startswith("crossover"):
            return runtime
    return None


def crossover_bottles_dir() -> Path:
    return Path.home() / "Library/Application Support/CrossOver/Bottles"


def _minimal_cxbottle_conf() -> str:
    """Minimal cxbottle.conf when no CrossOver template bottle exists."""
    bottle_id = str(uuid.uuid4()).upper()
    return f''';; MetalPlay CrossOver bottle registration
[Bottle]
"MenuRoot" = "/Windows Applications"
"MenuStrip" = "1"
"BottleID" = "{bottle_id}"
"Version" = "26.2.0"
"Timestamp" = "20260604T163056Z"
"Encoding" = "UTF-8"
"Description" = "MetalPlay Steam bottle"
"Template" = "win10"
"MenuMode" = "ignore"
"AssocMode" = "ignore"
[Wine]
"WineArch" = "win64"
'''


def prepare_crossover_template() -> None:
    """Ensure a CrossOver template bottle exists so cxbottle.conf can be cloned."""
    if crossover_runtime() is None or not _CXBOTTLE_BIN.is_file():
        return
    template_conf = crossover_bottles_dir() / ".metalplay-template" / "cxbottle.conf"
    if template_conf.is_file():
        return
    subprocess.run(
        [str(_CXBOTTLE_BIN), "--bottle", ".metalplay-template", "--create", "--template", "win10"],
        capture_output=True,
        timeout=120,
    )


def ensure_bottle_registered(bottle: Path, name: str = CROSSOVER_BOTTLE_NAME) -> bool:
    """
    Register a MetalPlay prefix as a CrossOver bottle.

    CrossOver's wine binary requires CX_BOTTLE + cxbottle.conf; WINEPREFIX alone
    triggers a fatal 'default bottle' error.
    """
    if crossover_runtime() is None:
        return False

    prepare_crossover_template()

    conf = bottle / "cxbottle.conf"
    if not conf.is_file():
        template_conf = crossover_bottles_dir() / ".metalplay-template" / "cxbottle.conf"
        if template_conf.is_file():
            text = template_conf.read_text()
            new_id = str(uuid.uuid4()).upper()
            text = re.sub(r'"BottleID" = "[^"]+"', f'"BottleID" = "{new_id}"', text)
            conf.write_text(text)
        else:
            conf.write_text(_minimal_cxbottle_conf())

    bottles_dir = crossover_bottles_dir()
    bottles_dir.mkdir(parents=True, exist_ok=True)
    link = bottles_dir / name
    target = bottle.resolve()
    if link.is_symlink():
        if link.resolve() != target:
            link.unlink()
            link.symlink_to(target)
    elif not link.exists():
        link.symlink_to(target)
    return True


def crossover_env(base: dict[str, str], bottle_name: str = CROSSOVER_BOTTLE_NAME) -> dict[str, str]:
    """Env vars required when launching through CrossOver's wine."""
    env = dict(base)
    runtime = crossover_runtime()
    if runtime is None:
        return env
    env["CX_BOTTLE"] = bottle_name
    env["PATH"] = f"{runtime.bin_dir}:{env.get('PATH', '')}"
    return env


def cxstart_bin() -> Path | None:
    """
    CrossOver's launcher script.

    Games must go through cxstart (not bin/wine directly): cxstart launches via
    the CrossOver-Hosted Application, which is what gets the game's Cocoa window
    activated and ordered on-screen. Raw bin/wine launches create the window but
    never map it (onscreen=no), which looks like a silent hang.
    """
    runtime = crossover_runtime()
    if runtime is None:
        return None
    cxstart = runtime.root / "bin" / "cxstart"
    return cxstart if cxstart.is_file() else None


def apply_crossover_display_fix(
    bottle: Path,
    env: dict[str, str],
    exe_names: tuple[str, ...] = (),
) -> None:
    """
    Undo CrossOver's Retina display settings for the session.

    When CrossOver adopts a bottle on a Retina Mac it writes a global
    `Mac Driver RetinaMode=y` AND sets the Windows DPI to 192 (LogPixels).
    Either one makes Wine scale a non-DPI-aware game 2x: the window doubles
    past the screen, the game renders into the top-left quarter, and
    mouse/trackpad coordinates land 2x off. Re-assert 1:1 mapping every
    launch in case CrossOver flips them back.
    """
    from metalplay.runtime.wine import wine_command

    runtime = crossover_runtime()
    if runtime is None:
        return
    retina_keys = [r"HKCU\Software\Wine\Mac Driver"]
    retina_keys += [rf"HKCU\Software\Wine\AppDefaults\{exe}\Mac Driver" for exe in exe_names]
    settings = [(key, "RetinaMode", "REG_SZ", "n") for key in retina_keys]
    settings += [
        (r"HKCU\Control Panel\Desktop", "LogPixels", "REG_DWORD", "96"),
        (r"HKCU\Software\Wine\Fonts", "LogPixels", "REG_DWORD", "96"),
    ]
    for key, value, reg_type, data in settings:
        subprocess.run(
            wine_command(
                runtime.wine_bin,
                "reg", "add", key,
                "/v", value, "/t", reg_type, "/d", data, "/f",
            ),
            env=env,
            capture_output=True,
            timeout=60,
        )


def cxstart_command(
    exe: Path,
    args: list[str],
    bottle_name: str = CROSSOVER_BOTTLE_NAME,
) -> list[str] | None:
    """
    Build a cxstart invocation for a game exe.

    --no-update stops CrossOver from running its bottle-update/package installers
    on launch (they interrupt the game with installer dialogs); --wait keeps
    cxstart alive until the game exits so callers can wait on it.
    """
    cxstart = cxstart_bin()
    if cxstart is None:
        return None
    return [
        str(cxstart),
        "--bottle", bottle_name,
        "--no-update",
        "--wait",
        str(exe),
        *args,
    ]


def without_crossover_conf(bottle: Path):
    """Hide cxbottle.conf while running Gcenx Wine on the shared prefix."""
    from contextlib import contextmanager

    conf = bottle / "cxbottle.conf"
    hidden = bottle / "cxbottle.conf.metalplay-hidden"

    @contextmanager
    def _guard():
        moved = False
        if conf.is_file() and not hidden.is_file():
            conf.rename(hidden)
            moved = True
        try:
            yield
        finally:
            if moved and hidden.is_file() and not conf.is_file():
                hidden.rename(conf)

    return _guard()
