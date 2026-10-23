"""Substack source: fetches a post via Substack's (unofficial) post API."""

import re
from datetime import datetime
from urllib.parse import urlparse

import httpx

from paperboy.post import Post

_POST_PATH = re.compile(r"^/p/([^/]+)/?$")

# The session cookie is scoped to substack.com: publication hosts (custom
# domains or *.substack.com) only ever return the teaser of a paid post,
# while this endpoint returns the full text for a logged-in subscriber.
_FULL_POST_URL = "https://substack.com/api/v1/posts/by-id/{id}"

# A paid body with fewer words than this share of the declared word count is a teaser.
_FULL_TEXT_RATIO = 0.8


COOKIE_NAME = "substack.sid"


class SourceError(Exception):
    pass


class AuthExpired(Exception):
    """A paid post came back as a teaser: the cookie is missing, expired or not subscribed."""


def parse_post_url(url: str) -> tuple[str, str]:
    """Return (host, slug) of a Substack post URL like https://host/p/slug."""
    parsed = urlparse(url)
    match = _POST_PATH.match(parsed.path)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not match:
        raise SourceError(f"Not a Substack post URL (expected https://<host>/p/<slug>): {url}")
    return parsed.netloc, match.group(1)


def fetch_post(client: httpx.Client, url: str, cookie: str | None = None) -> Post:
    host, slug = parse_post_url(url)
    data = _get_json(client, f"https://{host}/api/v1/posts/{slug}", url)
    if data.get("audience", "everyone") != "everyone":
        data = _fetch_full_text(client, data, url, cookie)
    return _to_post(data, host)


def _fetch_full_text(client: httpx.Client, data: dict, url: str, cookie: str | None) -> dict:
    if not cookie:
        raise AuthExpired(f"Paid post needs a Substack cookie: {url}")
    full = _get_json(client, _FULL_POST_URL.format(id=data["id"]), url, cookie)
    post = full.get("post") or {}
    if _is_teaser(post):
        raise AuthExpired(f"Substack returned only the teaser (cookie expired or not subscribed?): {url}")
    return post


def _is_teaser(post: dict) -> bool:
    declared = post.get("wordcount") or 0
    words = len(re.sub(r"<[^>]+>", " ", post.get("body_html") or "").split())
    return not words or words < declared * _FULL_TEXT_RATIO


def _get_json(client: httpx.Client, api_url: str, url: str, cookie: str | None = None) -> dict:
    headers = {"Cookie": f"{COOKIE_NAME}={cookie}"} if cookie else None
    host = httpx.URL(api_url).host
    try:
        response = client.get(api_url, headers=headers)
    except httpx.HTTPError as e:
        raise SourceError(f"Could not reach {host}: {e}") from e
    if response.status_code == 404:
        raise SourceError(f"Post not found: {url}")
    if response.is_error:
        raise SourceError(f"Substack returned HTTP {response.status_code} for {url}")
    return response.json()


def _to_post(data: dict, host: str) -> Post:
    bylines = data.get("publishedBylines") or []
    return Post(
        publication=_publication_name(bylines) or host,
        title=data["title"],
        subtitle=data.get("subtitle") or "",
        author=", ".join(b["name"] for b in bylines if b.get("name")),
        published=datetime.fromisoformat(data["post_date"].replace("Z", "+00:00")),
        url=data.get("canonical_url") or f"https://{host}/p/{data['slug']}",
        language=data.get("language") or "en",
        body_html=data.get("body_html") or "",
    )


def _publication_name(bylines: list[dict]) -> str | None:
    for byline in bylines:
        for publication_user in byline.get("publicationUsers") or []:
            name = (publication_user.get("publication") or {}).get("name")
            if name:
                return name
    return None


def normalize_cookie(raw: str) -> str:
    """Accept the bare cookie value or a pasted 'substack.sid=...' (even within a full Cookie header)."""
    raw = raw.strip()
    for part in raw.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name == COOKIE_NAME:
            raw = value.strip()
            break
    if not raw or any(c in raw for c in " ;\t\n"):
        raise ValueError(f"Expected the value of the {COOKIE_NAME} cookie")
    return raw
