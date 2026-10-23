from pathlib import Path

import pytest

from conftest import HOST, assert_epubcheck_clean, make_post_json, read_epub
from paperboy.cli import main
from paperboy.substack import SourceError


def test_writes_epub_into_publication_folder_with_date_prefix(pipeline, substack, output_dir):
    url = substack.add(make_post_json())

    path = pipeline.convert(url)

    assert path == output_dir / "The Example Letter" / "2026-03-14 A Fine Post.epub"
    assert path.is_file()
    assert [p.name for p in path.parent.iterdir()] == [path.name], "no temp files left behind"


def test_epub_has_title_page_body_and_source_link(pipeline, substack):
    url = substack.add(make_post_json())

    files = read_epub(pipeline.convert(url))

    title_page = files["EPUB/title.xhtml"]
    assert "A Fine Post" in title_page
    assert "About fine things" in title_page
    assert "Erika Mustermann" in title_page
    assert "2026-03-14" in title_page
    content = files["EPUB/content.xhtml"]
    assert "Body text of section one." in content
    assert content.rindex(f"https://{HOST}/p/a-fine-post") > content.index("Body text of section one.")


def test_epub_passes_epubcheck(pipeline, substack):
    url = substack.add(make_post_json())

    assert_epubcheck_clean(pipeline.convert(url))


def test_epub_metadata(pipeline, substack):
    url = substack.add(make_post_json(language="de"))

    opf = read_epub(pipeline.convert(url))["EPUB/content.opf"]

    assert "<dc:title>A Fine Post</dc:title>" in opf
    assert "Erika Mustermann</dc:creator>" in opf
    assert "<dc:publisher>The Example Letter</dc:publisher>" in opf
    assert "<dc:language>de</dc:language>" in opf


def test_widgets_are_removed_but_captions_kept(pipeline, substack):
    url = substack.add(make_post_json())

    content = read_epub(pipeline.convert(url))["EPUB/content.xhtml"]

    for tag in ("<picture", "<source", "<svg", "<button"):
        assert tag not in content
    assert "Source: Max Mustermann" in content


def test_unsafe_and_long_titles_become_safe_file_names(pipeline, substack, output_dir):
    long_title = "Why? A/B tests: <the> \"truth\" | " + "really " * 30 + "end"
    url = substack.add(make_post_json(title=long_title))

    path = pipeline.convert(url)

    assert path.parent == output_dir / "The Example Letter"
    stem = path.stem.removeprefix("2026-03-14 ")
    assert stem.startswith("Why A B tests the truth really")
    assert len(stem) <= 100
    assert not any(c in path.name for c in '\\/:*?"<>|')
    assert_epubcheck_clean(path)


def test_publication_falls_back_to_host_without_bylines(pipeline, substack, output_dir):
    url = substack.add(make_post_json(publishedBylines=[]))

    path = pipeline.convert(url)

    assert path.parent == output_dir / HOST
    assert_epubcheck_clean(path)


def test_empty_body_still_produces_valid_epub(pipeline, substack):
    url = substack.add(make_post_json(body_html=""))

    assert_epubcheck_clean(pipeline.convert(url))


@pytest.mark.parametrize("url", ["https://letter.example.com/", "https://letter.example.com/about", "not a url"])
def test_rejects_urls_that_are_not_posts(pipeline, url):
    with pytest.raises(SourceError, match="Not a Substack post URL"):
        pipeline.convert(url)


def test_unknown_post_reports_not_found(pipeline):
    with pytest.raises(SourceError, match="Post not found"):
        pipeline.convert(f"https://{HOST}/p/does-not-exist")


def test_server_error_is_reported(pipeline, substack):
    url = substack.add(make_post_json())
    substack.errors[substack.api_url("a-fine-post")] = 503

    with pytest.raises(SourceError, match="HTTP 503"):
        pipeline.convert(url)


def test_cli_convert_prints_path(substack, write_config, capsys):
    url = substack.add(make_post_json())

    code = main(["convert", url, "--config", str(write_config())], transport=substack.transport())

    assert code == 0
    assert Path(capsys.readouterr().out.strip()).is_file()


def test_cli_output_option_overrides_config(substack, write_config, tmp_path):
    url = substack.add(make_post_json())

    code = main(
        ["convert", url, "--config", str(write_config()), "--output", str(tmp_path / "elsewhere")],
        transport=substack.transport(),
    )

    assert code == 0
    assert (tmp_path / "elsewhere" / "The Example Letter").is_dir()


def test_cli_reports_missing_config(substack, tmp_path, capsys):
    code = main(["convert", "https://x.example.com/p/y", "--config", str(tmp_path / "nope.toml")],
                transport=substack.transport())

    assert code == 2
    assert "Config file not found" in capsys.readouterr().err


def test_cli_reports_source_errors(substack, write_config, capsys):
    code = main(["convert", f"https://{HOST}/p/missing", "--config", str(write_config())],
                transport=substack.transport())

    assert code == 1
    assert "Post not found" in capsys.readouterr().err
