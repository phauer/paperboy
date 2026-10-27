import io
import re

import pytest
from PIL import Image

from conftest import assert_epubcheck_clean, make_post_json, numbered_post, read_epub, read_epub_bytes
from paperboy import cover


def _convert(pipeline, substack, post_json):
    path = pipeline.convert(substack.add(post_json))
    assert_epubcheck_clean(path)
    return path


def _cover(path) -> Image.Image:
    return Image.open(io.BytesIO(read_epub_bytes(path)["EPUB/cover.jpg"]))


def _ink(image: Image.Image, box) -> int:
    """Number of dark pixels in a region."""
    return sum(image.crop(box).histogram()[:128])


def test_epub_has_a_screen_sized_grayscale_cover(pipeline, substack):
    path = _convert(pipeline, substack, make_post_json())

    image = _cover(path)
    opf = read_epub(path)["EPUB/content.opf"]
    assert (image.format, image.mode, image.size) == ("JPEG", "L", (1264, 1680))
    assert re.search(r'<item href="cover.jpg" id="([^"]+)" media-type="image/jpeg" properties="cover-image"/>', opf)
    assert '<meta name="cover" content="cover-img">' in opf


def test_cover_shows_text_in_header_title_and_footer(pipeline, substack):
    image = _cover(_convert(pipeline, substack, make_post_json()))

    assert _ink(image, (0, 0, 1264, 300)) > 1000, "publication name"
    assert _ink(image, (0, 420, 1264, 1300)) > 10000, "title"
    assert _ink(image, (0, 1450, 1264, 1680)) > 1000, "author and date"


@pytest.mark.parametrize(
    ("author", "publication"),
    [
        ("Erika Mustermann", "The Example Letter"),
        ("Erika Mustermann-Musterfrau von und zu Beispielhausen", "The Very Long Example Letter on Engineering ⚡️"),
    ],
)
def test_header_and_footer_leave_the_kindle_badge_corners_free(pipeline, substack, author, publication):
    byline = {"id": 1, "name": author, "publicationUsers": [{"publication": {"id": 7, "name": publication}}]}
    image = _cover(_convert(pipeline, substack, make_post_json(publishedBylines=[byline])))

    assert _ink(image, (794, 0, 1264, 180)) == 0, "progress and read badges at the top right (the rule below may pass under it)"
    assert _ink(image, (0, 1450, 470, 1680)) == 0, "check symbol at the bottom left"
    assert _ink(image, (0, 0, 470, 260)) > 1000, "publication name, left-aligned"
    assert _ink(image, (800, 1450, 1264, 1680)) > 1000, "author and date, right-aligned"


def test_different_titles_get_different_covers(pipeline, substack):
    first = read_epub_bytes(_convert(pipeline, substack, numbered_post(1)))["EPUB/cover.jpg"]
    second = read_epub_bytes(_convert(pipeline, substack, numbered_post(2)))["EPUB/cover.jpg"]

    assert first != second


@pytest.mark.parametrize(
    "title",
    [
        "Why " + "really " * 80 + "long titles end in an ellipsis",
        "Supercalifragilisticexpialidocious" * 4,
        "Ünïcödé — “quotes”, emoji 🚀 and 日本語",
    ],
)
def test_unusual_titles_render(pipeline, substack, title):
    image = _cover(_convert(pipeline, substack, make_post_json(title=title, subtitle="")))

    assert image.size == (1264, 1680)


def test_cover_renders_without_macos_fonts(pipeline, substack, monkeypatch):
    monkeypatch.setattr(cover, "_FONT_FILE", "/nonexistent/font.ttc")
    cover._font.cache_clear()
    try:
        image = _cover(_convert(pipeline, substack, make_post_json()))
    finally:
        cover._font.cache_clear()

    assert _ink(image, (0, 420, 1264, 1300)) > 1000


def test_metadata_describes_the_issue(pipeline, substack):
    opf = read_epub(_convert(pipeline, substack, make_post_json()))["EPUB/content.opf"]

    assert "<dc:date>2026-03-14</dc:date>" in opf
    assert "<dc:description>About fine things</dc:description>" in opf
    assert "<dc:language>en</dc:language>" in opf


def test_no_description_without_subtitle(pipeline, substack):
    opf = read_epub(_convert(pipeline, substack, make_post_json(subtitle="")))["EPUB/content.opf"]

    assert "dc:description" not in opf


