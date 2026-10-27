import io

import pytest
from PIL import Image

from conftest import HOST, assert_epubcheck_clean, make_image, make_post_json, read_epub_bytes
from paperboy.config import Publication

S3_LOGO = "https://bucket.s3.amazonaws.com/public/images/logo_300x300.png"
PROXIED_LOGO = "https://substackcdn.com/image/fetch/w_300/https%3A%2F%2Fbucket.s3.amazonaws.com%2Fpublic%2Fimages%2Flogo_300x300.png"
CDN_LOGO = "https://substackcdn.com/image/fetch/$s_!abc!,f_auto/https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Flogo.png"
LOGO_BOX = (868, 1110, 1168, 1410)
LEFT_OF_LOGO = (96, 1110, 860, 1410)


def _with_logo(logo_url: str | None, **overrides) -> dict:
    publication = {"id": 7, "name": "The Example Letter"}
    if logo_url:
        publication["logo_url"] = logo_url
    byline = {"id": 1, "name": "Erika Mustermann", "publicationUsers": [{"publication": publication}]}
    return make_post_json(publishedBylines=[byline], **overrides)


def _cover(path) -> Image.Image:
    return Image.open(io.BytesIO(read_epub_bytes(path)["EPUB/cover.jpg"]))


def _dark(image: Image.Image, box) -> int:
    return sum(image.crop(box).histogram()[:128])


def test_cover_shows_the_logo_fetched_through_substackcdn(pipeline, substack):
    substack.files[PROXIED_LOGO] = make_image(size=(300, 300), color=(200, 30, 30))

    path = pipeline.convert(substack.add(_with_logo(S3_LOGO)))

    assert _dark(_cover(path), LOGO_BOX) > 0.9 * 300 * 300
    assert S3_LOGO not in substack.requests, "raw S3 links can be denied"
    assert_epubcheck_clean(path)


def test_logo_already_on_substackcdn_is_fetched_as_is(pipeline, substack):
    substack.files[CDN_LOGO] = make_image(size=(300, 300), color=(0, 0, 0))

    path = pipeline.convert(substack.add(_with_logo(CDN_LOGO)))

    assert _dark(_cover(path), LOGO_BOX) > 0.9 * 300 * 300


def test_without_a_logo_the_cover_stays_as_before(pipeline, substack):
    path = pipeline.convert(substack.add(_with_logo(None)))

    assert _dark(_cover(path), LOGO_BOX) == 0
    assert not [url for url in substack.requests if "logo" in url]


@pytest.mark.parametrize("served", [None, b"<Error>AccessDenied</Error>"])
def test_broken_logo_still_delivers_the_post(pipeline, substack, served):
    if served:
        substack.files[PROXIED_LOGO] = served
    substack.publish(_with_logo(S3_LOGO))

    report = pipeline.run([Publication(HOST, backfill=3)])

    assert len(report.converted) == 1 and report.errors == []
    assert _dark(_cover(report.converted[0]), LOGO_BOX) == 0


def test_transparent_logo_is_flattened_onto_white(pipeline, substack):
    substack.files[PROXIED_LOGO] = make_image(size=(300, 300), format="PNG", mode="RGBA", color=(0, 0, 0, 0))

    path = pipeline.convert(substack.add(_with_logo(S3_LOGO)))

    assert _dark(_cover(path), LOGO_BOX) == 0


def test_non_square_logo_fits_into_the_box(pipeline, substack):
    substack.files[PROXIED_LOGO] = make_image(size=(900, 300), color=(0, 0, 0))

    image = _cover(pipeline.convert(substack.add(_with_logo(S3_LOGO))))

    assert _dark(image, (868, 1310, 1168, 1410)) > 0.9 * 300 * 100, "bottom-aligned, full width"
    assert _dark(image, (868, 1110, 1168, 1300)) == 0, "no stretching"


def test_long_title_stays_clear_of_the_logo(pipeline, substack):
    substack.files[PROXIED_LOGO] = make_image(size=(300, 300))

    image = _cover(pipeline.convert(substack.add(_with_logo(S3_LOGO, title="Why " + "really " * 80 + "long"))))

    assert _dark(image, LEFT_OF_LOGO) == 0


def test_logo_leaves_the_badge_corners_free(pipeline, substack):
    substack.files[PROXIED_LOGO] = make_image(size=(300, 300), color=(0, 0, 0))

    image = _cover(pipeline.convert(substack.add(_with_logo(S3_LOGO))))

    assert _dark(image, (0, 1450, 470, 1680)) == 0, "EPUB badge at the bottom left"
    assert _dark(image, (800, 1450, 1264, 1680)) > 1000, "author and date still there"
