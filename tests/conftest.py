"""Test seam: the pipeline with fakes at the I/O boundary.

Fixture posts are anonymized and made up, but mirror the structure of real
Substack API responses (bylines, captioned images, buttons, subscribe widgets).
"""

import io
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
from PIL import Image

from paperboy.cli import main
from paperboy.pipeline import Deps, Pipeline
from paperboy.state import StateStore

HOST = "letter.example.com"
IMAGE_URL = "https://substackcdn.com/image/fetch/x.png"
NOW = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
VALID_COOKIE = "s%3Avalid-session.signature"
TEASER_BODY = "<p>Teaser paragraph for subscribers.</p>"

FREE_BODY = """\
<p>Hello readers, this is <strong>Erika Mustermann</strong> with <a href="https://example.org/a">a link</a>.</p>
<div class="captioned-image-container"><figure><a class="image-link image2 is-viewable-img" target="_blank" href="https://substackcdn.com/image/fetch/x.png" data-component-name="Image2ToDOM"><div class="image2-inset"><picture><source type="image/webp" srcset="https://substackcdn.com/image/fetch/w_424/x.png 424w"/><img src="https://substackcdn.com/image/fetch/x.png" width="1456" height="1762" alt="A chart"/></picture><div class="image-link-expand"><div class="pencraft pc-display-flex pc-gap-8 pc-reset"><button tabindex="0" type="button" class="pencraft pc-reset pencraft icon-container restack-image"><svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20"><g><path d="M0 0h20v20H0z"></path></g></svg></button></div></div></div></a><figcaption class="image-caption">Source: Max Mustermann</figcaption></figure></div>
<h2>Section one</h2>
<p>Body text of section one.</p>
<p class="button-wrapper" data-attrs="{}" data-component-name="ButtonCreateButton"><a class="button primary" href="https://letter.example.com/subscribe"><span>Subscribe now</span></a></p>
<pre><code>print("hello")</code></pre>
"""


def make_post_json(**overrides) -> dict:
    post = {
        "id": 12345,
        "slug": "a-fine-post",
        "title": "A Fine Post",
        "subtitle": "About fine things",
        "post_date": "2026-03-14T09:30:00.000Z",
        "canonical_url": f"https://{HOST}/p/a-fine-post",
        "audience": "everyone",
        "type": "newsletter",
        "language": "en",
        "wordcount": 42,
        "body_html": FREE_BODY,
        "publishedBylines": [
            {
                "id": 1,
                "name": "Erika Mustermann",
                "publicationUsers": [{"publication": {"id": 7, "name": "The Example Letter"}}],
            }
        ],
    }
    post.update(overrides)
    return post


class FakeSubstack:
    """Fake Substack: post API and RSS feed per publication host, plus files by URL."""

    def __init__(self):
        self.posts: dict[tuple[str, str], dict] = {}
        self.feeds: dict[str, list[str]] = {}
        self.files: dict[str, bytes] = {}
        self.errors: dict[str, int] = {}
        self.requests: list[str] = []
        self.cookies_sent: list[tuple[str, str]] = []
        self.full_posts: dict[int, dict] = {}
        self.valid_cookie: str | None = VALID_COOKIE

    def add(self, post_json: dict, host: str = HOST) -> str:
        """Make a post available via the post API and return its URL."""
        url = f"https://{host}/p/{post_json['slug']}"
        self.posts[(host, post_json["slug"])] = {**post_json, "canonical_url": url}
        return url

    def add_paid(self, post_json: dict, host: str = HOST) -> str:
        """A paid post: publication hosts serve the teaser, substack.com the full text for a valid cookie."""
        url = self.add({**post_json, "audience": "only_paid", "body_html": TEASER_BODY}, host)
        self.full_posts[post_json["id"]] = {**post_json, "audience": "only_paid", "canonical_url": url}
        return url

    def publish(self, *post_jsons: dict, host: str = HOST) -> list[str]:
        """Add posts and put them on top of the host's feed (first argument ends up newest)."""
        urls = [self.add(post_json, host) for post_json in post_jsons]
        self.feeds[host] = [post_json["slug"] for post_json in post_jsons] + self.feeds.get(host, [])
        return urls

    def handler(self, request: httpx.Request) -> httpx.Response:
        url, host, path = str(request.url), request.url.host, request.url.path
        self.requests.append(url)
        if cookie := request.headers.get("cookie"):
            self.cookies_sent.append((host, cookie))
        if url in self.errors:
            return httpx.Response(self.errors[url])
        if url in self.files:
            return httpx.Response(200, content=self.files[url])
        if path == "/feed" and host in self.feeds:
            return httpx.Response(200, content=self._feed_xml(host))
        if host == "substack.com" and path.startswith("/api/v1/posts/by-id/"):
            return self._full_post(int(path.rsplit("/", 1)[1]), request.headers.get("cookie"))
        slug = path.removeprefix("/api/v1/posts/")
        if path.startswith("/api/v1/posts/") and (host, slug) in self.posts:
            return httpx.Response(200, content=json.dumps(self.posts[(host, slug)]))
        return httpx.Response(404)

    def _full_post(self, post_id: int, cookie: str | None) -> httpx.Response:
        if post_id not in self.full_posts:
            return httpx.Response(404)
        post = self.full_posts[post_id]
        logged_in = self.valid_cookie and cookie == f"substack.sid={self.valid_cookie}"
        body = {"post": post if logged_in else {**post, "body_html": TEASER_BODY}, "publication": {"id": 7}}
        if logged_in:
            body["subscription"] = {"membership_state": "subscribed"}
        return httpx.Response(200, content=json.dumps(body))

    def _feed_xml(self, host: str) -> str:
        items = "".join(
            f"<item><title>{post['title']}</title><link>https://{host}/p/{slug}</link>"
            f"<guid isPermaLink=\"false\">https://{host}/p/{slug}</guid>"
            f"<pubDate>{_rfc822(post['post_date'])}</pubDate></item>"
            for slug in self.feeds[host]
            for post in [self.posts[(host, slug)]]
        )
        return f'<?xml version="1.0"?><rss version="2.0"><channel><title>{host}</title>{items}</channel></rss>'

    def api_url(self, slug: str, host: str = HOST) -> str:
        return f"https://{host}/api/v1/posts/{slug}"

    def post_api_requests(self) -> list[str]:
        return [url for url in self.requests if "/api/v1/posts/" in url]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def _rfc822(iso: str) -> str:
    return format_datetime(datetime.fromisoformat(iso.replace("Z", "+00:00")), usegmt=True)


