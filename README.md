# paperboy

Turns Substack posts into clean EPUBs for e-ink readers like the Boox Go 7.

## Status

Early work in progress.
Today you can convert a single free post on demand, including its images.
Feed discovery, paid posts, covers and automatic syncing follow (see `.scratch/paperboy/`).

## Setup

Requirements: [uv](https://docs.astral.sh/uv/) and epubcheck (`brew install epubcheck`).

```sh
uv sync
cp config.example.toml config.toml   # then set output_dir
```

## Usage

```sh
uv run paperboy convert https://<publication>/p/<slug>
```

The EPUB lands in `<output_dir>/<Publication>/YYYY-MM-DD <Title>.epub`.
Use `--output <folder>` to override `output_dir`, or `--config <file>` to use another config file.

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
