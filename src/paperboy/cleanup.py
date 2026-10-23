"""Turns Substack's body HTML into plain article markup for an e-reader."""

import json
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from lxml import etree, html

# Calls to action and interactive widgets that make no sense in a book.
_WIDGET_COMPONENTS = {
    "SubscribeWidgetToDOM", "PaywallToDOM", "InstallSubstackAppToDOM", "PollToDOM",
    "CommunityChatRCMToDOM", "DirectMessageToDOM", "RecommendationsToDOM",
}
_WIDGET_CLASS_PREFIXES = (
    "subscription-widget", "subscribe-widget", "paywall", "install-substack-app", "poll-embed", "share-dialog",
)
_BUTTON_COMPONENTS = {"ButtonCreateButton", "CaptionedButtonToDOM"}
_BUTTON_CLASSES = {"button-wrapper", "captioned-button-wrap"}
_CTA_TEXT = re.compile(r"\b(subscribe|share|comment|upgrade|pledge|get the app|download the app|refer|gift)", re.I)
_CTA_PATH = re.compile(r"/(subscribe|comments?|share|app|account|referrals?)/?$")

# Elements that are interactive, decorative or reference remote resources.
# Remote resources are not allowed in an EPUB, so images that could not be
# embedded are dropped, too.
_DROPPED_TAGS = (
    "script", "style", "noscript", "svg", "button", "form", "input",
    "iframe", "picture", "source", "img", "video", "audio",
)
_DROPPED_ATTRIBUTES = ("contenteditable", "tabindex", "target")
_KEEP_WHEN_EMPTY = {"img", "hr", "br", "td", "th"}

# Query parameters that only track readers.
_TRACKING_PARAMS = re.compile(r"^(utm_.*|r|ref_source|triedRedirect|showWelcomeOnShare|isFreemail|publication_id|post_id)$")


def remove_clutter(root: html.HtmlElement) -> None:
    """Remove calls to action and turn embeds into links. Runs before images are fetched."""
    for element in list(root.iter()):
        if element.getparent() is None and element is not root:
            continue  # already removed with an ancestor
        component = element.get("data-component-name", "")
        classes = set(element.get("class", "").split())
        if component in _WIDGET_COMPONENTS or any(c.startswith(_WIDGET_CLASS_PREFIXES) for c in classes):
            _remove(element)
        elif component in _BUTTON_COMPONENTS or classes & _BUTTON_CLASSES:
            _simplify_button(element)
        elif component == "Youtube2ToDOM" or "youtube-wrap" in classes:
            _replace_embed(element, _youtube_link)
        elif component == "Twitter2ToDOM" or "tweet" in classes:
            _replace_embed(element, _tweet_quote)
        elif component == "EmbeddedPostToDOM" or "embedded-post-wrap" in classes:
            _replace_embed(element, _post_link)


def tidy(root: html.HtmlElement) -> None:
    """Strip what an EPUB can't contain, leftover empty wrappers and tracking. Runs after images are embedded."""
    # Dropping while iterating would skip elements, so take one match at a time.
    while (element := next(_droppable(root), None)) is not None:
        element.drop_tree()
    for element in list(root.iter("iframe")):
        _remove(element)
    for element in reversed(list(root.iter("div", "span", "p", "a", "figure", "picture"))):
        if element is not root and _is_empty(element):
            element.drop_tree()
    for element in root.iter():
        for name in list(element.attrib):
            if name.startswith("data-") or name in _DROPPED_ATTRIBUTES:
                del element.attrib[name]
        if element.tag == "a" and element.get("href"):
            element.set("href", strip_tracking(element.get("href")))


def strip_tracking(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query or parsed.scheme not in ("http", "https"):
        return url
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not _TRACKING_PARAMS.match(k)]
    return urlunparse(parsed._replace(query=urlencode(query)))


def _droppable(root: html.HtmlElement):
    return (
        element
        for element in root.iter(*_DROPPED_TAGS)
        if not (element.tag == "img" and not _is_remote(element.get("src")))
    )


def _is_remote(src: str | None) -> bool:
    return not src or "://" in src or src.startswith(("//", "data:"))


def _is_empty(element: html.HtmlElement) -> bool:
    if element.text_content().strip() or element.get("id"):
        return False
    return not any(child.tag in _KEEP_WHEN_EMPTY or child.get("id") for child in element.iterdescendants())


def _remove(element: html.HtmlElement) -> None:
    # Unlike drop_tree(), keep the text that follows the element.
    parent = element.getparent()
    if parent is None:
        return
    if element.tail:
        previous = element.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + element.tail
        else:
            parent.text = (parent.text or "") + element.tail
    parent.remove(element)


def _simplify_button(element: html.HtmlElement) -> None:
    """Drop calls to action; keep buttons that lead somewhere meaningful as plain links."""
    link = next(element.iter("a"), None)
    href, text = (link.get("href", ""), link.text_content().strip()) if link is not None else ("", "")
    if not href or not text or _CTA_TEXT.search(text) or _CTA_PATH.search(urlparse(href).path) or "action=share" in href:
        _remove(element)
        return
    _swap(element, _link_paragraph(href, text))


def _replace_embed(element: html.HtmlElement, build) -> None:
    try:
        attrs = json.loads(element.get("data-attrs") or "{}")
    except json.JSONDecodeError:
        attrs = {}
    replacement = build(attrs)
    if replacement is None:
        _remove(element)
    else:
        _swap(element, replacement)


def _youtube_link(attrs: dict) -> html.HtmlElement | None:
    video_id = attrs.get("videoId")
    if not video_id:
        return None
    return _link_paragraph(f"https://www.youtube.com/watch?v={video_id}", "Video on YouTube")


def _tweet_quote(attrs: dict) -> html.HtmlElement | None:
    url, text = attrs.get("url"), attrs.get("full_text")
    if not url or not text:
        return None
    quote = html.Element("blockquote")
    etree.SubElement(quote, "p").text = text
    source = etree.SubElement(quote, "p")
    source.text = "— "
    author = attrs.get("name") or attrs.get("username") or "Post"
    anchor = etree.SubElement(source, "a", href=url)
    anchor.text = f"{author} (@{attrs['username']})" if attrs.get("username") and attrs.get("name") else author
    return quote


def _post_link(attrs: dict) -> html.HtmlElement | None:
    url, title = attrs.get("url"), attrs.get("title")
    return _link_paragraph(url, title) if url and title else None


def _link_paragraph(url: str, text: str) -> html.HtmlElement:
    paragraph = html.Element("p", {"class": "link"})
    anchor = etree.SubElement(paragraph, "a", href=url)
    anchor.text = text
    return paragraph


def _swap(old: html.HtmlElement, new: html.HtmlElement) -> None:
    new.tail = old.tail
    old.getparent().replace(old, new)
