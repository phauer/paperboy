"""Substack source: fetches a post via Substack's (unofficial) post API."""

import re
from datetime import datetime
from urllib.parse import urlparse

import httpx

from paperboy.post import Post

_POST_PATH = re.compile(r"^/p/([^/]+)/?$")


class SourceError(Exception):
    pass


def parse_post_url(url: str) -> tuple[str, str]:
    """Return (host, slug) of a Substack post URL like https://host/p/slug."""
    parsed = urlparse(url)
    match = _POST_PATH.match(parsed.path)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not match:
        raise SourceError(f"Not a Substack post URL (expected https://<host>/p/<slug>): {url}")
    return parsed.netloc, match.group(1)


def fetch_post(client: httpx.Client, url: str) -> Post:
    host, slug = parse_post_url(url)
    try:
        response = client.get(f"https://{host}/api/v1/posts/{slug}")
    except httpx.HTTPError as e:
        raise SourceError(f"Could not reach {host}: {e}") from e
    if response.status_code == 404:
        raise SourceError(f"Post not found: {url}")
    if response.is_error:
        raise SourceError(f"Substack returned HTTP {response.status_code} for {url}")
    return _to_post(response.json(), host)


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
