"""Renders a typographic cover that stays readable as a library thumbnail."""

import io
import logging
from datetime import datetime
from functools import cache

from PIL import Image, ImageDraw, ImageFont

from paperboy.images import on_white

log = logging.getLogger(__name__)

SIZE = (1264, 1680)  # Boox Go 7 screen, portrait
_MARGIN = 96
_TEXT_WIDTH = SIZE[0] - 2 * _MARGIN
# NeoReader puts badges on the left of the thumbnail ("NEW"/progress at the top, "EPUB" at the bottom),
# so the header and footer are right-aligned and keep this much of the left side free.
_BADGE_CLEARANCE = 380
_SIDE_WIDTH = _TEXT_WIDTH - _BADGE_CLEARANCE
_TITLE_SIZES = range(136, 63, -8)  # largest first, shrink until the title fits
_TITLE_TOP = 420
_TEASER_BAND = (300, 400)
_LOGO_SIZE = 300
_LOGO_TOP = 1110  # the box ends about 50 px above the author line
_TITLE_BOTTOM = SIZE[1] - 360
_FONT_FILE = "/System/Library/Fonts/HelveticaNeue.ttc"
_REGULAR, _BOLD = 0, 1


def render_cover(
    publication: str, title: str, author: str, published: datetime, teaser: bool = False, logo: bytes | None = None
) -> bytes:
    image = Image.new("L", SIZE, 255)
    draw = ImageDraw.Draw(image)
    logo_image = _decode_logo(logo) if logo else None

    y = _MARGIN
    for line in _wrap(publication, _font(_REGULAR, 60), max_lines=2, width=_SIDE_WIDTH):
        _draw_right(draw, y, line, _font(_REGULAR, 60))
        y += 76
    draw.rectangle((_MARGIN, y + 24, SIZE[0] - _MARGIN, y + 36), fill=0)

    if teaser:
        # Between the header rule (at most y=284) and the title, centered so the badges don't hide it.
        top, bottom = _TEASER_BAND
        draw.rectangle((_MARGIN, top, SIZE[0] - _MARGIN, bottom), fill=0)
        draw.text((SIZE[0] // 2, (top + bottom) // 2), "TEASER", font=_font(_BOLD, 72), fill=255, anchor="mm")

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

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue()


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
