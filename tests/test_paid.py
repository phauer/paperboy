import io

import pytest

from conftest import HOST, VALID_COOKIE, numbered_post, read_epub
from paperboy.config import Publication
from paperboy.substack import AuthExpired

FULL_BODY = "<p>" + "Full paid text. " * 50 + "</p>"
PUBLICATION = Publication(HOST, backfill=3)


def _paid(number: int) -> dict:
    return numbered_post(number, body_html=FULL_BODY, wordcount=150)


def _content(path) -> str:
    return read_epub(path)["EPUB/content.xhtml"]


def test_paid_post_arrives_with_full_text(pipeline, substack):
    url = substack.add_paid(_paid(1))

    content = _content(pipeline.convert(url))

    assert "Full paid text." in content
    assert "Teaser paragraph" not in content


def test_cookie_is_only_sent_to_substack_com(pipeline, substack):
    substack.files["https://substackcdn.com/image/fetch/x.png"] = b"not relevant"
    url = substack.add_paid(_paid(1))

    pipeline.convert(url)

    assert substack.cookies_sent == [("substack.com", f"substack.sid={VALID_COOKIE}")]


def test_free_posts_work_without_cookie(pipeline, substack, secrets):
    secrets.values.clear()
    url = substack.add(numbered_post(1))

    assert "Body text of section one." in _content(pipeline.convert(url))
    assert substack.cookies_sent == []


@pytest.mark.parametrize("cookie", [None, "s%3Aexpired.signature"])
def test_convert_refuses_teaser_without_valid_cookie(pipeline, substack, secrets, output_dir, cookie):
    secrets.values.clear()
    if cookie:
        secrets.values["substack.sid"] = cookie
    url = substack.add_paid(_paid(1))

    with pytest.raises(AuthExpired):
        pipeline.convert(url)

    assert not output_dir.exists() or not list(output_dir.rglob("*.epub"))


def test_paid_post_without_declared_wordcount_needs_a_body(pipeline, substack):
    url = substack.add_paid(numbered_post(1, body_html="", wordcount=None))

    with pytest.raises(AuthExpired):
        pipeline.convert(url)


def test_expired_cookie_holds_paid_posts_and_delivers_free_ones(pipeline, substack, secrets, output_dir):
    secrets.values["substack.sid"] = "s%3Aexpired.signature"
    substack.add_paid(_paid(2))
    substack.add(numbered_post(1))
    substack.feeds[HOST] = ["post-2", "post-1"]

    report = pipeline.run([PUBLICATION])

    assert [p.name for p in report.converted] == ["2026-03-01 Post 1.epub"]
    assert report.pending_auth == 1
    assert sorted(p.name for p in output_dir.rglob("*.epub")) == ["2026-03-01 Post 1.epub"]


def test_expiry_is_notified_once_across_posts_and_runs(pipeline, substack, secrets, notifier):
    secrets.values.clear()
    for number in (3, 2, 1):
        substack.add_paid(_paid(number))
    substack.feeds[HOST] = ["post-3", "post-2", "post-1"]

    pipeline.run([PUBLICATION])
    pipeline.run([PUBLICATION])

    assert len(notifier.messages) == 1
    title, message = notifier.messages[0]
    assert "login" in title.lower() and "set-cookie" in message


def test_pending_posts_are_converted_after_new_cookie(cli, pipeline, substack, secrets, notifier, write_config, monkeypatch, output_dir):
    secrets.values["substack.sid"] = "s%3Aexpired.signature"
    for number in (2, 1):
        substack.add_paid(_paid(number))
    substack.feeds[HOST] = ["post-2", "post-1"]
    assert pipeline.run([PUBLICATION]).pending_auth == 2

    monkeypatch.setattr("sys.stdin", io.StringIO(f"substack.sid={VALID_COOKIE}\n"))
    assert cli("set-cookie", "--config", str(write_config())) == 0
    report = pipeline.run([PUBLICATION])

    assert sorted(p.name for p in report.converted) == ["2026-03-01 Post 1.epub", "2026-03-02 Post 2.epub"]
    assert all("Full paid text." in _content(p) for p in report.converted)
    assert report.pending_auth == 0


