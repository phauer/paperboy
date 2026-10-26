import io
import sqlite3

import pytest

from conftest import BOOX_URL, HOST, KINDLE_SERIAL, make_post_json, numbered_post
from paperboy.config import Boox, Publication
from paperboy.kindle import CREDENTIAL_NAME, KindleError, start_login

BOOX = Boox(url=BOOX_URL, folder="Books/Newsletters")
PUBLICATIONS = [Publication(HOST, backfill=3)]
KINDLE_CONFIG = f'[[publications]]\nurl = "https://{HOST}"\n\n[kindle]\n'


def _publish(substack, *numbers: int) -> None:
    substack.publish(*(numbered_post(n) for n in numbers))


def _login(pipeline, kindle):
    login = start_login()
    return pipeline.kindle_login(login, kindle.authorize(login.url))


def test_login_stores_a_credential_and_lists_the_kindles(pipeline, kindle, secrets):
    devices = _login(pipeline, kindle)

    assert [(d.serial, d.name) for d in devices] == [(KINDLE_SERIAL, "Erikas Kindle")]
    assert CREDENTIAL_NAME in secrets.values


def test_every_login_registers_its_own_serial(pipeline, kindle):
    _login(pipeline, kindle)
    _login(pipeline, kindle)

    first, second = kindle.serials
    assert first != second
    assert len(first) == 32 and first.isalnum() and first.isupper()


@pytest.mark.parametrize("redirect, message", [
    ("https://www.amazon.com/gp/sendtokindle?openid.assoc_handle=amzn_device_na", "no authorization code"),
    ("https://www.amazon.com/gp/sendtokindle?openid.oa2.authorization_code=ANunknown", "Invalid authorization code"),
])
def test_bad_redirect_url_stores_nothing(pipeline, secrets, redirect, message):
    with pytest.raises(KindleError, match=message):
        pipeline.kindle_login(start_login(), redirect)

    assert CREDENTIAL_NAME not in secrets.values


def test_login_only_works_with_the_verifier_it_started_with(pipeline, kindle, secrets):
    redirect = kindle.authorize(start_login().url)

    with pytest.raises(KindleError, match="Code verifier mismatch"):
        pipeline.kindle_login(start_login(), redirect)


def test_run_sends_new_issues_with_the_publication_as_author(pipeline, substack, kindle, output_dir):
    _login(pipeline, kindle)
    _publish(substack, 2, 1)

    report = pipeline.run(PUBLICATIONS, kindle=True)

    assert [(s["title"], s["author"], s["inputFormat"]) for s in kindle.sent] == [
        ("Post 1", "The Example Letter", "epub"),
        ("Post 2", "The Example Letter", "epub"),
    ]
    assert all(s["targets"] == [KINDLE_SERIAL] for s in kindle.sent)
    assert kindle.sent[1]["data"] == (output_dir / "The Example Letter" / "2026-03-02 Post 2.epub").read_bytes()
    assert sorted(report.kindle.pushed) == sorted(report.converted)
    assert report.kindle.errors == [] and report.kindle.waiting == 0


def test_title_comes_from_the_epub_not_the_file_name(pipeline, substack, kindle):
    _login(pipeline, kindle)
    substack.publish(make_post_json(title="Größe: “Quoted” / isn’t a problem?"))

    pipeline.run(PUBLICATIONS, kindle=True)

    assert [s["title"] for s in kindle.sent] == ["Größe: “Quoted” / isn’t a problem?"]


def test_each_issue_is_sent_once(pipeline, substack, kindle):
    _login(pipeline, kindle)
    _publish(substack, 1)
    pipeline.run(PUBLICATIONS, kindle=True)
    requests = kindle.api_requests

    report, = pipeline.push(kindle=True)

    assert report.pushed == [] and len(kindle.sent) == 1
    assert kindle.api_requests == requests, "nothing to send means no traffic to Amazon"


def test_regenerated_issue_is_sent_again(pipeline, substack, kindle, output_dir):
    _login(pipeline, kindle)
    _publish(substack, 1)
    pipeline.run(PUBLICATIONS, kindle=True)
    local = output_dir / "The Example Letter" / "2026-03-01 Post 1.epub"
    local.write_bytes(local.read_bytes() + b"regenerated")

    report, = pipeline.push(kindle=True)

    assert report.pushed == [local]
    assert kindle.sent[-1]["data"] == local.read_bytes()


