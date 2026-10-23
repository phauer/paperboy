import pytest

from conftest import BOOX_URL, HOST, LIBRARY, make_post_json, numbered_post
from paperboy.boox import STORAGE_ROOT
from paperboy.config import Boox, Publication
from paperboy.state import AlreadyRunning

BOOX = Boox(url=BOOX_URL, folder="Books/Newsletters")
PUBLICATIONS = [Publication(HOST, backfill=3)]
BOOX_CONFIG = f'[[publications]]\nurl = "https://{HOST}"\n\n[boox]\nurl = "{BOOX_URL}"\n'


def _publish(substack, *numbers: int) -> None:
    substack.publish(*(numbered_post(n) for n in numbers))


def test_run_pushes_new_issues_into_the_library(pipeline, substack, boox, output_dir):
    _publish(substack, 2, 1)

    report = pipeline.run(PUBLICATIONS, BOOX)

    assert boox.library() == [
        "The Example Letter/2026-03-01 Post 1.epub",
        "The Example Letter/2026-03-02 Post 2.epub",
    ]
    local = output_dir / "The Example Letter" / "2026-03-02 Post 2.epub"
    assert boox.files[f"{LIBRARY}/The Example Letter/2026-03-02 Post 2.epub"] == local.read_bytes()
    assert sorted(report.push.pushed) == sorted(report.converted)
    assert report.push.reachable and report.push.errors == []


def test_nothing_new_means_no_traffic_to_the_boox(pipeline, substack, boox):
    _publish(substack, 1)
    pipeline.run(PUBLICATIONS, BOOX)
    requests = boox.requests

    report = pipeline.push(BOOX)

    assert report.pushed == [] and report.waiting == 0
    assert boox.requests == requests


def test_issue_deleted_on_the_boox_stays_deleted(pipeline, substack, boox):
    _publish(substack, 1)
    pipeline.run(PUBLICATIONS, BOOX)
    boox.files.clear()

    pipeline.push(BOOX)

    assert boox.library() == []


def test_regenerated_issue_replaces_the_copy_on_the_boox(pipeline, substack, boox, output_dir):
    _publish(substack, 1)
    pipeline.run(PUBLICATIONS, BOOX)
    local = output_dir / "The Example Letter" / "2026-03-01 Post 1.epub"
    local.write_bytes(local.read_bytes() + b"regenerated")

    report = pipeline.push(BOOX)

    assert report.pushed == [local]
    assert boox.files[f"{LIBRARY}/The Example Letter/2026-03-01 Post 1.epub"] == local.read_bytes()
    assert boox.library() == ["The Example Letter/2026-03-01 Post 1.epub"]


def test_sleeping_boox_keeps_issues_waiting_on_the_mac(pipeline, substack, boox):
    _publish(substack, 2, 1)
    boox.online = False

    report = pipeline.run(PUBLICATIONS, BOOX)

    assert len(report.converted) == 2
    assert not report.push.reachable and report.push.waiting == 2 and report.push.errors == []

    boox.online = True
    report = pipeline.push(BOOX)

    assert len(report.pushed) == 2
    assert len(boox.library()) == 2


def test_connection_lost_mid_push_continues_next_time(pipeline, substack, boox):
    _publish(substack, 3, 2, 1)
    boox.sleep_after_uploads = 1

    first = pipeline.run(PUBLICATIONS, BOOX).push

    assert len(first.pushed) == 1 and first.waiting == 2 and not first.reachable
    boox.online, boox.sleep_after_uploads = True, None
    second = pipeline.push(BOOX)

    assert len(second.pushed) == 2
    assert len(boox.uploads) == 3  # nothing was pushed twice
    assert len(boox.library()) == 3


def test_rejected_upload_is_an_error_and_retried(pipeline, substack, boox):
    _publish(substack, 2, 1)
    boox.reject_uploads = {"2026-03-01 Post 1.epub"}

    report = pipeline.run(PUBLICATIONS, BOOX).push

    assert [p.name for p in report.pushed] == ["2026-03-02 Post 2.epub"]
    assert report.errors == ["BOOX Drop /api/storage/upload failed: storage full"]

    boox.reject_uploads = set()
    report = pipeline.push(BOOX)

    assert [p.name for p in report.pushed] == ["2026-03-01 Post 1.epub"]


