# paperboy

Turns Substack posts into clean EPUBs for e-ink readers like the Boox Go 7.

## Status

Early work in progress.
Today it converts new free and paid posts of the configured publications, including their images.
Covers, body cleanup and automatic syncing follow (see `.scratch/paperboy/`).

## Setup

Requirements: [uv](https://docs.astral.sh/uv/) and epubcheck (`brew install epubcheck`).

```sh
uv sync
cp config.example.toml config.toml   # then set output_dir and your publications
```

## Paid Posts

Paid posts need your Substack login cookie.
Copy it once from your browser and store it in the macOS Keychain:

1. Log in at [substack.com](https://substack.com) in your browser.
2. Open the developer tools (Chrome: `⌥⌘I`) → **Application** → **Cookies** → `https://substack.com`.
3. Copy the value of `substack.sid`.
4. Run `uv run paperboy set-cookie` in a terminal and paste it (input stays hidden).
   Without an interactive terminal, pipe it in from the clipboard instead: `pbpaste | uv run paperboy set-cookie`.

The cookie is sent only to `substack.com`, never stored in files or logs, and only read from the Keychain.
macOS may ask once whether Python may access the Keychain item; choose **Always Allow** so scheduled runs work.

When the cookie expires, paid posts are held back instead of delivered as teasers.
You get one macOS notification; after `set-cookie` with a fresh cookie, the next run delivers all waiting posts.

## Usage

```sh
uv run paperboy run                                    # all new posts of the configured publications
uv run paperboy convert https://<publication>/p/<slug>  # one specific post
uv run paperboy set-cookie                             # store the Substack cookie
```

EPUBs land in `<output_dir>/<Publication>/YYYY-MM-DD <Title>.epub`.
Both commands accept `--config <file>`; `convert` also accepts `--output <folder>` to override `output_dir`.

`run` discovers new posts through each publication's RSS feed.
The first time it sees a publication, it converts only the newest posts (`backfill`, default 3) and marks older ones as skipped.
After that, every new post is converted exactly once, even if you delete the file.
A failing feed or post doesn't stop the others; posts that failed with a network or server error are retried on the next run.
Only one `run` can be active at a time.
`run` exits with code 1 if any errors occurred, so schedulers can flag them.

Each EPUB has a title page (title, subtitle, author, publication, date), the post body and a link to the original at the end.
Images are embedded for offline reading and optimized for a monochrome e-ink screen.
They are converted to grayscale with a slight contrast boost, fitted to the Boox Go 7 screen (1264×1680) and stored as JPEG.
An image that can't be downloaded or decoded is left out; its caption and the rest of the post stay.

## Development

```sh
uv run pytest
```

Tests go through the pipeline with a mocked Substack API and anonymized, made-up fixtures.
Tests that build EPUBs validate them with epubcheck and are skipped if it is not installed.
epubcheck's image check needs a writable Java temp folder; the test helper points `java.io.tmpdir` at Python's temp folder, so it also works in sandboxes.
Real API responses for exploration belong in the gitignored `samples/` folder.
