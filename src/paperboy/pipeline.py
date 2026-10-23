"""The pipeline: the single entry point the CLI and the tests go through."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from paperboy.config import Publication
from paperboy.converter import build_epub
from paperboy.delivery import deliver
from paperboy.feed import discover_post_urls
from paperboy.state import StateStore
from paperboy.substack import SourceError, fetch_post, parse_post_url

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Deps:
    """All I/O the pipeline touches, injected so tests can replace it."""

    http: httpx.Client
    output_dir: Path
    state: StateStore
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)


@dataclass
class RunReport:
    converted: list[Path] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, deps: Deps):
        self._deps = deps

    def convert(self, url: str) -> Path:
        """Convert a single post URL into an EPUB in the output folder."""
        post = fetch_post(self._deps.http, url)
        return deliver(self._deps.output_dir, post, build_epub(post, self._fetch_image))

    def run(self, publications: list[Publication]) -> RunReport:
        """Convert all new posts of the given publications. Raises AlreadyRunning."""
        report = RunReport()
        with self._deps.state.lock():
            for publication in publications:
                try:
                    self._run_publication(publication, report)
                except SourceError as e:
                    # Not marked as known, so a first-time publication gets its backfill next run.
                    log.warning("Skipping %s this run: %s", publication.host, e)
                    report.errors.append(str(e))
        return report

    def _run_publication(self, publication: Publication, report: RunReport) -> None:
        state, host = self._deps.state, publication.host
        first_sight = not state.knows_publication(host)
        for position, url in enumerate(discover_post_urls(self._deps.http, host)):
            _, slug = parse_post_url(url)
            if state.post_status(host, slug):
                continue
            if first_sight and position >= publication.backfill:
                state.record_post(host, slug, url=url, status="skipped", reason="backfill", at=self._deps.clock())
                report.skipped += 1
                continue
            try:
                path = self.convert(url)
            except SourceError as e:
                # No state written, so the post is retried on the next run.
                log.warning("Will retry %s: %s", url, e)
                report.errors.append(str(e))
                continue
            state.record_post(host, slug, url=url, status="done", file=path, at=self._deps.clock())
            report.converted.append(path)
        state.add_publication(host, self._deps.clock())

    def _fetch_image(self, url: str) -> bytes | None:
        try:
            response = self._deps.http.get(url)
        except httpx.HTTPError as e:
            log.warning("Image request failed: %s (%s)", url, e)
            return None
        return response.content if response.is_success else None
