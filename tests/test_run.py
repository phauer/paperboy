import pytest

from conftest import HOST, numbered_post
from paperboy.config import Publication
from paperboy.state import AlreadyRunning

OTHER = "other.example.com"


def _files(output_dir) -> list[str]:
    return sorted(p.name for p in output_dir.rglob("*.epub"))


def test_first_run_converts_only_the_newest_posts(pipeline, substack, output_dir):
    substack.publish(*(numbered_post(n) for n in (5, 4, 3, 2, 1)))

    report = pipeline.run([Publication(HOST, backfill=3)])

    assert _files(output_dir) == ["2026-03-03 Post 3.epub", "2026-03-04 Post 4.epub", "2026-03-05 Post 5.epub"]
    assert len(report.converted) == 3
    assert report.skipped == 2
    assert report.errors == []


def test_feed_order_does_not_matter_newest_by_date_wins(pipeline, substack, output_dir):
    substack.publish(*(numbered_post(n) for n in (1, 5, 2, 4, 3)))

    pipeline.run([Publication(HOST, backfill=2)])

    assert _files(output_dir) == ["2026-03-04 Post 4.epub", "2026-03-05 Post 5.epub"]


def test_second_run_without_new_posts_does_nothing(pipeline, substack, output_dir):
    substack.publish(*(numbered_post(n) for n in (5, 4, 3, 2, 1)))
    pipeline.run([Publication(HOST, backfill=3)])
    substack.requests.clear()

    report = pipeline.run([Publication(HOST, backfill=3)])

    assert report.converted == [] and report.skipped == 0
    assert substack.post_api_requests() == [], "no post is fetched again"


def test_later_run_converts_only_new_posts(pipeline, substack, output_dir):
    substack.publish(*(numbered_post(n) for n in (5, 4, 3, 2, 1)))
    pipeline.run([Publication(HOST, backfill=3)])
    substack.publish(numbered_post(7), numbered_post(6))

    report = pipeline.run([Publication(HOST, backfill=3)])

    assert sorted(p.name for p in report.converted) == ["2026-03-06 Post 6.epub", "2026-03-07 Post 7.epub"]
    assert "2026-03-01 Post 1.epub" not in _files(output_dir), "backfill-skipped posts stay skipped"


@pytest.mark.parametrize("backfill, expected", [(0, 0), (1, 1), (10, 3)])
def test_backfill_is_configurable(pipeline, substack, output_dir, backfill, expected):
    substack.publish(*(numbered_post(n) for n in (3, 2, 1)))

    report = pipeline.run([Publication(HOST, backfill=backfill)])

    assert len(report.converted) == expected
    assert report.skipped == 3 - expected


def test_publication_added_later_gets_its_own_backfill(pipeline, substack, output_dir):
    other_bylines = [{"name": "Max Mustermann", "publicationUsers": [{"publication": {"name": "Other Letter"}}]}]
    substack.publish(*(numbered_post(n) for n in (3, 2, 1)))
    substack.publish(*(numbered_post(n, publishedBylines=other_bylines) for n in (3, 2, 1)), host=OTHER)
    pipeline.run([Publication(HOST, backfill=1)])

    report = pipeline.run([Publication(HOST, backfill=1), Publication(OTHER, backfill=2)])

    assert sorted(str(p.relative_to(output_dir)) for p in report.converted) == [
        "Other Letter/2026-03-02 Post 2.epub",
        "Other Letter/2026-03-03 Post 3.epub",
    ]
    assert report.skipped == 1


def test_deleted_file_is_not_recreated(pipeline, substack, output_dir):
    substack.publish(numbered_post(1))
    [path] = pipeline.run([Publication(HOST, backfill=3)]).converted
    path.unlink()

    report = pipeline.run([Publication(HOST, backfill=3)])

    assert report.converted == []
    assert not path.exists()


def test_failing_feed_does_not_stop_other_publications(pipeline, substack, output_dir):
    substack.publish(numbered_post(1))
    substack.publish(numbered_post(2), host=OTHER)
    substack.errors[f"https://{HOST}/feed"] = 500

    report = pipeline.run([Publication(HOST, backfill=3), Publication(OTHER, backfill=3)])

    assert len(report.converted) == 1
    assert report.errors == [f"Feed of {HOST} returned HTTP 500"]


