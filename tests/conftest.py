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
from pathlib import Path

import httpx
import pytest
from PIL import Image

from paperboy.pipeline import Deps, Pipeline

HOST = "letter.example.com"
IMAGE_URL = "https://substackcdn.com/image/fetch/x.png"

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
    """Serves post JSON by slug, like the Substack post API, plus image files by URL."""

    def __init__(self):
        self.posts: dict[str, dict] = {}
        self.files: dict[str, bytes] = {}
        self.requests: list[str] = []
        self.status_override: int | None = None

    def add(self, post_json: dict) -> str:
        self.posts[post_json["slug"]] = post_json
        return f"https://{HOST}/p/{post_json['slug']}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if str(request.url) in self.files:
            return httpx.Response(200, content=self.files[str(request.url)])
        if self.status_override:
            return httpx.Response(self.status_override)
        prefix = "/api/v1/posts/"
        slug = request.url.path.removeprefix(prefix)
        if request.url.host == HOST and request.url.path.startswith(prefix) and slug in self.posts:
            return httpx.Response(200, content=json.dumps(self.posts[slug]))
        return httpx.Response(404)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def substack() -> FakeSubstack:
    return FakeSubstack()


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "Newsletters"


@pytest.fixture
def pipeline(substack: FakeSubstack, output_dir: Path):
    with httpx.Client(transport=substack.transport()) as http:
        yield Pipeline(Deps(http=http, output_dir=output_dir))


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
