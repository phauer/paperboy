"""Writes EPUBs into the output folder, one subfolder per publication."""

import re
from pathlib import Path

from paperboy.post import TEASER_MARK, Post

_MAX_NAME_LENGTH = 100
_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def deliver(output_dir: Path, post: Post, epub_bytes: bytes) -> Path:
    folder = output_dir / safe_name(post.publication)
    # The mark goes after shortening, so a long title can't cut it off.
    mark = TEASER_MARK if post.teaser else ""
    path = folder / f"{post.published:%Y-%m-%d} {safe_name(post.title)}{mark}.epub"
    folder.mkdir(parents=True, exist_ok=True)
    # A dot file without the .epub suffix, so a half-written EPUB is never pushed or listed.
    temp = folder / f".{path.name}.tmp"
    temp.write_bytes(epub_bytes)
    temp.replace(path)
    return path


def safe_name(name: str) -> str:
    cleaned = " ".join(_UNSAFE_CHARS.sub(" ", name).split()).strip(". ")
    if len(cleaned) > _MAX_NAME_LENGTH:
        cut = cleaned[:_MAX_NAME_LENGTH]
        cleaned = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" .,;-")
    return cleaned or "Untitled"
