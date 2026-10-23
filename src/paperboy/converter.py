"""Converts a Post into EPUB bytes."""

import io
from dataclasses import dataclass
from html import escape

from ebooklib import epub
from lxml import etree, html

from paperboy.cleanup import remove_clutter, tidy
from paperboy.images import EmbeddedImage, FetchImage, embed_images
from paperboy.post import Post

# Minimal on purpose: the reader's own typography settings stay in control.
_CSS = """\
img { max-width: 100%; height: auto; }
figure { margin: 1em 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
.subtitle { font-style: italic; }
.source { font-size: 0.9em; }
"""


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    anchor: str


def build_epub(post: Post, fetch_image: FetchImage) -> bytes:
    body_html, images, headings = _prepare_body(post, fetch_image)
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
    content = _chapter(post.title, "content.xhtml", post.language, _content_html(post, body_html), css)
    for chapter in (title_page, content):
        book.add_item(chapter)
    for number, image in enumerate(images, start=1):
        book.add_item(
            epub.EpubImage(uid=f"image-{number}", file_name=image.file_name, media_type="image/jpeg", content=image.data)
        )

    book.toc = _toc(post, headings)
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


def _content_html(post: Post, body_html: str) -> str:
    url = escape(post.url)
    return (
        f"{body_html}\n"
        f'<hr/>\n<p class="source">Original: <a href="{url}">{url}</a></p>'
    )


def _prepare_body(post: Post, fetch_image: FetchImage) -> tuple[str, list[EmbeddedImage], list[Heading]]:
    """Clean the body, embed its images and collect its headings."""
    if not post.body_html.strip():
        return "", [], []
    root = html.fragment_fromstring(post.body_html, create_parent="div")
    remove_clutter(root)  # first, so images of removed widgets aren't downloaded
    images = embed_images(root, post.url, fetch_image)
    tidy(root)
    headings = _anchor_headings(root)
    inner = escape(root.text or "", quote=False)
    inner += "".join(etree.tostring(child, encoding="unicode", method="html") for child in root)
    return inner, images, headings


def _anchor_headings(root: html.HtmlElement) -> list[Heading]:
    """Give section headings ids, so the EPUB navigation can jump to them."""
    headings = []
    for element in root.iter("h1", "h2", "h3"):
        text = " ".join(element.text_content().split())
        if not text:
            continue
        anchor = element.get("id") or f"section-{len(headings) + 1}"
        element.set("id", anchor)
        headings.append(Heading(level=1 if element.tag in ("h1", "h2") else 2, text=text, anchor=anchor))
    return headings


def _toc(post: Post, headings: list[Heading]) -> list:
    """The post, followed by its sections; h3 nest under the preceding h1/h2."""
    toc: list = [epub.Link("content.xhtml", post.title, "content")]
    for heading in headings:
        link = epub.Link(f"content.xhtml#{heading.anchor}", heading.text, heading.anchor)
        parent = toc[-1]
        if heading.level == 1 or len(toc) == 1:
            toc.append(link)
        elif isinstance(parent, epub.Link):
            toc[-1] = (epub.Section(parent.title, parent.href), [link])
        else:
            parent[1].append(link)
    return toc