def test_publication_whose_first_feed_failed_still_gets_backfill_later(pipeline, substack):
    substack.publish(*(numbered_post(n) for n in (3, 2, 1)))
    substack.errors[f"https://{HOST}/feed"] = 503
    pipeline.run([Publication(HOST, backfill=2)])
    del substack.errors[f"https://{HOST}/feed"]

    report = pipeline.run([Publication(HOST, backfill=2)])

    assert len(report.converted) == 2
    assert report.skipped == 1


def test_invalid_feed_xml_is_reported(pipeline, substack):
    substack.files[f"https://{HOST}/feed"] = b"<html>maintenance</html"

    report = pipeline.run([Publication(HOST, backfill=3)])

    assert report.errors and "not valid XML" in report.errors[0]


def test_feed_links_that_are_not_posts_are_ignored(pipeline, substack):
    substack.files[f"https://{HOST}/feed"] = (
        f"<rss><channel><item><link>https://{HOST}/podcast</link></item>"
        f"<item><link>https://{HOST}/p/post-1</link></item></channel></rss>"
    ).encode()
    substack.add(numbered_post(1))

    report = pipeline.run([Publication(HOST, backfill=3)])

    assert len(report.converted) == 1 and report.errors == []


def test_transient_post_error_is_retried_next_run(pipeline, substack, output_dir):
    substack.publish(numbered_post(2), numbered_post(1))
    substack.errors[substack.api_url("post-2")] = 503

    first = pipeline.run([Publication(HOST, backfill=3)])
    del substack.errors[substack.api_url("post-2")]
    second = pipeline.run([Publication(HOST, backfill=3)])

    assert [p.name for p in first.converted] == ["2026-03-01 Post 1.epub"]
    assert first.errors == [f"Substack returned HTTP 503 for https://{HOST}/p/post-2"]
    assert [p.name for p in second.converted] == ["2026-03-02 Post 2.epub"]


def test_concurrent_run_is_refused(pipeline, substack, state, output_dir):
    substack.publish(numbered_post(1))

    with state.lock():
        with pytest.raises(AlreadyRunning):
            pipeline.run([Publication(HOST, backfill=3)])

    assert _files(output_dir) == []


def test_cli_run_converts_and_summarizes(cli, substack, write_config, capsys):
    substack.publish(numbered_post(2), numbered_post(1))
    config = write_config(f'backfill = 1\n\n[[publications]]\nurl = "https://{HOST}"\n')

    code = cli("run", "--config", str(config))

    assert code == 0
    assert "1 converted, 1 skipped, 0 waiting for login, 0 errors" in capsys.readouterr().out


def test_cli_run_with_per_publication_backfill(cli, substack, write_config, output_dir):
    substack.publish(numbered_post(2), numbered_post(1))
    config = write_config(f'[[publications]]\nurl = "{HOST}"\nbackfill = 0\n')

    assert cli("run", "--config", str(config)) == 0
    assert _files(output_dir) == []


def test_cli_run_exits_nonzero_on_errors(cli, substack, write_config):
    substack.errors[f"https://{HOST}/feed"] = 500
    config = write_config(f'[[publications]]\nurl = "https://{HOST}"\n')

    assert cli("run", "--config", str(config)) == 1


def test_cli_run_reports_concurrent_run(cli, substack, write_config, state, capsys):
    config = write_config(f'[[publications]]\nurl = "https://{HOST}"\n')

    with state.lock():
        code = cli("run", "--config", str(config))

    assert code == 0
    assert "Another run is in progress" in capsys.readouterr().out


@pytest.mark.parametrize(
    "toml, message",
    [
        ("", "no [[publications]] configured"),
        ('[[publications]]\nbackfill = 1\n', "publication #1 needs a 'url'"),
        ('[[publications]]\nurl = "https://"\n', "publication #1 needs a 'url'"),
        (f'[[publications]]\nurl = "https://{HOST}"\nbackfill = -1\n', "'publication #1.backfill' must be a whole number"),
        (f'backfill = "3"\n[[publications]]\nurl = "https://{HOST}"\n', "'backfill' must be a whole number"),
        (f'[[publications]]\nurl = "https://{HOST}"\n[[publications]]\nurl = "{HOST}/"\n', f"publication #2 duplicates {HOST}"),
        ('publications = "nope"\n', "'publications' must be a list"),
        ("this is not toml", "Invalid TOML"),
    ],
)
def test_cli_run_reports_config_errors(cli, substack, write_config, capsys, toml, message):
    code = cli("run", "--config", str(write_config(toml)))

    assert code == 2
    assert message in capsys.readouterr().err
