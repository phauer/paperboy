"""Writes EPUBs into the synced output folder."""

import re
from pathlib import Path

from paperboy.post import Post

_MAX_NAME_LENGTH = 100
_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def deliver(output_dir: Path, post: Post, epub_bytes: bytes) -> Path:
    folder = output_dir / safe_name(post.publication)
    path = folder / f"{post.published:%Y-%m-%d} {safe_name(post.title)}.epub"
    folder.mkdir(parents=True, exist_ok=True)
    # Syncthing ignores files named .syncthing.*.tmp, so it never syncs a half-written EPUB.
    temp = folder / f".syncthing.{path.name}.tmp"
    temp.write_bytes(epub_bytes)
    temp.replace(path)
    return path


def safe_name(name: str) -> str:
    cleaned = " ".join(_UNSAFE_CHARS.sub(" ", name).split()).strip(". ")
    if len(cleaned) > _MAX_NAME_LENGTH:
        cut = cleaned[:_MAX_NAME_LENGTH]
        cleaned = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" .,;-")
    return cleaned or "Untitled"