def test_without_a_login_issues_wait_and_the_user_is_told_once(pipeline, substack, kindle, notifier):
    _publish(substack, 1)

    report = pipeline.run(PUBLICATIONS, kindle=True).kindle
    pipeline.push(kindle=True)

    assert report.errors == ["No Kindle login stored: run 'paperboy kindle-login'"]
    assert report.waiting == 1 and kindle.sent == []
    assert notifier.messages == [("paperboy: Kindle login needed", "New issues wait for the Kindle. Run: paperboy kindle-login")]

    _login(pipeline, kindle)
    report, = pipeline.push(kindle=True)

    assert len(report.pushed) == 1 and len(kindle.sent) == 1


def test_rejected_login_waits_for_a_new_one_and_notifies_again_after_it(pipeline, substack, kindle, notifier):
    _login(pipeline, kindle)
    _publish(substack, 1)
    kindle.revoke()

    report = pipeline.run(PUBLICATIONS, kindle=True).kindle

    assert report.errors == [
        "Send to Kindle /import/kindle-doc/get-device-list failed: HTTP 403 Failed to validate DeviceInfoToken.: "
        "run 'paperboy kindle-login'"
    ]
    assert report.waiting == 1 and len(notifier.messages) == 1

    _login(pipeline, kindle)
    report, = pipeline.push(kindle=True)
    assert len(report.pushed) == 1

    kindle.revoke()
    _publish(substack, 2)
    pipeline.run(PUBLICATIONS, kindle=True)
    assert len(notifier.messages) == 2, "a new login re-arms the notification"


def test_rejected_document_is_an_error_and_retried(pipeline, substack, kindle):
    _login(pipeline, kindle)
    _publish(substack, 2, 1)
    kindle.reject_titles = {"Post 1"}

    report = pipeline.run(PUBLICATIONS, kindle=True).kindle

    assert [p.name for p in report.pushed] == ["2026-03-02 Post 2.epub"]
    assert report.errors == ["Send to Kindle /import/kindle-doc/send-to-kindle failed: HTTP 400 Document could not be converted"]
    assert report.waiting == 1

    kindle.reject_titles = set()
    report, = pipeline.push(kindle=True)

    assert [p.name for p in report.pushed] == ["2026-03-01 Post 1.epub"]
    assert [s["title"] for s in kindle.sent] == ["Post 2", "Post 1"]


def test_unreachable_amazon_keeps_issues_waiting_without_an_error(pipeline, substack, kindle):
    _login(pipeline, kindle)
    _publish(substack, 1)
    kindle.online = False

    report = pipeline.run(PUBLICATIONS, kindle=True).kindle

    assert not report.reachable and report.waiting == 1 and report.errors == []

    kindle.online = True
    report, = pipeline.push(kindle=True)
    assert len(report.pushed) == 1


def test_account_without_a_kindle_is_an_error(pipeline, substack, kindle):
    _login(pipeline, kindle)
    kindle.devices = []
    _publish(substack, 1)

    report = pipeline.run(PUBLICATIONS, kindle=True).kindle

    assert report.errors == ["No Kindle on the Amazon account to send to"] and report.waiting == 1


def test_boox_and_kindle_deliver_independently(pipeline, substack, boox, kindle):
    _login(pipeline, kindle)
    _publish(substack, 1)
    boox.online = False

    report = pipeline.run(PUBLICATIONS, BOOX, kindle=True)

    assert not report.push.reachable and report.push.waiting == 1
    assert len(report.kindle.pushed) == 1

    boox.online = True
    kindle.revoke()
    boox_report, kindle_report = pipeline.push(BOOX, kindle=True)

    assert len(boox_report.pushed) == 1 and boox.library() == ["The Example Letter/2026-03-01 Post 1.epub"]
    assert kindle_report.pushed == [] and kindle_report.errors == [], "already sent: Amazon isn't even asked"