def test_existing_folders_are_reused_even_on_later_listing_pages(pipeline, substack, boox):
    boox.dirs |= {f"{STORAGE_ROOT}/Books/{name}" for name in ("Comics", "Manga", "Newsletters")}
    boox.dirs.add(f"{LIBRARY}/The Example Letter")
    boox.page_size = 1  # "Newsletters" only shows up on the third page
    _publish(substack, 1)

    report = pipeline.run(PUBLICATIONS, BOOX).push

    assert report.errors == []
    assert boox.library() == ["The Example Letter/2026-03-01 Post 1.epub"]


def test_custom_library_folder_and_unicode_names(pipeline, substack, boox):
    substack.publish(make_post_json(title="Größe – “Quoted” isn’t a problem"))

    pipeline.run(PUBLICATIONS, Boox(url=BOOX_URL, folder="Documents/Reading/Letters"))

    assert list(boox.files) == [
        f"{STORAGE_ROOT}/Documents/Reading/Letters/The Example Letter/2026-03-14 Größe – “Quoted” isn’t a problem.epub"
    ]


def test_only_finished_epubs_are_pushed(pipeline, boox, output_dir):
    folder = output_dir / "The Example Letter"
    folder.mkdir(parents=True)
    (folder / ".2026-03-01 Half written.epub.tmp").write_bytes(b"partial")
    (folder / "notes.txt").write_text("not an issue")
    (folder / "2026-03-02 Finished.epub").write_bytes(b"epub")

    pipeline.push(BOOX)

    assert boox.library() == ["The Example Letter/2026-03-02 Finished.epub"]


def test_push_waits_for_a_running_run(pipeline, state):
    with state.lock(), pytest.raises(AlreadyRunning):
        pipeline.push(BOOX)


def test_cli_run_reports_pushed_issues(cli, substack, write_config, capsys):
    _publish(substack, 1)

    assert cli("run", "--config", str(write_config(BOOX_CONFIG))) == 0

    out = capsys.readouterr().out
    assert "pushed: " in out and "2026-03-01 Post 1.epub" in out
    assert "1 pushed to the Boox, 0 errors" in out


def test_cli_push_with_sleeping_boox_succeeds(cli, substack, boox, write_config, capsys):
    _publish(substack, 1)
    boox.online = False
    config = str(write_config(BOOX_CONFIG))
    cli("run", "--config", config)
    capsys.readouterr()

    assert cli("push", "--config", config) == 0
    assert "not reachable (asleep or off Wi-Fi), 1 files wait on the Mac" in capsys.readouterr().out

    assert cli("push", "--quiet", "--config", config) == 0
    assert capsys.readouterr().out == ""


def test_cli_push_quiet_prints_only_when_something_happened(cli, substack, boox, write_config, capsys):
    config = str(write_config(BOOX_CONFIG))
    assert cli("push", "--quiet", "--config", config) == 0
    assert capsys.readouterr().out == ""

    assert cli("push", "--config", config) == 0
    assert "The Boox is up to date." in capsys.readouterr().out


def test_cli_push_errors_exit_nonzero(cli, substack, boox, write_config, capsys):
    _publish(substack, 1)
    boox.reject_uploads = {"2026-03-01 Post 1.epub"}

    assert cli("run", "--config", str(write_config(BOOX_CONFIG))) == 1
    assert "error: BOOX Drop /api/storage/upload failed: storage full" in capsys.readouterr().out


def test_cli_push_needs_a_boox(cli, write_config, capsys):
    assert cli("push", "--config", str(write_config())) == 2
    assert "no [boox] url configured" in capsys.readouterr().err


@pytest.mark.parametrize("section, message", [
    ("[boox]\n", "[boox] needs a 'url'"),
    ('[boox]\nurl = "192.0.2.10:8085"\n', "[boox] needs a 'url'"),
    (f'[boox]\nurl = "{BOOX_URL}"\nfolder = "/"\n', "'folder' must be a non-empty path"),
])
def test_invalid_boox_config_is_rejected(cli, write_config, capsys, section, message):
    assert cli("push", "--config", str(write_config(section))) == 2
    assert message in capsys.readouterr().err
