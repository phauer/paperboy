"""Renders a typographic cover that stays readable as a library thumbnail."""

import io
import logging
import math
import unicodedata
from datetime import datetime
from functools import cache

from PIL import Image, ImageDraw, ImageFont

from paperboy.images import on_white

log = logging.getLogger(__name__)

SIZE = (1264, 1680)  # Boox Go 7 screen, portrait
_MARGIN = 96
_TEXT_WIDTH = SIZE[0] - 2 * _MARGIN
# The Kindle puts badges on the thumbnail's top right (progress, "read" flag) and bottom left (a check
# symbol), so the header is left-aligned, the footer right-aligned, and both keep this much of their
# opposite side free. NeoReader's badges on the Boox (left side) may cover the header.
_BADGE_CLEARANCE = 380
_SIDE_WIDTH = _TEXT_WIDTH - _BADGE_CLEARANCE
_TITLE_SIZES = range(136, 63, -8)  # largest first, shrink until the title fits
_TITLE_TOP = 420
_WATERMARK_SIZE = 360  # largest first, shrink until it fits the diagonal
_WATERMARK_OPACITY = 0.3  # light gray on white, still visible on 16-level e-ink
_LOGO_SIZE = 300
_LOGO_TOP = 1110  # the box ends about 50 px above the author line
_TITLE_BOTTOM = SIZE[1] - 360
_FONT_FILE = "/System/Library/Fonts/HelveticaNeue.ttc"
_REGULAR, _BOLD = 0, 1
_MISSING = "\U000f0000"  # a private-use character: no font has it, so it renders as the "missing glyph" box


def render_cover(
    publication: str, title: str, author: str, published: datetime, teaser: bool = False, logo: bytes | None = None
) -> bytes:
    image = Image.new("L", SIZE, 255)
    draw = ImageDraw.Draw(image)
    logo_image = _decode_logo(logo) if logo else None
    # Characters the font lacks (emoji, arrows, CJK) would show as empty boxes; an undrawable
    # title stays as it is, so the cover always has one.
    publication = _drawable(publication, _REGULAR)
    author = _drawable(author, _REGULAR)
    title = _drawable(title, _BOLD) or title

    y = _MARGIN
    for line in _wrap(publication, _font(_REGULAR, 60), max_lines=2, width=_SIDE_WIDTH) if publication else []:
        draw.text((_MARGIN, y), line, font=_font(_REGULAR, 60), fill=0)
        y += 76
    draw.rectangle((_MARGIN, y + 24, SIZE[0] - _MARGIN, y + 36), fill=0)

    # The logo takes the lower part of the title area, so the title ends above it.
    title_font, lines = _fit_title(title, bottom=_LOGO_TOP - 10 if logo_image else _TITLE_BOTTOM)
    line_height = int(title_font.size * 1.15)
    for number, line in enumerate(lines):
        draw.text((_MARGIN, _TITLE_TOP + number * line_height), line, font=title_font, fill=0)

    if logo_image:
        # Right-aligned and bottom-aligned in its box, so it sits right above the footer.
        x = SIZE[0] - _MARGIN - logo_image.width
        image.paste(logo_image, (x, _LOGO_TOP + _LOGO_SIZE - logo_image.height))

    footer = [published.strftime("%-d %B %Y")]
    if author:
        footer.insert(0, _wrap(author, _font(_REGULAR, 52), max_lines=1, width=_SIDE_WIDTH)[0])
    for number, line in enumerate(reversed(footer)):
        _draw_right(draw, SIZE[1] - _MARGIN - 52 - number * 70, line, _font(_REGULAR, 52))

    if teaser:
        image = _watermark(image, "TEASER")

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue()