def test_issues_pushed_to_the_boox_before_the_kindle_existed_are_sent_to_the_kindle(
    pipeline, cli, substack, boox, kindle, state_file, write_config, capsys
):
    _publish(substack, 1)
    boox_config = f'[[publications]]\nurl = "https://{HOST}"\n\n[boox]\nurl = "{BOOX_URL}"\n'
    cli("run", "--config", str(write_config(boox_config)))
    _rewrite_as_state_store_before_the_kindle(state_file)
    _login(pipeline, kindle)
    capsys.readouterr()

    assert cli("push", "--config", str(write_config(boox_config + "\n[kindle]\n"))) == 0

    out = capsys.readouterr().out
    assert "The Boox is up to date." in out and "1 sent to the Kindle, 0 errors" in out
    assert len(boox.uploads) == 1 and len(kindle.sent) == 1


def _rewrite_as_state_store_before_the_kindle(state_file) -> None:
    """Back then, one table recorded pushes, all to the Boox."""
    db = sqlite3.connect(state_file)
    with db:
        rows = db.execute("SELECT file, size, pushed_at FROM deliveries WHERE target = 'boox'").fetchall()
        db.execute("DROP TABLE deliveries")
        db.execute("CREATE TABLE pushes (file TEXT PRIMARY KEY, size INTEGER NOT NULL, pushed_at TEXT NOT NULL)")
        db.executemany("INSERT INTO pushes VALUES (?, ?, ?)", rows)
    db.close()


def test_only_finished_epubs_are_sent(pipeline, kindle, output_dir):
    _login(pipeline, kindle)
    folder = output_dir / "The Example Letter"
    folder.mkdir(parents=True)
    (folder / ".2026-03-01 Half written.epub.tmp").write_bytes(b"partial")
    (folder / "2026-03-02 Finished.epub").write_bytes(b"not a real epub")

    report, = pipeline.push(kindle=True)

    assert len(report.pushed) == 1
    assert [(s["title"], s["author"]) for s in kindle.sent] == [("2026-03-02 Finished", "The Example Letter")]


def test_cli_kindle_login(cli, kindle, secrets, write_config, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(kindle.authorize() + "\n"))

    assert cli("kindle-login", "--config", str(write_config(KINDLE_CONFIG))) == 0

    out = capsys.readouterr().out
    assert "https://www.amazon.com/ap/signin?" in out
    assert "Kindle login stored in the Keychain." in out and "New issues go to: Erikas Kindle" in out
    assert CREDENTIAL_NAME in secrets.values


def test_cli_kindle_login_with_a_wrong_url_fails(cli, secrets, write_config, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("https://www.amazon.com/\n"))

    assert cli("kindle-login", "--config", str(write_config(KINDLE_CONFIG))) == 1
    assert "no authorization code" in capsys.readouterr().err
    assert CREDENTIAL_NAME not in secrets.values


def test_cli_run_and_push_report_the_kindle(pipeline, cli, substack, kindle, write_config, capsys):
    _login(pipeline, kindle)
    _publish(substack, 1)
    config = str(write_config(KINDLE_CONFIG))

    assert cli("run", "--config", config) == 0
    out = capsys.readouterr().out
    assert "sent to the Kindle: " in out and "1 sent to the Kindle, 0 errors" in out

    assert cli("push", "--quiet", "--config", config) == 0
    assert capsys.readouterr().out == ""
    assert cli("push", "--config", config) == 0
    assert "The Kindle is up to date." in capsys.readouterr().out


def test_cli_run_without_kindle_login_exits_nonzero(cli, substack, write_config, capsys):
    _publish(substack, 1)

    assert cli("run", "--config", str(write_config(KINDLE_CONFIG))) == 1
    out = capsys.readouterr().out
    assert "error: No Kindle login stored: run 'paperboy kindle-login'" in out
    assert "0 sent to the Kindle, 1 errors" in out


def test_status_shows_the_kindle(pipeline, cli, substack, kindle, write_config, capsys):
    _publish(substack, 1)
    pipeline.run(PUBLICATIONS, kindle=True)
    config = str(write_config(KINDLE_CONFIG))

    cli("status", "--config", config)
    out = capsys.readouterr().out
    assert "Kindle login: not stored (run 'paperboy kindle-login'), 1 issues not sent yet" in out
    assert "All good" not in out

    _login(pipeline, kindle)
    pipeline.push(kindle=True)
    cli("status", "--config", config)
    assert "Kindle login: stored, all issues sent" in capsys.readouterr().out


def test_kindle_section_must_be_a_table(cli, write_config, capsys):
    assert cli("push", "--config", str(write_config("kindle = true\n"))) == 2
    assert "'kindle' must be a [kindle] section" in capsys.readouterr().err
