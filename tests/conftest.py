"""Test seam: the pipeline with fakes at the I/O boundary.

Fixture posts are anonymized and made up, but mirror the structure of real
Substack API responses (bylines, captioned images, buttons, subscribe widgets).
"""

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from lxml import etree
from PIL import Image

from paperboy import schedule
from paperboy.boox import STORAGE_ROOT
from paperboy.cli import main
from paperboy.pipeline import Deps, Pipeline
from paperboy.state import StateStore

HOST = "letter.example.com"
BOOX_URL = "http://192.0.2.10:8085"
LIBRARY = f"{STORAGE_ROOT}/Books/Newsletters"
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
        self.membership = "subscribed"  # the logged-in user's subscription; "free_signup" gets teasers only

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
        paying = logged_in and self.membership == "subscribed"
        # Substack sends "subscription": null when nobody is logged in.
        body = {
            "post": post if paying else {**post, "body_html": TEASER_BODY},
            "publication": {"id": 7},
            "subscription": {"membership_state": self.membership} if logged_in else None,
        }
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


class FakeBoox:
    """Fake BOOX Drop server with an in-memory file system, mirroring the responses probed on a Go 7."""

    def __init__(self):
        self.online = True
        self.dirs = {STORAGE_ROOT, f"{STORAGE_ROOT}/Books"}
        self.files: dict[str, bytes] = {}
        self.uploads: list[str] = []
        self.requests = 0
        self.reject_uploads: set[str] = set()  # file names the device refuses
        self.sleep_after_uploads: int | None = None
        self.page_size = 50

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if not self.online:
            raise httpx.ConnectError("No route to host", request=request)
        path = request.url.path
        if path == "/api/ping":
            return httpx.Response(200, text="ok")
        if path == "/api/storage" and request.method == "GET":
            return self._list(json.loads(request.url.params["args"]))
        if path == "/api/storage/directory" and request.method == "POST":
            return self._make_dir(json.loads(request.content))
        if path == "/api/storage/upload" and request.method == "POST":
            return self._upload(request)
        return httpx.Response(404)

    def _list(self, args: dict) -> httpx.Response:
        folder = args["dir"]
        children = sorted(
            [{"dir": True, "name": d.rsplit("/", 1)[1], "path": d, "size": 3488} for d in self.dirs if _parent(d) == folder]
            + [{"dir": False, "name": f.rsplit("/", 1)[1], "path": f, "size": len(data)}
               for f, data in self.files.items() if _parent(f) == folder],
            key=lambda e: e["name"],
        )
        offset = args.get("offset", 0)
        page = children[offset:offset + self.page_size]
        return _ok({"data": {"count": len(children), "list": page}})

    def _make_dir(self, body: dict) -> httpx.Response:
        path = f"{body['parent']}/{body['name']}"
        if body["parent"] not in self.dirs:
            return _failed("parent not exist")
        if path in self.dirs or path in self.files:
            return _failed("There is file with the same name")
        self.dirs.add(path)
        return _ok({"message": path})

    def _upload(self, request: httpx.Request) -> httpx.Response:
        content_type = request.headers["content-type"].encode()
        message = BytesParser(policy=policy.HTTP).parsebytes(b"Content-Type: " + content_type + b"\r\n\r\n" + request.read())
        fields, name, data = {}, None, b""
        for part in message.iter_parts():
            if part.get_filename():
                name, data = part.get_filename(), part.get_payload(decode=True)
            else:
                fields[part.get_param("name", header="content-disposition")] = part.get_content().strip()
        if fields.get("dir") not in self.dirs:
            return _failed("dir not exist")
        if name in self.reject_uploads:
            return _failed("storage full")
        path = f"{fields['dir']}/{name}"
        self.files[path] = data
        self.uploads.append(path)
        if self.sleep_after_uploads is not None and len(self.uploads) >= self.sleep_after_uploads:
            self.online = False
        return _ok({"message": path})

    def library(self) -> list[str]:
        """Files below the newsletter library, relative to it."""
        return sorted(f.removeprefix(f"{LIBRARY}/") for f in self.files if f.startswith(f"{LIBRARY}/"))


