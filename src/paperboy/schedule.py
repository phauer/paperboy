"""Runs the pipeline every hour, and pushes to the Boox every 5 minutes, via launchd user agents."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "local.paperboy"
PUSH_LABEL = "local.paperboy.push"
PUSH_INTERVAL = 300  # seconds; the Boox is only reachable while it's awake
LAUNCH_AGENTS = Path("~/Library/LaunchAgents").expanduser()
LOG_FILE = Path("~/Library/Logs/paperboy/paperboy.log").expanduser()

def render_plist(config: Path, log_file: Path | None = None) -> bytes:
    # Calendar intervals catch up right after wake if the Mac slept through them.
    return _render(LABEL, ["run"], config, log_file, {"StartCalendarInterval": {"Minute": 0}, "RunAtLoad": True})


def render_push_plist(config: Path, log_file: Path | None = None) -> bytes:
    return _render(PUSH_LABEL, ["push", "--quiet"], config, log_file, {"StartInterval": PUSH_INTERVAL})


def _render(label: str, command: list[str], config: Path, log_file: Path | None, timing: dict) -> bytes:
    config, log_file = config.expanduser().resolve(), log_file or LOG_FILE
    return plistlib.dumps({
        "Label": label,
        "ProgramArguments": [sys.executable, "-m", "paperboy.cli", *command, "--config", str(config)],
        "WorkingDirectory": str(config.parent),
        **timing,
        "ProcessType": "Background",
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        "StandardOutPath": str(log_file),
        "StandardErrorPath": str(log_file),
    })


def install(config: Path, push: bool) -> list[Path]:
    """Install the hourly run, and the push job if a Boox is configured; returns the plists."""
    launch_agents, log_file = LAUNCH_AGENTS, LOG_FILE
    launch_agents.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)  # launchd doesn't create it
    jobs = [(LABEL, render_plist(config, log_file))]
    if push:
        jobs.append((PUSH_LABEL, render_push_plist(config, log_file)))
    else:
        _remove(PUSH_LABEL)  # the Boox was removed from the config
    return [_load(label, plist) for label, plist in jobs]


def uninstall() -> bool:
    """Stop and remove the jobs; returns False if none was installed."""
    return any([_remove(LABEL), _remove(PUSH_LABEL)])


def _load(label: str, content: bytes) -> Path:
    run, plist = _launchctl, LAUNCH_AGENTS / f"{label}.plist"
    run(["launchctl", "bootout", f"{_domain()}/{label}"])  # replace an older version, if any
    plist.write_bytes(content)
    result = run(["launchctl", "bootstrap", _domain(), str(plist)])
    if result.returncode != 0:
        raise ScheduleError(f"launchctl bootstrap failed: {result.stderr.strip() or result.returncode}")
    return plist


def _remove(label: str) -> bool:
    plist = LAUNCH_AGENTS / f"{label}.plist"
    _launchctl(["launchctl", "bootout", f"{_domain()}/{label}"])
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
