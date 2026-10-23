import re

import pytest

from conftest import assert_epubcheck_clean, make_image, make_post_json, read_epub


def _convert(pipeline, substack, body: str) -> dict[str, str]:
    path = pipeline.convert(substack.add(make_post_json(body_html=body)))
    assert_epubcheck_clean(path)
    return read_epub(path)


def _content(pipeline, substack, body: str) -> str:
    return _convert(pipeline, substack, body)["EPUB/content.xhtml"]


def _button(url: str, text: str) -> str:
    return (
        f'<p class="button-wrapper" data-attrs="{{&quot;url&quot;:&quot;{url}&quot;}}" '
        f'data-component-name="ButtonCreateButton"><a class="button primary" href="{url}"><span>{text}</span></a></p>'
    )


SUBSCRIBE_WIDGET = (
    '<div class="subscription-widget-wrap-editor" data-attrs="{}" data-component-name="SubscribeWidgetToDOM">'
    '<div class="subscription-widget show-subscribe"><div class="preamble"><p>Thanks for reading! Subscribe for free.</p></div>'
    '<form class="subscription-widget-subscribe"><input type="email" class="email-input" name="email"/>'
    '<input type="submit" class="button primary" value="Subscribe"/></form></div></div>'
)
PAYWALL = '<div class="paywall-jump" data-component-name="PaywallToDOM"></div>'
CAPTIONED_CTA = (
    '<div class="captioned-button-wrap" data-component-name="CaptionedButtonToDOM"><div class="preamble">'
    '<p>Share this post with a friend.</p></div>' + _button("https://letter.example.com/p/a-fine-post?action=share", "Share") + "</div>"
)


@pytest.mark.parametrize(
    "widget, gone",
    [
        (SUBSCRIBE_WIDGET, "Subscribe for free"),
        (PAYWALL, "paywall"),
        (CAPTIONED_CTA, "Share this post"),
        (_button("https://letter.example.com/subscribe?coupon=x", "Subscribe now"), "Subscribe now"),
        (_button("https://letter.example.com/p/a-fine-post/comments", "Leave a comment"), "Leave a comment"),
        (_button("https://letter.example.com/p/a-fine-post?utm_content=share&action=share", "Share"), ">Share<"),
        (_button("https://letter.example.com/subscribe", "Upgrade to paid"), "Upgrade"),
        (_button("https://substack.com/app", "Get the app"), "Get the app"),
    ],
)
def test_calls_to_action_are_removed(pipeline, substack, widget, gone):
    content = _content(pipeline, substack, f"<p>Before.</p>{widget}<p>After.</p>")

    assert gone not in content
    assert "Before." in content and "After." in content


def test_meaningful_button_becomes_a_plain_link(pipeline, substack):
    content = _content(pipeline, substack, _button("https://books.example.org/fine-book", "Get the ebook or print book"))

    assert re.search(r'<p class="link"><a href="https://books.example.org/fine-book">Get the ebook or print book</a></p>', content)
    assert "button" not in content


def test_youtube_embed_becomes_a_link(pipeline, substack):
    embed = (
        '<div id="youtube2-abc123" class="youtube-wrap" data-attrs="{&quot;videoId&quot;:&quot;abc123&quot;}" '
        'data-component-name="Youtube2ToDOM"><div class="youtube-inner">'
        '<iframe src="https://www.youtube-nocookie.com/embed/abc123" frameborder="0"></iframe></div></div>'
    )

    content = _content(pipeline, substack, embed)

    assert '<a href="https://www.youtube.com/watch?v=abc123">Video on YouTube</a>' in content
    assert "<iframe" not in content


def test_tweet_embed_becomes_a_quote(pipeline, substack):
    embed = (
        '<div class="tweet" data-attrs="{&quot;url&quot;:&quot;https://x.com/maxm/status/1&quot;,'
        '&quot;full_text&quot;:&quot;Shipping on a Friday.&quot;,&quot;username&quot;:&quot;maxm&quot;,'
        '&quot;name&quot;:&quot;Max Mustermann&quot;}" data-component-name="Twitter2ToDOM">'
        '<a class="tweet-link-top" href="https://x.com/maxm/status/1"><div class="tweet-header"></div></a></div>'
    )

    content = _content(pipeline, substack, embed)

    assert re.search(r"<blockquote><p>Shipping on a Friday.</p><p>— <a href=\"https://x.com/maxm/status/1\">Max Mustermann \(@maxm\)</a></p></blockquote>", content)


def test_embedded_post_becomes_a_link(pipeline, substack):
    embed = (
        '<div class="embedded-post-wrap" data-attrs="{&quot;url&quot;:&quot;https://other.example.com/p/older&quot;,'
        '&quot;title&quot;:&quot;An older post&quot;}" data-component-name="EmbeddedPostToDOM">'
        '<a class="embedded-post" href="https://other.example.com/p/older"><div class="embedded-post-header">'
        '<span class="embedded-post-publication-name">Other</span></div><div class="embedded-post-title">An older post</div>'
        '<div class="embedded-post-cta-wrapper"><span class="embedded-post-cta">Read full story</span></div></a></div>'
    )

    content = _content(pipeline, substack, embed)

    assert '<a href="https://other.example.com/p/older">An older post</a>' in content
    assert "Read full story" not in content