KINDLE_SERIAL = "G000TEST00000001"
_UPLOAD_HOST = "upload.kindle.example"


class FakeKindle:
    """Fake Send to Kindle: Amazon login, device registration, the signed API and the upload host.

    Mirrors the responses seen in the spike (ticket 17); signatures are checked against the issued keys.
    """

    HOSTS = {"api.amazon.com", "firs-ta-g7g.amazon.com", "kd.amazon.com", _UPLOAD_HOST}

    def __init__(self):
        self.online = True
        self.devices = [{"deviceSerialNumber": KINDLE_SERIAL, "deviceName": "Erikas Kindle", "deviceCapabilities": {}}]
        self.codes: dict[str, str | None] = {}  # authorization code -> PKCE challenge
        self.serials: list[str] = []  # registered device serials
        self.keys: dict[str, object] = {}  # adp token -> public key
        self.uploads: dict[str, bytes] = {}
        self.sent: list[dict] = []
        self.api_requests = 0
        self.reject_titles: set[str] = set()

    def authorize(self, login_url: str | None = None) -> str:
        """Sign in like the browser would; returns the URL Amazon redirects to."""
        challenge = parse_qs(urlparse(login_url).query)["openid.oa2.code_challenge"][0] if login_url else None
        code = f"ANcode{len(self.codes)}"
        self.codes[code] = challenge
        return f"https://www.amazon.com/gp/sendtokindle?openid.assoc_handle=amzn_device_na&openid.oa2.authorization_code={code}&"

    def revoke(self) -> None:
        """Invalidate every issued login, like another registration of the same serial did."""
        self.keys.clear()

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.online:
            raise httpx.ConnectError("Could not resolve host", request=request)
        host, path = request.url.host, request.url.path
        if host == "api.amazon.com" and path == "/auth/token":
            return self._token(json.loads(request.content))
        if host == "firs-ta-g7g.amazon.com" and path == "/FirsProxy/registerDeviceWithToken":
            return self._register(request.content)
        if host == _UPLOAD_HOST and request.method == "PUT":
            self.uploads[path.rsplit("/", 1)[1]] = request.read()
            return httpx.Response(200)
        if host == "kd.amazon.com":
            self.api_requests += 1
            if not self._signed(request):
                return httpx.Response(403, json={"Message": "Failed to validate DeviceInfoToken."})
            return self._api(path, json.loads(request.content))
        return httpx.Response(404)

    def _token(self, body: dict) -> httpx.Response:
        code = body["source_token"]
        if code not in self.codes:
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Invalid authorization code"})
        challenge = self.codes.pop(code)
        verifier_hash = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"].encode()).digest()).rstrip(b"=")
        if challenge and verifier_hash.decode() != challenge:
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Code verifier mismatch"})
        return httpx.Response(200, json={"access_token": f"Atza|token{len(self.serials)}", "token_type": "bearer"})

    def _register(self, body: bytes) -> httpx.Response:
        serial = etree.fromstring(body).findtext(".//deviceSerialNumber")
        self.serials.append(serial)
        key = _test_rsa_key()
        token = f"{{enc:adp-token-{len(self.serials)}}}"
        self.keys[token] = key.public_key()
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
        return httpx.Response(200, content=(
            f"<response><adp_token>{token}</adp_token><device_private_key>{pem.decode()}</device_private_key>"
            "<device_type>A1K6D1WRW0MALS</device_type><given_name>Erika</given_name><name>Erika Mustermann</name>"
            "<user_device_name>Erika's Send to Kindle for Mac</user_device_name><home_region>EU</home_region></response>"
        ).encode())

    def _signed(self, request: httpx.Request) -> bool:
        token = request.headers.get("x-adp-authentication-token", "")
        signature, _, date = request.headers.get("x-adp-request-digest", "").partition(":")
        if token not in self.keys:
            return False
        data = "\n".join(["POST", request.url.path, date, request.content.decode(), token]).encode()
        try:
            self.keys[token].verify(base64.b64decode(signature), hashlib.sha256(data).digest(), padding.PKCS1v15(), utils.NoDigestInfo())
        except InvalidSignature:
            return False
        return True

    def _api(self, path: str, body: dict) -> httpx.Response:
        if path == "/import/kindle-doc/get-device-list":
            return httpx.Response(200, json={"ownedDevices": self.devices, "statusCode": 0})
        if path == "/import/upload-url":
            token = f"stk{len(self.uploads) + len(self.sent)}-{body['fileSize']}"
            return httpx.Response(200, json={
                "uploadUrl": f"https://{_UPLOAD_HOST}/upload/{token}", "stkToken": token, "expiryTime": 0, "statusCode": 0,
            })
        if path == "/import/kindle-doc/send-to-kindle":
            data = self.uploads.pop(body["stkToken"], None)
            if data is None or len(data) != body["fileSize"]:
                return httpx.Response(400, json={"Message": "Unknown upload"})
            metadata = body["documentMetadata"]
            if metadata["title"] in self.reject_titles:
                return httpx.Response(400, json={"Message": "Document could not be converted"})
            self.sent.append({**metadata, "targets": body["targetDevices"], "data": data})
            return httpx.Response(200, json={"sku": f"SKU{len(self.sent)}", "statusCode": 0})
        return httpx.Response(404)


