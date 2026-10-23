"""The pipeline: the single entry point the CLI and the tests go through."""

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from paperboy.converter import build_epub
from paperboy.delivery import deliver
from paperboy.substack import fetch_post

log = logging.getLogger(__name__)


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
        return deliver(self._deps.output_dir, post, build_epub(post, self._fetch_image))

    def _fetch_image(self, url: str) -> bytes | None:
        try:
            response = self._deps.http.get(url)
        except httpx.HTTPError as e:
            log.warning("Image request failed: %s (%s)", url, e)
            return None
        return response.content if response.is_success else None
