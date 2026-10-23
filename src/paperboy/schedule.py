"""Runs the pipeline every hour via a launchd user agent."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "local.paperboy"
LAUNCH_AGENTS = Path("~/Library/LaunchAgents").expanduser()
LOG_FILE = Path("~/Library/Logs/paperboy/paperboy.log").expanduser()

def render_plist(config: Path, log_file: Path | None = None) -> bytes:
    config, log_file = config.expanduser().resolve(), log_file or LOG_FILE
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-m", "paperboy.cli", "run", "--config", str(config)],
        "WorkingDirectory": str(config.parent),
        # Calendar intervals catch up right after wake if the Mac slept through them.
        "StartCalendarInterval": {"Minute": 0},
        "RunAtLoad": True,
        "ProcessType": "Background",
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        "StandardOutPath": str(log_file),
        "StandardErrorPath": str(log_file),
    })


def install(config: Path) -> Path:
    run, launch_agents, log_file = _launchctl, LAUNCH_AGENTS, LOG_FILE
    plist = launch_agents / f"{LABEL}.plist"
    launch_agents.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)  # launchd doesn't create it
    run(["launchctl", "bootout", f"{_domain()}/{LABEL}"])  # replace an older version, if any
    plist.write_bytes(render_plist(config, log_file))
    result = run(["launchctl", "bootstrap", _domain(), str(plist)])
    if result.returncode != 0:
        raise ScheduleError(f"launchctl bootstrap failed: {result.stderr.strip() or result.returncode}")
    return plist


def uninstall() -> bool:
    """Stop and remove the job; returns False if it wasn't installed."""
    run = _launchctl
    plist = LAUNCH_AGENTS / f"{LABEL}.plist"
    run(["launchctl", "bootout", f"{_domain()}/{LABEL}"])
    if not plist.exists():
        return False
    plist.unlink()
    return True


class ScheduleError(Exception):
    pass


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _launchctl(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True)
