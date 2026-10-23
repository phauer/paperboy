import io
import re

from PIL import Image

from conftest import IMAGE_URL, assert_epubcheck_clean, make_image, make_post_json, read_epub, read_epub_bytes


def _embedded_images(files: dict[str, bytes]) -> dict[str, Image.Image]:
    return {name: Image.open(io.BytesIO(data)) for name, data in files.items() if name.startswith("EPUB/images/")}


def _body_with_images(*srcs: str) -> str:
    figures = "".join(
        f'<div class="captioned-image-container"><figure><a class="image-link" href="{src}">'
        f'<div class="image2-inset"><picture><source srcset="{src} 424w"/><img src="{src}" alt="Chart {i}"/></picture>'
        f'<div class="image-link-expand"><button type="button"><svg viewBox="0 0 1 1"></svg></button></div></div></a>'
        f'<figcaption class="image-caption">Caption {i}</figcaption></figure></div>'
        for i, src in enumerate(srcs, start=1)
    )
    return f"<p>Intro.</p>{figures}<p>Outro.</p>"


def test_image_is_embedded_and_referenced_locally(pipeline, substack):
    substack.files[IMAGE_URL] = make_image()
    url = substack.add(make_post_json())

    path = pipeline.convert(url)

    files = read_epub_bytes(path)
    assert list(_embedded_images(files)) == ["EPUB/images/001.jpg"]
    content = files["EPUB/content.xhtml"].decode()
    assert re.search(r'<img [^>]*src="images/001.jpg"', content)
    assert 'alt="A chart"' in content
    assert "Source: Max Mustermann" in content
    assert not re.search(r'src="https?://', content), "no remote resources"
    assert "<picture" not in content and "<button" not in content
    assert_epubcheck_clean(path)


def test_image_is_grayscale_jpeg_fitted_to_screen(pipeline, substack):
    substack.files[IMAGE_URL] = make_image(size=(3000, 1000))
    url = substack.add(make_post_json())

    image = _embedded_images(read_epub_bytes(pipeline.convert(url)))["EPUB/images/001.jpg"]

    assert image.format == "JPEG"
    assert image.mode == "L"
    assert image.size == (1264, 421), "aspect ratio kept, width fits the screen"


def test_tall_image_fits_screen_height(pipeline, substack):
    substack.files[IMAGE_URL] = make_image(size=(1000, 4000))
    url = substack.add(make_post_json())

    image = _embedded_images(read_epub_bytes(pipeline.convert(url)))["EPUB/images/001.jpg"]

    assert image.size == (420, 1680)


def test_small_image_is_not_upscaled(pipeline, substack):
    substack.files[IMAGE_URL] = make_image(size=(300, 200))
    url = substack.add(make_post_json())

    image = _embedded_images(read_epub_bytes(pipeline.convert(url)))["EPUB/images/001.jpg"]

    assert image.size == (300, 200)


def test_contrast_is_boosted(pipeline, substack):
    gray = Image.new("L", (200, 100), 100)
    gray.paste(150, (100, 0, 200, 100))
    buffer = io.BytesIO()
    gray.save(buffer, format="PNG")
    substack.files[IMAGE_URL] = buffer.getvalue()
    url = substack.add(make_post_json())

    image = _embedded_images(read_epub_bytes(pipeline.convert(url)))["EPUB/images/001.jpg"]

    dark, light = image.getpixel((50, 50)), image.getpixel((150, 50))
    assert light - dark > 55, f"expected a wider spread than the original 50, got {dark}..{light}"


def test_transparent_image_gets_white_background(pipeline, substack):
    substack.files[IMAGE_URL] = make_image(mode="RGBA", color=(0, 0, 0, 0), size=(100, 100))
    url = substack.add(make_post_json())

    image = _embedded_images(read_epub_bytes(pipeline.convert(url)))["EPUB/images/001.jpg"]

    assert image.getpixel((50, 50)) > 245


def test_webp_and_gif_are_converted(pipeline, substack):
    webp, gif = "https://cdn.example.com/a.webp", "https://cdn.example.com/b.gif"
    substack.files[webp] = make_image(format="WEBP", size=(400, 300))
    substack.files[gif] = make_image(mode="P", format="GIF", size=(400, 300), color=3)
    url = substack.add(make_post_json(body_html=_body_with_images(webp, gif)))

    path = pipeline.convert(url)

    images = _embedded_images(read_epub_bytes(path))
    assert sorted(images) == ["EPUB/images/001.jpg", "EPUB/images/002.jpg"]
    assert all(image.format == "JPEG" for image in images.values())
    assert_epubcheck_clean(path)


def test_broken_image_is_dropped_but_caption_and_post_kept(pipeline, substack, output_dir):
    good, missing, garbage = (f"https://cdn.example.com/{n}.png" for n in ("good", "missing", "garbage"))
    substack.files[good] = make_image(size=(400, 300))
    substack.files[garbage] = b"<html>not an image</html>"
    url = substack.add(make_post_json(body_html=_body_with_images(good, missing, garbage)))

    path = pipeline.convert(url)

    files = read_epub_bytes(path)
    assert list(_embedded_images(files)) == ["EPUB/images/001.jpg"]
    content = files["EPUB/content.xhtml"].decode()
    assert content.count("<img") == 1
    for caption in ("Caption 1", "Caption 2", "Caption 3"):
        assert caption in content
    assert "Intro." in content and "Outro." in content
    assert_epubcheck_clean(path)


def test_same_image_twice_is_embedded_and_downloaded_once(pipeline, substack):
    substack.files[IMAGE_URL] = make_image(size=(400, 300))
    url = substack.add(make_post_json(body_html=_body_with_images(IMAGE_URL, IMAGE_URL)))

    path = pipeline.convert(url)

    files = read_epub_bytes(path)
    assert list(_embedded_images(files)) == ["EPUB/images/001.jpg"]
    assert files["EPUB/content.xhtml"].decode().count('src="images/001.jpg"') == 2
    assert substack.requests.count(IMAGE_URL) == 1


def test_image_inside_text_link_keeps_the_link_text(pipeline, substack):
    substack.files[IMAGE_URL] = make_image(size=(100, 100))
    body = f'<p><a href="https://example.org/x">See this <img src="{IMAGE_URL}"/> chart</a></p>'
    url = substack.add(make_post_json(body_html=body))

    path = pipeline.convert(url)

    content = read_epub(path)["EPUB/content.xhtml"]
    assert re.search(r'<a href="https://example.org/x">See this <img [^>]*src="images/001.jpg"[^>]*/> chart</a>', content)
    assert_epubcheck_clean(path)