def _test_rsa_key():
    """A fresh RSA key per registration; small, since generating 2048-bit keys per test is slow."""
    return rsa.generate_private_key(public_exponent=65537, key_size=1024)


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0]


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "successful": True, **body})


def _failed(message: str) -> httpx.Response:
    return httpx.Response(200, json={"code": -1, "message": message, "successful": False})


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


class FakeLaunchctl:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.fail_bootstrap = False

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(command)
        failed = self.fail_bootstrap and command[1] == "bootstrap"
        return subprocess.CompletedProcess(command, 1 if failed else 0, "", "Bootstrap failed: 5" if failed else "")


@pytest.fixture(autouse=True)
def launchd(tmp_path, monkeypatch) -> FakeLaunchctl:
    """Never touch the real LaunchAgents folder, log folder or launchctl in tests."""
    fake = FakeLaunchctl()
    monkeypatch.setattr(schedule, "LAUNCH_AGENTS", tmp_path / "LaunchAgents")
    monkeypatch.setattr(schedule, "LOG_FILE", tmp_path / "Logs" / "paperboy.log")
    monkeypatch.setattr(schedule, "_launchctl", fake)
    return fake


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
def boox() -> FakeBoox:
    return FakeBoox()


@pytest.fixture
def kindle() -> FakeKindle:
    return FakeKindle()


@pytest.fixture
def transport(substack, boox, kindle) -> httpx.MockTransport:
    """One transport for everything: the Boox by its address, Amazon by its hosts, Substack for the rest."""
    boox_host = httpx.URL(BOOX_URL).host

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.host == boox_host:
            return boox.handler(request)
        if request.url.host in FakeKindle.HOSTS:
            return kindle.handler(request)
        return substack.handler(request)

    return httpx.MockTransport(route)


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
def pipeline(transport, output_dir, state, secrets, notifier):
    with httpx.Client(transport=transport) as http:
        yield Pipeline(
            Deps(http=http, output_dir=output_dir, state=state, secrets=secrets, notifier=notifier, clock=lambda: NOW)
        )


@pytest.fixture
def cli(transport, secrets, notifier):
    """Run the CLI against the fakes; never touches the real Keychain, notifications or Boox."""

    def run(*argv: str) -> int:
        return main(list(argv), transport=transport, secrets=secrets, notifier=notifier)

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
