from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Post:
    """A source-independent post, ready to be converted into an EPUB."""

    publication: str
    title: str
    subtitle: str
    author: str
    published: datetime
    url: str
    language: str
    body_html: str
