from dataclasses import dataclass
from datetime import datetime

TEASER_MARK = " (Teaser)"


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
    teaser: bool = False  # only the free part of a paid post

    @property
    def library_title(self) -> str:
        """The title shown in the library and used for the file, marked when the post is a teaser."""
        return f"{self.title}{TEASER_MARK}" if self.teaser else self.title
