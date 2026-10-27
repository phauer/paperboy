from conftest import HOST, NOW, VALID_COOKIE, numbered_post
from paperboy.config import Publication

FULL_BODY = "<p>" + "Full paid text. " * 50 + "</p>"
OTHER = "other.example.com"


def _status(cli, write_config, capsys, publications=(HOST,)) -> str:
    capsys.readouterr()
    config = write_config("".join(f'[[publications]]\nurl = "https://{host}"\n' for host in publications))
    assert cli("status", "--config", str(config)) == 0
    return capsys.readouterr().out


def test_before_the_first_run(cli, write_config, capsys):
    out = _status(cli, write_config, capsys)

    assert "Last run: none yet" in out
    assert f"{HOST}: 0 delivered" in out
    assert "All good" not in out


def test_clean_run_is_all_good_and_summarizes_backfill(pipeline, substack, cli, write_config, capsys):
    substack.publish(*(numbered_post(n) for n in (5, 4, 3, 2, 1)))
    pipeline.run([Publication(HOST, backfill=3)])

    out = _status(cli, write_config, capsys)

    assert f"Last run: {NOW.astimezone():%Y-%m-%d %H:%M}, no errors" in out
    assert f"{HOST}: 3 delivered, 2 older posts skipped at first run" in out
    assert "/p/post-1" not in out, "backfill skips are counted, not listed"
    assert "All good: nothing is waiting or failed." in out


def test_posts_waiting_for_login_are_listed_with_reason(pipeline, substack, secrets, cli, write_config, capsys):
    secrets.values["substack.sid"] = "s%3Aexpired.signature"
    substack.add_paid(numbered_post(2, body_html=FULL_BODY, wordcount=150))
    substack.add(numbered_post(1))
    substack.feeds[HOST] = ["post-2", "post-1"]
    pipeline.run([Publication(HOST, backfill=3)])

    out = _status(cli, write_config, capsys)

    assert f"{HOST}: 1 delivered" in out
    assert "  Waiting for login (1):" in out
    assert f"    {NOW.astimezone():%Y-%m-%d}  https://{HOST}/p/post-2\n" in out
    assert "Substack returned only the teaser (cookie expired?)\n" in out, "reason without the URL again"
    assert "set-cookie" in out
    assert "All good" not in out


def test_cookie_presence_is_shown_but_never_its_value(cli, secrets, write_config, capsys):
    assert "Substack cookie: stored" in (out := _status(cli, write_config, capsys))
    assert VALID_COOKIE not in out

    secrets.values.clear()

    assert "Substack cookie: not stored" in _status(cli, write_config, capsys)


def test_errors_of_the_last_run_are_shown_until_a_clean_run(pipeline, substack, cli, write_config, capsys):
    substack.publish(numbered_post(1))
    substack.errors[f"https://{HOST}/feed"] = 500
    pipeline.run([Publication(HOST, backfill=3)])

    out = _status(cli, write_config, capsys)

    assert "no errors" not in out and ", 1 error:\n  - " in out
    assert "500" in out
    assert "All good" not in out

    del substack.errors[f"https://{HOST}/feed"]
    pipeline.run([Publication(HOST, backfill=3)])

    assert "All good" in _status(cli, write_config, capsys)


def test_failed_and_other_skipped_posts_are_listed(state, cli, write_config, capsys):
    state.record_post(HOST, "broken", url=f"https://{HOST}/p/broken", status="failed", reason="epubcheck: RSC-005", at=NOW)
    state.record_post(HOST, "podcast", url=f"https://{HOST}/p/podcast", status="skipped", reason="podcast with 80 words", at=NOW)
    state.record_run(NOW, [])

    out = _status(cli, write_config, capsys)

    assert f"  Failed (1):\n    {NOW.astimezone():%Y-%m-%d}  https://{HOST}/p/broken\n" in out
    assert "epubcheck: RSC-005" in out
    assert f"  Skipped (1):\n    {NOW.astimezone():%Y-%m-%d}  https://{HOST}/p/podcast\n" in out
    assert "podcast with 80 words" in out
    assert "All good" not in out, "a failed post needs attention"


def test_publications_are_grouped_in_config_order(pipeline, substack, cli, write_config, capsys):
    substack.publish(numbered_post(1), host=OTHER)
    substack.publish(numbered_post(2), numbered_post(1))
    pipeline.run([Publication(OTHER, backfill=3), Publication(HOST, backfill=3)])

    out = _status(cli, write_config, capsys, publications=(HOST, OTHER))

    assert out.index(f"{HOST}: 2 delivered") < out.index(f"{OTHER}: 1 delivered")


def test_publication_removed_from_config_still_shows_its_posts(pipeline, substack, secrets, cli, write_config, capsys):
    secrets.values.clear()
    substack.add_paid(numbered_post(1, body_html=FULL_BODY, wordcount=150), host=OTHER)
    substack.feeds[OTHER] = ["post-1"]
    pipeline.run([Publication(OTHER, backfill=3)])

    out = _status(cli, write_config, capsys)

    assert f"{OTHER}: 0 delivered" in out
    assert f"https://{OTHER}/p/post-1" in out


def test_status_does_not_touch_the_network(cli, substack, write_config, capsys):
    _status(cli, write_config, capsys)

    assert substack.requests == []