def test_embed_with_broken_attributes_is_removed(pipeline, substack):
    content = _content(pipeline, substack, '<p>Text.</p><div class="youtube-wrap" data-attrs="{not json"><iframe src="https://x"></iframe></div>')

    assert "youtube" not in content and "Text." in content


def test_tracking_parameters_are_removed_from_links(pipeline, substack):
    body = (
        '<p><a href="https://example.org/article?id=7&amp;utm_source=substack&amp;utm_medium=email">One</a> '
        '<a href="https://letter.example.com/p/other?r=abc12&amp;triedRedirect=true">Two</a> '
        '<a href="https://example.org/clean">Three</a></p>'
    )

    content = _content(pipeline, substack, body)

    assert 'href="https://example.org/article?id=7"' in content
    assert 'href="https://letter.example.com/p/other"' in content
    assert 'href="https://example.org/clean"' in content


def test_tracking_pixel_is_dropped(pipeline, substack):
    pixel = "https://track.example.com/pixel.gif"
    substack.files[pixel] = make_image(size=(1, 1), format="GIF", mode="P", color=0)

    files = _convert(pipeline, substack, f'<p>Text.<img src="{pixel}" width="1" height="1"/></p>')

    assert not any(name.startswith("EPUB/images/") for name in files)
    assert "<img" not in files["EPUB/content.xhtml"]


def test_figure_caption_survives_cleanup(pipeline, substack):
    content = _content(pipeline, substack, make_post_json()["body_html"])

    assert "Source: Max Mustermann" in content


def test_footnotes_link_both_ways(pipeline, substack):
    body = (
        '<p>A claim<a class="footnote-anchor" data-component-name="FootnoteAnchorToDOM" id="footnote-anchor-1" '
        'href="#footnote-1" target="_self">1</a>.</p>'
        '<div class="footnote" data-component-name="FootnoteToDOM"><a id="footnote-1" href="#footnote-anchor-1" '
        'class="footnote-number" contenteditable="false" target="_self">1</a>'
        '<div class="footnote-content"><p>The source of the claim.</p></div></div>'
    )

    content = _content(pipeline, substack, body)

    assert re.search(r'<a [^>]*id="footnote-anchor-1"[^>]*href="#footnote-1"|<a [^>]*href="#footnote-1"[^>]*id="footnote-anchor-1"', content)
    assert re.search(r'<a [^>]*id="footnote-1"[^>]*href="#footnote-anchor-1"|<a [^>]*href="#footnote-anchor-1"[^>]*id="footnote-1"', content)
    assert "The source of the claim." in content
    assert "contenteditable" not in content and "target=" not in content


def test_code_blocks_wrap(pipeline, substack):
    files = _convert(pipeline, substack, '<pre><code>very_long_identifier = compute(' + "x" * 200 + ")</code></pre>")

    assert "<pre><code>very_long_identifier" in files["EPUB/content.xhtml"]
    assert "white-space: pre-wrap" in files["EPUB/style.css"]


def test_headings_appear_in_navigation(pipeline, substack):
    body = "<h2>First part</h2><p>a</p><h3>Detail</h3><p>b</p><h2>Second part</h2><p>c</p><h2> </h2>"

    files = _convert(pipeline, substack, body)

    nav, content = files["EPUB/nav.xhtml"], files["EPUB/content.xhtml"]
    assert re.findall(r'href="content.xhtml#([^"]+)">([^<]+)<', nav) == [
        ("section-1", "First part"), ("section-2", "Detail"), ("section-3", "Second part"),
    ]
    assert re.search(r"First part.*<ol>.*Detail.*</ol>.*Second part", nav, re.S), "h3 nests under its h2"
    for anchor in ("section-1", "section-2", "section-3"):
        assert f'id="{anchor}"' in content


def test_existing_heading_ids_are_kept(pipeline, substack):
    files = _convert(pipeline, substack, '<h2 id="intro">Intro</h2><p>a</p>')

    assert 'href="content.xhtml#intro"' in files["EPUB/nav.xhtml"]


def test_css_does_not_force_fonts_or_sizes(pipeline, substack):
    files = _convert(pipeline, substack, "<p>Text.</p>")

    css = files["EPUB/style.css"]
    assert "font-family" not in css
    assert not re.search(r"font-size:\s*[\d.]+(px|pt)", css)
    assert not any(name.endswith((".ttf", ".otf", ".woff", ".woff2")) for name in files)


def test_data_attributes_and_empty_wrappers_are_removed(pipeline, substack):
    content = _content(pipeline, substack, make_post_json()["body_html"])

    assert "data-" not in content
    assert "image-link-expand" not in content and "pencraft" not in content
