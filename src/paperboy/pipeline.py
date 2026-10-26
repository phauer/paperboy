"""The pipeline: the single entry point the CLI and the tests go through."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from paperboy.boox import STORAGE_ROOT, BooxDrop, BooxError
from paperboy.config import Boox, Publication
from paperboy.converter import build_epub, read_title_and_publication
from paperboy.delivery import deliver
from paperboy.feed import discover_post_urls
from paperboy.kindle import CREDENTIAL_NAME, Device, KindleAuthError, KindleError, Login, SendToKindle, finish_login
from paperboy.macos import Notifier, SecretStore
from paperboy.state import FINAL_STATUSES, LastRun, StateStore, StoredPost
from paperboy.substack import COOKIE_NAME, AuthExpired, SourceError, fetch_post, parse_post_url

log = logging.getLogger(__name__)

# Set while the user has been told about an expired login, so they're told only once.
AUTH_EXPIRY_NOTIFIED = "auth_expiry_notified"
KINDLE_LOGIN_NOTIFIED = "kindle_login_notified"


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
    push: "PushReport | None" = None  # to the Boox
    kindle: "PushReport | None" = None


@dataclass
class PushReport:
    target: str  # "Boox" or "Kindle"
    pushed: list[Path] = field(default_factory=list)
    waiting: int = 0  # files that stay on the Mac until the next push
    reachable: bool = True
    errors: list[str] = field(default_factory=list)


@dataclass
class KindleStatus:
    login_stored: bool
    waiting: int  # issues not sent yet


@dataclass
class PublicationStatus:
    host: str
    done: int = 0
    pending_auth: list[StoredPost] = field(default_factory=list)
    failed: list[StoredPost] = field(default_factory=list)
    skipped: list[StoredPost] = field(default_factory=list)  # for a reason worth reading, i.e. not backfill
    backfill: int = 0  # older posts skipped when the publication was first seen


@dataclass
class Status:
    last_run: LastRun | None
    cookie_stored: bool
    publications: list[PublicationStatus]
    kindle: KindleStatus | None = None  # None without a [kindle] section

    @property
    def all_good(self) -> bool:
        return (
            self.last_run is not None
            and not self.last_run.errors
            and not any(p.pending_auth or p.failed for p in self.publications)
            and (self.kindle is None or (self.kindle.login_stored and not self.kindle.waiting))
        )


class Pipeline:
    def __init__(self, deps: Deps):
        self._deps = deps

    def convert(self, url: str) -> Path:
        """Convert a single post URL into an EPUB in the output folder. Raises AuthExpired."""
        post = fetch_post(self._deps.http, url, self._deps.secrets.get(COOKIE_NAME))
        return deliver(self._deps.output_dir, post, build_epub(post, self._fetch_image))

    def run(self, publications: list[Publication], boox: Boox | None = None, kindle: bool = False) -> RunReport:
        """Convert all new posts of the given publications, then push them to the Boox and Kindle.

        Raises AlreadyRunning.
        """
        report = RunReport()
        with self._deps.state.lock():
            for publication in publications:
                try:
                    self._run_publication(publication, report)
                except SourceError as e:
                    # Not marked as known, so a first-time publication gets its backfill next run.
                    log.warning("Skipping %s this run: %s", publication.host, e)
                    report.errors.append(str(e))
            if boox:
                report.push = self._push(boox)
            if kindle:
                report.kindle = self._send_to_kindle()
            push_errors = [e for push in (report.push, report.kindle) if push for e in push.errors]
            self._deps.state.record_run(self._deps.clock(), report.errors + push_errors)
        return report

    def push(self, boox: Boox | None = None, kindle: bool = False) -> list[PushReport]:
        """Deliver EPUBs the Boox and Kindle haven't received yet. Raises AlreadyRunning (that run pushes at its end)."""
        with self._deps.state.lock():
            return ([self._push(boox)] if boox else []) + ([self._send_to_kindle()] if kindle else [])

    def kindle_login(self, login: Login, redirect_url: str) -> list[Device]:
        """Finish a Send to Kindle login, store it, and return the Kindles it can send to. Raises KindleError."""
        credential = finish_login(self._deps.http, login, redirect_url)
        devices = SendToKindle(self._deps.http, credential).devices()
        self._deps.secrets.set(CREDENTIAL_NAME, credential)
        self._deps.state.set_flag(KINDLE_LOGIN_NOTIFIED, False)
        return devices

    def status(self, publications: list[Publication], kindle: bool = False) -> Status:
        """What the pipeline has done so far: per publication, plus the last run and whether a cookie is stored."""
        by_host = {p.host: PublicationStatus(p.host) for p in publications}
        for post in self._deps.state.posts():
            publication = by_host.setdefault(post.publication, PublicationStatus(post.publication))
            if post.status == "done":
                publication.done += 1
            elif post.status == "skipped" and post.reason == "backfill":
                publication.backfill += 1
            elif post.status == "pending_auth":
                publication.pending_auth.append(post)
            elif post.status == "skipped":
                publication.skipped.append(post)
            else:
                publication.failed.append(post)
        return Status(
            last_run=self._deps.state.last_run(),
            cookie_stored=self._deps.secrets.get(COOKIE_NAME) is not None,
            publications=list(by_host.values()),
            kindle=KindleStatus(
                login_stored=self._deps.secrets.get(CREDENTIAL_NAME) is not None,
                waiting=len(self._pending("kindle")),
            ) if kindle else None,
        )

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

    def _pending(self, target: str) -> list[tuple[Path, str, int]]:
        """EPUBs the target hasn't received yet, as (path, state key, size)."""
        return [
            (path, key, size)
            for path in sorted(self._deps.output_dir.glob("*/*.epub"))
            for key in [path.relative_to(self._deps.output_dir).as_posix()]
            for size in [path.stat().st_size]
            # Pushed once means delivered: an issue deleted on the device stays deleted.
            if self._deps.state.pushed_size(target, key) != size
        ]

    def _push(self, boox: Boox) -> PushReport:
        report, state = PushReport("Boox"), self._deps.state
        pending = self._pending("boox")
        if not pending:
            return report
        device = BooxDrop(self._deps.http, boox.url)
        if not device.reachable():
            report.reachable, report.waiting = False, len(pending)
            return report
        for index, (path, key, size) in enumerate(pending):
            folder = f"{STORAGE_ROOT}/{boox.folder}/{path.parent.name}"
            try:
                device.ensure_dir(folder)
                device.upload(folder, path.name, path.read_bytes())
            except BooxError as e:
                log.warning("Will push %s again next time: %s", key, e)
                report.errors.append(str(e))
                continue
            except httpx.TransportError as e:
                log.info("Lost the Boox, %d files follow next time: %s", len(pending) - index, e)
                report.reachable, report.waiting = False, len(pending) - index
                break
            state.record_push("boox", key, size, self._deps.clock())
            report.pushed.append(path)
        return report

    def _send_to_kindle(self) -> PushReport:
        report, state = PushReport("Kindle"), self._deps.state
        pending = self._pending("kindle")
        if not pending:
            return report
        report.waiting = len(pending)  # everything not sent, rejected files included, waits for the next push
        credential = self._deps.secrets.get(CREDENTIAL_NAME)
        try:
            if credential is None:
                raise KindleAuthError("No Kindle login stored")
            kindle = SendToKindle(self._deps.http, credential)
            serials = [device.serial for device in kindle.devices()]
        except KindleAuthError as e:
            return self._kindle_login_needed(report, e)
        except KindleError as e:
            report.errors.append(str(e))
            return report
        except httpx.TransportError as e:
            log.info("Send to Kindle not reachable, %d files follow next time: %s", len(pending), e)
            report.reachable = False
            return report
        if not serials:
            report.errors.append("No Kindle on the Amazon account to send to")
            return report
        for path, key, size in pending:
            data = path.read_bytes()
            title, publication = read_title_and_publication(data)
            try:
                # The Kindle shows neither series nor folders: the publication as author groups issues when sorted.
                kindle.send(data, title=title or path.stem, author=publication or path.parent.name, serials=serials)
            except KindleAuthError as e:
                return self._kindle_login_needed(report, e)
            except KindleError as e:
                log.warning("Will send %s again next time: %s", key, e)
                report.errors.append(str(e))
                continue
            except httpx.TransportError as e:
                log.info("Lost Send to Kindle, the rest follows next time: %s", e)
                report.reachable = False
                break
            state.record_push("kindle", key, size, self._deps.clock())
            report.pushed.append(path)
            report.waiting -= 1
        return report

    def _kindle_login_needed(self, report: PushReport, error: KindleAuthError) -> PushReport:
        report.errors.append(f"{error}: run 'paperboy kindle-login'")
        if not self._deps.state.has_flag(KINDLE_LOGIN_NOTIFIED):
            self._deps.notifier.notify(
                "paperboy: Kindle login needed",
                "New issues wait for the Kindle. Run: paperboy kindle-login",
            )
            self._deps.state.set_flag(KINDLE_LOGIN_NOTIFIED, True)
        return report

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
