import os
import plistlib
import sys

from conftest import HOST
from paperboy import schedule

PUBLICATIONS = f'[[publications]]\nurl = "https://{HOST}"\n'


def test_show_prints_an_hourly_job_with_absolute_paths(cli, write_config, capsys, tmp_path, monkeypatch):
    config = write_config(PUBLICATIONS)
    monkeypatch.chdir(tmp_path)

    assert cli("schedule", "show", "--config", "config.toml") == 0

    job = plistlib.loads(capsys.readouterr().out.encode())
    assert job["Label"] == "local.paperboy"
    assert job["ProgramArguments"] == [sys.executable, "-m", "paperboy.cli", "run", "--config", str(config.resolve())]
    assert job["WorkingDirectory"] == str(config.resolve().parent)
    assert job["StartCalendarInterval"] == {"Minute": 0}
    assert job["RunAtLoad"] is True
    assert job["StandardOutPath"] == job["StandardErrorPath"] == str(schedule.LOG_FILE)
    assert "/opt/homebrew/bin" in job["EnvironmentVariables"]["PATH"]


def test_install_writes_job_and_loads_it(cli, write_config, launchd, capsys):
    config = write_config(PUBLICATIONS)

    assert cli("schedule", "install", "--config", str(config)) == 0

    plist = schedule.LAUNCH_AGENTS / "local.paperboy.plist"
    assert plistlib.loads(plist.read_bytes())["ProgramArguments"][-1] == str(config.resolve())
    assert schedule.LOG_FILE.parent.is_dir()
    domain = f"gui/{os.getuid()}"
    assert launchd.calls == [
        ["launchctl", "bootout", f"{domain}/local.paperboy"],
        ["launchctl", "bootstrap", domain, str(plist)],
    ]
    assert "Hourly run installed" in capsys.readouterr().out


def test_install_twice_replaces_the_job(cli, write_config, launchd):
    config = write_config(PUBLICATIONS)

    cli("schedule", "install", "--config", str(config))
    cli("schedule", "install", "--config", str(config))

    assert [call[1] for call in launchd.calls] == ["bootout", "bootstrap", "bootout", "bootstrap"]


def test_install_reports_launchctl_failure(cli, write_config, launchd, capsys):
    launchd.fail_bootstrap = True

    assert cli("schedule", "install", "--config", str(write_config(PUBLICATIONS))) == 2
    assert "launchctl bootstrap failed: Bootstrap failed: 5" in capsys.readouterr().err


def test_install_refuses_broken_or_empty_config(cli, write_config, launchd, tmp_path, capsys):
    assert cli("schedule", "install", "--config", str(write_config())) == 2
    assert "no [[publications]] configured" in capsys.readouterr().err
    assert cli("schedule", "install", "--config", str(tmp_path / "missing.toml")) == 2
    assert launchd.calls == []
    assert not schedule.LAUNCH_AGENTS.exists()


def test_uninstall_stops_and_removes_the_job(cli, write_config, launchd, capsys):
    cli("schedule", "install", "--config", str(write_config(PUBLICATIONS)))

    assert cli("schedule", "uninstall") == 0

    assert not (schedule.LAUNCH_AGENTS / "local.paperboy.plist").exists()
    assert launchd.calls[-1][1] == "bootout"
    assert "Hourly run removed." in capsys.readouterr().out


def test_uninstall_without_job(cli, capsys):
    assert cli("schedule", "uninstall") == 0
    assert "No hourly run was installed." in capsys.readouterr().out
