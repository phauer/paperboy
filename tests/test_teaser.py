import io

import pytest
from PIL import Image

from conftest import HOST, assert_epubcheck_clean, numbered_post, read_epub, read_epub_bytes
from paperboy.config import Publication
from paperboy.kindle import start_login
from paperboy.substack import AuthExpired

FULL_BODY = "<p>" + "Full paid text. " * 50 + "</p>"
PUBLICATION = Publication(HOST, backfill=3)
CENTER = (232, 640, 1032, 1040)  # below the one-line title
TOP_LEFT = (96, 600, 400, 900)
BOTTOM_RIGHT = (864, 1000, 1168, 1300)


def _paid(number: int) -> dict:
    return numbered_post(number, body_html=FULL_BODY, wordcount=150)


def _cover(path) -> Image.Image:
    return Image.open(io.BytesIO(read_epub_bytes(path)["EPUB/cover.jpg"]))


def _dark(image: Image.Image, box) -> int:
    return sum(image.crop(box).histogram()[:128])


def _gray(image: Image.Image, box) -> int:
    """Light gray pixels, as left by the watermark on white."""
    return sum(image.crop(box).histogram()[150:215])


@pytest.fixture
def free_signup(substack):
    """Logged in, but only a free subscriber of the publication."""
    substack.membership = "free_signup"


@pytest.mark.usefixtures("free_signup")
def test_free_subscriber_gets_the_teaser_marked_as_such(pipeline, substack):
    path = pipeline.convert(substack.add_paid(_paid(1)))

    epub = read_epub(path)
    assert path.name == "2026-03-01 Post 1 (Teaser).epub"
    assert "<dc:title>Post 1 (Teaser)</dc:title>" in epub["EPUB/content.opf"]
    assert "Teaser paragraph for subscribers." in epub["EPUB/content.xhtml"]
    assert "Full paid text." not in epub["EPUB/content.xhtml"]
    assert_epubcheck_clean(path)


@pytest.mark.usefixtures("free_signup")
def test_long_title_keeps_the_mark_in_the_file_name(pipeline, substack):
    path = pipeline.convert(substack.add_paid({**_paid(1), "title": "A really long title " * 10}))

    assert path.name.endswith(" (Teaser).epub")


@pytest.mark.usefixtures("free_signup")
def test_teaser_ends_with_a_link_to_the_full_post(pipeline, substack):
    url = substack.add_paid(_paid(1))

    content = read_epub(pipeline.convert(url))["EPUB/content.xhtml"]

    ending = content[content.index("Teaser paragraph"):]
    assert "only the free part of a paid post" in ending
    assert f'href="{url}"' in ending


@pytest.mark.usefixtures("free_signup")
def test_teaser_cover_shows_a_gray_diagonal_watermark(pipeline, substack):
    teaser = _cover(pipeline.convert(substack.add_paid(_paid(1))))
    regular = _cover(pipeline.convert(substack.add(numbered_post(2))))

    assert _gray(teaser, CENTER) > 20000, "watermark across the middle"
    assert _dark(teaser, CENTER) == 0, "light gray, not black"
    assert _gray(regular, CENTER) == 0, "regular covers have no watermark"
    for corner in (TOP_LEFT, BOTTOM_RIGHT):
        assert _gray(teaser, corner) == 0, "runs bottom left to top right"


def test_paid_subscriber_never_gets_a_teaser_title(pipeline, substack):
    path = pipeline.convert(substack.add_paid(_paid(1)))

    assert path.name == "2026-03-01 Post 1.epub"
    assert "Teaser" not in read_epub(path)["EPUB/content.opf"]


def test_paid_subscriber_with_a_teaser_still_waits(pipeline, substack):
    """Subscribed but the body is short anyway: never deliver it truncated."""
    url = substack.add_paid(_paid(1))
    substack.full_posts[1001]["body_html"] = "<p>Too short.</p>"

    with pytest.raises(AuthExpired):
        pipeline.convert(url)


@pytest.mark.usefixtures("free_signup")
@pytest.mark.parametrize("cookie", [None, "s%3Aexpired.signature"])
def test_without_a_valid_login_teasers_still_wait(pipeline, substack, secrets, notifier, output_dir, cookie):
    secrets.values.clear()
    if cookie:
        secrets.values["substack.sid"] = cookie
    substack.add_paid(_paid(1))
    substack.feeds[HOST] = ["post-1"]

    report = pipeline.run([PUBLICATION])

    assert report.pending_auth == 1 and report.converted == []
    assert len(notifier.messages) == 1
    assert not list(output_dir.rglob("*.epub"))


@pytest.mark.usefixtures("free_signup")
def test_run_delivers_teasers_as_done_without_notification(pipeline, substack, notifier):
    substack.add_paid(_paid(2))
    substack.add(numbered_post(1))
    substack.feeds[HOST] = ["post-2", "post-1"]

    report = pipeline.run([PUBLICATION])
    again = pipeline.run([PUBLICATION])

    assert sorted(p.name for p in report.converted) == ["2026-03-01 Post 1.epub", "2026-03-02 Post 2 (Teaser).epub"]
    assert report.pending_auth == 0 and notifier.messages == []
    assert again.converted == [], "teasers are final"
    assert pipeline.status([PUBLICATION]).publications[0].pending_auth == []


@pytest.mark.usefixtures("free_signup")
def test_waiting_post_that_gets_through_rearms_the_expiry_notification(pipeline, substack, secrets, notifier):
    """A pending post that converts proves the login works, so the next expiry is told again."""
    secrets.values["substack.sid"] = "s%3Aexpired.signature"
    substack.add_paid(_paid(1))
    substack.feeds[HOST] = ["post-1"]
    pipeline.run([PUBLICATION])
    secrets.values["substack.sid"] = substack.valid_cookie  # the login works again, without set-cookie

    report = pipeline.run([PUBLICATION])
    secrets.values["substack.sid"] = "s%3Aexpired-again.signature"
    substack.add_paid(_paid(2))
    substack.feeds[HOST] = ["post-2", "post-1"]
    pipeline.run([PUBLICATION])

    assert [p.name for p in report.converted] == ["2026-03-01 Post 1 (Teaser).epub"]
    assert len(notifier.messages) == 2


@pytest.mark.usefixtures("free_signup")
def test_kindle_gets_the_teaser_title(pipeline, substack, kindle):
    login = start_login()
    pipeline.kindle_login(login, kindle.authorize(login.url))
    substack.add_paid(_paid(1))
    substack.feeds[HOST] = ["post-1"]

    pipeline.run([PUBLICATION], kindle=True)

    assert [s["title"] for s in kindle.sent] == ["Post 1 (Teaser)"]
