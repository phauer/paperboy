"""Embeds post images, optimized for a monochrome e-ink screen (Boox Go 7)."""

import io
import logging
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin

from lxml import html
from PIL import Image, ImageEnhance

log = logging.getLogger(__name__)

# Boox Go 7 screen resolution (portrait).
MAX_SIZE = (1264, 1680)
JPEG_QUALITY = 80
CONTRAST = 1.2

FetchImage = Callable[[str], bytes | None]


@dataclass(frozen=True)
class EmbeddedImage:
    file_name: str
    data: bytes


def embed_images(root: html.HtmlElement, base_url: str, fetch: FetchImage) -> list[EmbeddedImage]:
    """Replace remote images in `root` with local ones and return the files to embed.

    Images that can't be fetched or decoded are left untouched for the
    sanitizer to remove, so a broken image never costs the whole post.
    """
    by_url: dict[str, EmbeddedImage | None] = {}
    for img in list(root.iter("img")):
        src = img.get("src")
        if not src or src.startswith("data:"):
            continue
        url = urljoin(base_url, src)
        if url not in by_url:
            by_url[url] = _download(url, fetch, file_name=f"images/{len(by_url) + 1:03d}.jpg")
        if image := by_url[url]:
            _replace_with_local(img, image)
    return [image for image in by_url.values() if image]


def _download(url: str, fetch: FetchImage, file_name: str) -> EmbeddedImage | None:
    data = fetch(url)
    processed = process_image(data) if data else None
    if processed is None:
        log.warning("Dropping image that could not be fetched or decoded: %s", url)
        return None
    return EmbeddedImage(file_name, processed)


def process_image(data: bytes) -> bytes | None:
    """Grayscale, slight contrast boost, fit to the screen, JPEG. None if undecodable."""
    try:
        with Image.open(io.BytesIO(data)) as original:
            image = _on_white(original).convert("L")
            image = ImageEnhance.Contrast(image).enhance(CONTRAST)
            image.thumbnail(MAX_SIZE, Image.Resampling.LANCZOS)
            out = io.BytesIO()
            image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return out.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def _on_white(image: Image.Image) -> Image.Image:
    """Flatten transparency onto white, so transparent areas don't turn black."""
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        return background
    return image


def _replace_with_local(img: html.HtmlElement, image: EmbeddedImage) -> None:
    # Substack wraps images in <picture> and a link to the full-size image plus
    # expand/restack buttons; replace that whole wrapper with the plain image.
    target = img.getparent() if img.getparent() is not None and img.getparent().tag == "picture" else img
    link = next(target.iterancestors("a"), None)
    if link is not None and not link.text_content().strip():
        target = link
    local = html.Element("img", src=image.file_name, alt=img.get("alt") or "")
    local.tail = target.tail
    target.getparent().replace(target, local)
