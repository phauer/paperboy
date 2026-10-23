"""The pipeline: the single entry point the CLI and the tests go through."""

from dataclasses import dataclass
from pathlib import Path

import httpx

from paperboy.converter import build_epub
from paperboy.delivery import deliver
from paperboy.substack import fetch_post


@dataclass(frozen=True)
class Deps:
    """All I/O the pipeline touches, injected so tests can replace it."""

    http: httpx.Client
    output_dir: Path


class Pipeline:
    def __init__(self, deps: Deps):
        self._deps = deps

    def convert(self, url: str) -> Path:
        """Convert a single post URL into an EPUB in the output folder."""
        post = fetch_post(self._deps.http, url)
        return deliver(self._deps.output_dir, post, build_epub(post))