def test_publication_is_a_series_indexed_by_date(pipeline, substack):
    opf = read_epub(_convert(pipeline, substack, make_post_json()))["EPUB/content.opf"]

    assert '<meta property="belongs-to-collection" id="series">The Example Letter</meta>' in opf
    assert '<meta refines="#series" property="collection-type">series</meta>' in opf
    assert '<meta refines="#series" property="group-position">20260314</meta>' in opf
    assert '<meta name="calibre:series" content="The Example Letter">' in opf
    assert '<meta name="calibre:series_index" content="20260314">' in opf


def test_series_index_sorts_issues_chronologically(pipeline, substack):
    indexes = []
    for number in (12, 3, 7):
        opf = read_epub(_convert(pipeline, substack, numbered_post(number)))["EPUB/content.opf"]
        indexes.append(int(re.search(r'calibre:series_index" content="(\d+)"', opf).group(1)))

    assert indexes == [20260312, 20260303, 20260307]


def _cover_with(pipeline, substack, slug, title="A Fine Post", publication="The Example Letter", author="Erika Mustermann"):
    byline = {"id": 1, "name": author, "publicationUsers": [{"publication": {"id": 7, "name": publication}}]}
    post = make_post_json(slug=slug, title=title, publishedBylines=[byline])
    return read_epub_bytes(pipeline.convert(substack.add(post)))["EPUB/cover.jpg"]


@pytest.mark.parametrize(
    "field, with_symbols, plain",
    [
        ("publication", "Engineering Leader's Playbook ⚡️", "Engineering Leader's Playbook"),
        ("publication", "🚀 Launch 🚀 Notes", "Launch Notes"),
        ("title", "Ship it → 🚀 now", "Ship it now"),
        ("title", "日本 Tokyo notes", "Tokyo notes"),
        ("author", "Erika 👩🏽‍💻 Mustermann", "Erika Mustermann"),
    ],
)
def test_characters_the_font_cannot_draw_are_left_out(pipeline, substack, field, with_symbols, plain):
    assert _cover_with(pipeline, substack, "a", **{field: with_symbols}) == _cover_with(pipeline, substack, "b", **{field: plain})


def test_undrawable_publication_and_author_leave_their_lines_out(pipeline, substack):
    image = Image.open(io.BytesIO(_cover_with(pipeline, substack, "a", publication="⚡️🚀", author="👩🏽‍💻")))

    assert _ink(image, (0, 0, 1264, 110)) == 0, "no publication line, only the rule below"
    assert _ink(image, (0, 1450, 1264, 1525)) == 0, "no author line, only the date below"
    assert _ink(image, (800, 1525, 1264, 1680)) > 1000, "date"


def test_undrawable_title_is_kept_so_the_cover_has_a_title(pipeline, substack):
    image = Image.open(io.BytesIO(_cover_with(pipeline, substack, "a", title="🚀🚀🚀")))

    assert _ink(image, (0, 420, 1264, 700)) > 1000


def test_only_the_cover_drops_characters(pipeline, substack):
    byline = {"id": 1, "name": "Erika 🚀", "publicationUsers": [{"publication": {"id": 7, "name": "Playbook ⚡️"}}]}
    path = pipeline.convert(substack.add(make_post_json(title="Ship it 🚀", publishedBylines=[byline])))

    epub = read_epub(path)
    assert path.parent.name == "Playbook ⚡️" and path.name.endswith("Ship it 🚀.epub")
    assert "<dc:publisher>Playbook ⚡️</dc:publisher>" in epub["EPUB/content.opf"]
    assert "<dc:title>Ship it 🚀</dc:title>" in epub["EPUB/content.opf"]
    assert "Ship it 🚀" in epub["EPUB/title.xhtml"] and "Erika 🚀" in epub["EPUB/title.xhtml"]


def test_fallback_font_drops_what_it_cannot_draw(pipeline, substack, monkeypatch):
    monkeypatch.setattr(cover, "_FONT_FILE", "/nonexistent/font.ttc")
    cover._font.cache_clear()
    try:
        with_symbols = _cover_with(pipeline, substack, "a", publication="Letter ⚡️ Weekly")
        plain = _cover_with(pipeline, substack, "b", publication="Letter Weekly")
    finally:
        cover._font.cache_clear()

    assert with_symbols == plain
