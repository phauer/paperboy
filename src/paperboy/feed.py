"""Discovers posts from a publication's public RSS feed."""

from email.utils import parsedate_to_datetime

import httpx
from lxml import etree

from paperboy.substack import SourceError, parse_post_url


def discover_post_urls(client: httpx.Client, host: str) -> list[str]:
    """Return the feed's post URLs, newest first."""
    try:
        response = client.get(f"https://{host}/feed")
    except httpx.HTTPError as e:
        raise SourceError(f"Could not reach {host}: {e}") from e
    if response.is_error:
        raise SourceError(f"Feed of {host} returned HTTP {response.status_code}")
    try:
        root = etree.fromstring(response.content)
    except etree.XMLSyntaxError as e:
        raise SourceError(f"Feed of {host} is not valid XML: {e}") from e

    dated = []
    for position, item in enumerate(root.iter("item")):
        link = (item.findtext("link") or "").strip()
        try:
            parse_post_url(link)
        except SourceError:
            continue
        dated.append((_timestamp(item.findtext("pubDate")), -position, link))
    # Newest first by date; feed order breaks ties and covers missing dates.
    return [link for *_, link in sorted(dated, reverse=True)]


def _timestamp(pub_date: str | None) -> float:
    try:
        return parsedate_to_datetime(pub_date).timestamp() if pub_date else 0.0
    except (TypeError, ValueError):
        return 0.0