def numbered_post(number: int, **overrides) -> dict:
    """Post number N; higher numbers are newer."""
    return make_post_json(
        id=1000 + number,
        slug=f"post-{number}",
        title=f"Post {number}",
        post_date=f"2026-03-{number:02d}T09:00:00.000Z",
        **overrides,
    )


class MemorySecrets:
    def __init__(self, values: dict[str, str] | None = None):
        self.values = dict(values or {})

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value


class RecordingNotifier:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def notify(self, title: str, message: str) -> None:
        self.messages.append((title, message))


@pytest.fixture
def secrets() -> MemorySecrets:
    return MemorySecrets({"substack.sid": VALID_COOKIE})


@pytest.fixture
def notifier() -> RecordingNotifier:
    return RecordingNotifier()


@pytest.fixture
def substack() -> FakeSubstack:
    return FakeSubstack()


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "Newsletters"


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    return tmp_path / "state" / "state.db"


@pytest.fixture
def state(state_file: Path):
    store = StateStore(state_file)
    yield store
    store.close()


@pytest.fixture
def pipeline(substack, output_dir, state, secrets, notifier):
    with httpx.Client(transport=substack.transport()) as http:
        yield Pipeline(
            Deps(http=http, output_dir=output_dir, state=state, secrets=secrets, notifier=notifier, clock=lambda: NOW)
        )


@pytest.fixture
def cli(substack, secrets, notifier):
    """Run the CLI against the fakes; never touches the real Keychain or notifications."""

    def run(*argv: str) -> int:
        return main(list(argv), transport=substack.transport(), secrets=secrets, notifier=notifier)

    return run


@pytest.fixture
def write_config(tmp_path: Path, output_dir: Path, state_file: Path):
    """Write a config.toml; extra TOML is appended verbatim."""

    def write(extra: str = "") -> Path:
        path = tmp_path / "config.toml"
        path.write_text(f'output_dir = "{output_dir}"\nstate_file = "{state_file}"\n{extra}')
        return path

    return write


def make_image(size=(3000, 1000), mode="RGB", color=(200, 30, 30), format="PNG") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, format=format)
    return out.getvalue()


def read_epub_bytes(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {name: z.read(name) for name in z.namelist()}


def read_epub(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as z:
        return {name: z.read(name).decode("utf-8", errors="replace") for name in z.namelist()}


def assert_epubcheck_clean(path: Path) -> None:
    epubcheck = shutil.which("epubcheck")
    if not epubcheck:
        pytest.skip("epubcheck not installed (brew install epubcheck)")
    # Java's image reader writes cache files to java.io.tmpdir; point it at Python's
    # temp dir, which also works in sandboxes that block the system default.
    java_options = f"{os.environ.get('JAVA_TOOL_OPTIONS', '')} -Djava.io.tmpdir={tempfile.gettempdir()}"
    env = {**os.environ, "JAVA_TOOL_OPTIONS": java_options.strip()}
    result = subprocess.run([epubcheck, str(path)], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
