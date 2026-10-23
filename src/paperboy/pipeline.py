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
from paperboy.macos import Notifier, SecretStore
from paperboy.state import FINAL_STATUSES, StateStore
from paperboy.substack import COOKIE_NAME, AuthExpired, SourceError, fetch_post, parse_post_url

log = logging.getLogger(__name__)

# Set while the user has been told about an expired cookie, so they're told only once.
AUTH_EXPIRY_NOTIFIED = "auth_expiry_notified"


@dataclass(frozen=True)
class Deps:
    """All I/O the pipeline touches, injected so tests can replace it."""

    http: httpx.Client
    output_dir: Path
    state: StateStore
    secrets: SecretStore
    notifier: Notifier
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)


@dataclass
class RunReport:
    converted: list[Path] = field(default_factory=list)
    skipped: int = 0
    pending_auth: int = 0
    errors: list[str] = field(default_factory=list)


class Pipeline:
    def __init__(self, deps: Deps):
        self._deps = deps

    def convert(self, url: str) -> Path:
        """Convert a single post URL into an EPUB in the output folder. Raises AuthExpired."""
        post = fetch_post(self._deps.http, url, self._deps.secrets.get(COOKIE_NAME))
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
        urls = discover_post_urls(self._deps.http, host)
        # Pending posts may have dropped out of the feed while the cookie was expired.
        urls += [url for url in state.post_urls(host, "pending_auth") if url not in urls]
        for position, url in enumerate(urls):
            _, slug = parse_post_url(url)
            if state.post_status(host, slug) in FINAL_STATUSES:
                continue
            if first_sight and position >= publication.backfill:
                state.record_post(host, slug, url=url, status="skipped", reason="backfill", at=self._deps.clock())
                report.skipped += 1
                continue
            try:
                path = self.convert(url)
            except AuthExpired as e:
                state.record_post(host, slug, url=url, status="pending_auth", reason=str(e), at=self._deps.clock())
                report.pending_auth += 1
                self._notify_auth_expired(str(e))
                continue
            except SourceError as e:
                # No state written, so the post is retried on the next run.
                log.warning("Will retry %s: %s", url, e)
                report.errors.append(str(e))
                continue
            state.record_post(host, slug, url=url, status="done", file=path, at=self._deps.clock())
            report.converted.append(path)
        state.add_publication(host, self._deps.clock())

    def cookie_updated(self) -> None:
        """A new cookie was stored: the next expiry deserves a new notification."""
        self._deps.state.set_flag(AUTH_EXPIRY_NOTIFIED, False)

    def _notify_auth_expired(self, reason: str) -> None:
        log.warning("Holding paid post until a valid cookie is set: %s", reason)
        if self._deps.state.has_flag(AUTH_EXPIRY_NOTIFIED):
            return
        self._deps.notifier.notify(
            "paperboy: Substack login needed",
            "Paid posts are waiting. Copy a fresh substack.sid cookie and run: paperboy set-cookie",
        )
        self._deps.state.set_flag(AUTH_EXPIRY_NOTIFIED, True)

    def _fetch_image(self, url: str) -> bytes | None:
        try:
            response = self._deps.http.get(url)
        except httpx.HTTPError as e:
            log.warning("Image request failed: %s (%s)", url, e)
            return None
        return response.content if response.is_success else None
