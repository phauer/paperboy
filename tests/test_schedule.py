import os
import plistlib
import sys

from conftest import BOOX_URL, HOST
from paperboy import schedule

PUBLICATIONS = f'[[publications]]\nurl = "https://{HOST}"\n'
BOOX_CONFIG = f'{PUBLICATIONS}\n[boox]\nurl = "{BOOX_URL}"\n'


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
        ["launchctl", "bootout", f"{domain}/local.paperboy.push"],  # no Boox configured
        ["launchctl", "bootout", f"{domain}/local.paperboy"],
        ["launchctl", "bootstrap", domain, str(plist)],
    ]
    out = capsys.readouterr().out
    assert "Hourly run installed" in out and "Push to the Boox" not in out
    assert not (schedule.LAUNCH_AGENTS / "local.paperboy.push.plist").exists()


def test_install_with_boox_adds_a_push_job_every_5_minutes(cli, write_config, launchd, capsys):
    config = write_config(BOOX_CONFIG)

    assert cli("schedule", "install", "--config", str(config)) == 0

    plist = schedule.LAUNCH_AGENTS / "local.paperboy.push.plist"
    job = plistlib.loads(plist.read_bytes())
    assert job["ProgramArguments"] == [
        sys.executable, "-m", "paperboy.cli", "push", "--quiet", "--config", str(config.resolve())
    ]
    assert job["StartInterval"] == 300
    assert job["StandardOutPath"] == str(schedule.LOG_FILE)
    assert ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)] in launchd.calls
    assert (schedule.LAUNCH_AGENTS / "local.paperboy.plist").exists()
    assert "Push to the Boox every 5 minutes installed" in capsys.readouterr().out


def test_removing_the_boox_from_the_config_removes_the_push_job(cli, write_config):
    cli("schedule", "install", "--config", str(write_config(BOOX_CONFIG)))

    cli("schedule", "install", "--config", str(write_config(PUBLICATIONS)))

    assert not (schedule.LAUNCH_AGENTS / "local.paperboy.push.plist").exists()
    assert (schedule.LAUNCH_AGENTS / "local.paperboy.plist").exists()


def test_show_prints_both_jobs_with_a_boox(cli, write_config, capsys):
    assert cli("schedule", "show", "--config", str(write_config(BOOX_CONFIG))) == 0

    out = capsys.readouterr().out
    assert out.count("<?xml") == 2
    assert "<string>local.paperboy</string>" in out and "<string>local.paperboy.push</string>" in out


def test_install_twice_replaces_the_job(cli, write_config, launchd):
    config = write_config(PUBLICATIONS)

    cli("schedule", "install", "--config", str(config))
    cli("schedule", "install", "--config", str(config))

    assert [call[1] for call in launchd.calls] == ["bootout"] * 2 + ["bootstrap"] + ["bootout"] * 2 + ["bootstrap"]


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


def test_uninstall_stops_and_removes_both_jobs(cli, write_config, launchd, capsys):
    cli("schedule", "install", "--config", str(write_config(BOOX_CONFIG)))
    launchd.calls.clear()

    assert cli("schedule", "uninstall") == 0

    assert list(schedule.LAUNCH_AGENTS.glob("*.plist")) == []
    domain = f"gui/{os.getuid()}"
    assert launchd.calls == [
        ["launchctl", "bootout", f"{domain}/local.paperboy"],
        ["launchctl", "bootout", f"{domain}/local.paperboy.push"],
    ]
    assert "Scheduled jobs removed." in capsys.readouterr().out


def test_uninstall_without_job(cli, capsys):
    assert cli("schedule", "uninstall") == 0
    assert "No scheduled job was installed." in capsys.readouterr().out