def _watermark(image: Image.Image, text: str) -> Image.Image:
    """Translucent black text along the diagonal (bottom left to top right): gray on white, black stays black."""
    diagonal = math.hypot(*SIZE)
    size = _WATERMARK_SIZE
    while _font(_BOLD, size).getlength(text) > diagonal * 0.85:
        size -= 10
    strip = Image.new("L", (int(diagonal), size * 2), 0)
    ImageDraw.Draw(strip).text((strip.width // 2, size), text, font=_font(_BOLD, size), fill=255, anchor="mm")
    strip = strip.rotate(math.degrees(math.atan2(*reversed(SIZE))), expand=True, resample=Image.Resampling.BICUBIC)
    left, top = (strip.width - SIZE[0]) // 2, (strip.height - SIZE[1]) // 2
    mask = strip.crop((left, top, left + SIZE[0], top + SIZE[1])).point(lambda v: int(v * _WATERMARK_OPACITY))
    return Image.composite(Image.new("L", SIZE, 0), image, mask)


def _drawable(text: str, face: int) -> str:
    """The text without characters the font has no glyph for, with spaces tidied up."""
    font = _font(face, 60)
    kept = "".join(c for c in text if c.isspace() or (not _invisible(c) and _has_glyph(font, c)))
    return " ".join(kept.split())


def _invisible(char: str) -> bool:
    """Joiners and variation selectors: they only steer emoji, which are dropped anyway."""
    return unicodedata.category(char) == "Cf" or "︀" <= char <= "️" or "\U000e0100" <= char <= "\U000e01ef"


@cache
def _has_glyph(font: ImageFont.FreeTypeFont, char: str) -> bool:
    return _mask(font, char) != _mask(font, _MISSING)


def _mask(font: ImageFont.FreeTypeFont, char: str) -> tuple:
    mask = font.getmask(char)
    return mask.size, bytes(mask)


def _draw_right(draw: ImageDraw.ImageDraw, y: int, text: str, font: ImageFont.FreeTypeFont) -> None:
    draw.text((SIZE[0] - _MARGIN, y), text, font=font, fill=0, anchor="ra")


def _decode_logo(data: bytes) -> Image.Image | None:
    """The logo in grayscale, fitted into the logo box; None if it can't be decoded."""
    try:
        with Image.open(io.BytesIO(data)) as original:
            logo = on_white(original).convert("L")
            logo.thumbnail((_LOGO_SIZE, _LOGO_SIZE), Image.Resampling.LANCZOS)
            return logo
    except (OSError, ValueError, Image.DecompressionBombError):
        log.warning("Cover without logo: the image could not be decoded")
        return None


def _fit_title(title: str, bottom: int) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    for size in _TITLE_SIZES:
        font = _font(_BOLD, size)
        max_lines = (bottom - _TITLE_TOP) // int(size * 1.15)
        lines = _wrap(title, font, max_lines=None)
        if len(lines) <= max_lines:
            return font, lines
    return font, _wrap(title, font, max_lines=max_lines)


def _wrap(
    text: str, font: ImageFont.FreeTypeFont, max_lines: int | None, width: int = _TEXT_WIDTH
) -> list[str]:
    """Greedy word wrap; overlong words are split, and overflow ends in an ellipsis."""
    lines: list[str] = []
    line = ""
    for word in " ".join(text.split()).split(" ") or [""]:
        candidate = f"{line} {word}".strip()
        if font.getlength(candidate) <= width:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = word
        while font.getlength(line) > width:
            cut = _longest_fitting_prefix(line, font, width)
            lines.append(line[:cut])
            line = line[cut:]
    lines.append(line)
    if max_lines is not None and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _with_ellipsis(lines[-1], font, width)
    return lines


def _longest_fitting_prefix(text: str, font: ImageFont.FreeTypeFont, width: int) -> int:
    cut = len(text)
    while cut > 1 and font.getlength(text[:cut]) > width:
        cut -= 1
    return cut


def _with_ellipsis(line: str, font: ImageFont.FreeTypeFont, width: int) -> str:
    while line and font.getlength(f"{line}…") > width:
        line = line[:-1]
    return f"{line.rstrip(' ,.;:-')}…"


@cache
def _font(face: int, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(_FONT_FILE, size, index=face)
    except OSError:
        return ImageFont.load_default(size)  # not on macOS: Pillow's bundled font
