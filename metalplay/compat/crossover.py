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
