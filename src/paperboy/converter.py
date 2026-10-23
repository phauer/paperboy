"""Converts a Post into EPUB bytes."""

import io
from html import escape

from ebooklib import epub
from lxml import etree, html

from paperboy.post import Post

# Elements that are interactive, decorative or reference remote resources.
# Remote resources are not allowed in an EPUB; images get embedded separately.
_DROPPED_TAGS = (
    "script", "style", "noscript", "svg", "button", "form", "input",
    "iframe", "picture", "source", "img", "video", "audio",
)

# Minimal on purpose: the reader's own typography settings stay in control.
_CSS = """\
img { max-width: 100%; }
pre { white-space: pre-wrap; }
.subtitle { font-style: italic; }
.source { font-size: 0.9em; }
"""


def build_epub(post: Post) -> bytes:
    book = epub.EpubBook()
    book.set_identifier(post.url)
    book.set_title(post.title)
    book.set_language(post.language)
    if post.author:
        book.add_author(post.author)
    book.add_metadata("DC", "date", post.published.date().isoformat())
    book.add_metadata("DC", "publisher", post.publication)

    css = epub.EpubItem(uid="style", file_name="style.css", media_type="text/css", content=_CSS)
    book.add_item(css)

    title_page = _chapter("Title", "title.xhtml", post.language, _title_page_html(post), css)
    content = _chapter(post.title, "content.xhtml", post.language, _content_html(post), css)
    for chapter in (title_page, content):
        book.add_item(chapter)

    book.toc = [epub.Link("content.xhtml", post.title, "content")]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = [title_page, content]

    buffer = io.BytesIO()
    epub.write_epub(buffer, book)
    return buffer.getvalue()


def _chapter(title: str, file_name: str, language: str, body: str, css: epub.EpubItem) -> epub.EpubHtml:
    chapter = epub.EpubHtml(title=title, file_name=file_name, lang=language)
    chapter.content = body
    chapter.add_item(css)
    return chapter


def _title_page_html(post: Post) -> str:
    lines = [f"<h1>{escape(post.title)}</h1>"]
    if post.subtitle:
        lines.append(f'<p class="subtitle">{escape(post.subtitle)}</p>')
    if post.author:
        lines.append(f"<p>{escape(post.author)}</p>")
    lines.append(f"<p>{escape(post.publication)} · {post.published:%Y-%m-%d}</p>")
    return "\n".join(lines)


def _content_html(post: Post) -> str:
    url = escape(post.url)
    return (
        f"{sanitize_body(post.body_html)}\n"
        f'<hr/>\n<p class="source">Original: <a href="{url}">{url}</a></p>'
    )


def sanitize_body(body_html: str) -> str:
    """Strip everything an EPUB can't or shouldn't contain."""
    if not body_html.strip():
        return ""
    root = html.fragment_fromstring(body_html, create_parent="div")
    # Dropping while iterating would skip elements, so take one match at a time.
    while (leftover := next(root.iter(*_DROPPED_TAGS), None)) is not None:
        leftover.drop_tree()
    inner = escape(root.text or "", quote=False)
    inner += "".join(etree.tostring(child, encoding="unicode", method="html") for child in root)
    return inner