def test_pending_post_is_retried_even_after_leaving_the_feed(pipeline, substack, secrets):
    secrets.values.clear()
    substack.add_paid(_paid(1))
    substack.feeds[HOST] = ["post-1"]
    pipeline.run([PUBLICATION])
    substack.feeds[HOST] = []
    secrets.values["substack.sid"] = VALID_COOKIE

    report = pipeline.run([PUBLICATION])

    assert [p.name for p in report.converted] == ["2026-03-01 Post 1.epub"]


def test_new_expiry_after_set_cookie_is_notified_again(cli, pipeline, substack, secrets, notifier, write_config, monkeypatch):
    secrets.values.clear()
    substack.add_paid(_paid(1))
    substack.feeds[HOST] = ["post-1"]
    pipeline.run([PUBLICATION])

    monkeypatch.setattr("sys.stdin", io.StringIO("s%3Aalso-expired.signature\n"))
    cli("set-cookie", "--config", str(write_config()))
    pipeline.run([PUBLICATION])

    assert len(notifier.messages) == 2


def test_pending_posts_within_backfill_stay_pending_not_skipped(pipeline, substack, secrets):
    secrets.values.clear()
    for number in (3, 2, 1):
        substack.add_paid(_paid(number))
    substack.feeds[HOST] = ["post-3", "post-2", "post-1"]

    report = pipeline.run([Publication(HOST, backfill=2)])

    assert report.pending_auth == 2
    assert report.skipped == 1


@pytest.mark.parametrize(
    "pasted, stored",
    [
        (VALID_COOKIE, VALID_COOKIE),
        (f"substack.sid={VALID_COOKIE}", VALID_COOKIE),
        (f"  ab_testing_id=123; substack.sid={VALID_COOKIE}; other=x  ", VALID_COOKIE),
    ],
)
def test_set_cookie_accepts_common_paste_formats(cli, secrets, write_config, monkeypatch, capsys, pasted, stored):
    secrets.values.clear()
    monkeypatch.setattr("sys.stdin", io.StringIO(pasted + "\n"))

    assert cli("set-cookie", "--config", str(write_config())) == 0
    assert secrets.values == {"substack.sid": stored}
    assert stored not in capsys.readouterr().out, "the cookie is never echoed"


def test_set_cookie_explains_empty_non_interactive_input(cli, secrets, write_config, monkeypatch, capsys):
    secrets.values.clear()
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    assert cli("set-cookie", "--config", str(write_config())) == 2
    assert secrets.values == {}
    assert "pbpaste | paperboy set-cookie" in capsys.readouterr().err


@pytest.mark.parametrize("pasted", ["ab_testing_id=1; other=2", "two words"])
def test_set_cookie_rejects_invalid_input(cli, secrets, write_config, monkeypatch, capsys, pasted):
    secrets.values.clear()
    monkeypatch.setattr("sys.stdin", io.StringIO(pasted + "\n"))

    assert cli("set-cookie", "--config", str(write_config())) == 2
    assert secrets.values == {}
    assert "Expected the value of the substack.sid cookie" in capsys.readouterr().err


def test_cli_run_reports_waiting_posts(cli, substack, secrets, write_config, capsys):
    secrets.values.clear()
    substack.add_paid(_paid(1))
    substack.feeds[HOST] = ["post-1"]

    code = cli("run", "--config", str(write_config(f'[[publications]]\nurl = "https://{HOST}"\n')))

    out = capsys.readouterr().out
    assert code == 0
    assert "1 waiting for login" in out and "set-cookie" in out


def test_cli_convert_explains_missing_login(cli, substack, secrets, write_config, capsys):
    secrets.values.clear()
    url = substack.add_paid(_paid(1))

    assert cli("convert", url, "--config", str(write_config())) == 1
    assert "needs a Substack cookie" in capsys.readouterr().err


def test_cookie_never_appears_in_logs_or_state(pipeline, substack, secrets, state_file, caplog):
    secrets.values["substack.sid"] = "s%3Aexpired-secret.signature"
    substack.add_paid(_paid(1))
    substack.feeds[HOST] = ["post-1"]

    pipeline.run([PUBLICATION])

    assert "expired-secret" not in caplog.text
    assert b"expired-secret" not in state_file.read_bytes()
