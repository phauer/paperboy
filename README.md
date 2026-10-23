# paperboy

Turns Substack posts into clean EPUBs for e-ink readers like the Boox Go 7.

## Status

Early work in progress.
It converts new free and paid posts of the configured publications every hour and syncs them to the Boox via Syncthing.
Open work is tracked in `.scratch/paperboy/`.

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
uv run paperboy schedule install                       # run every hour (see below)
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
Its generated cover shows publication, title, author and date in large type, so issues are easy to tell apart as thumbnails.
The publication is set as the series (indexed by date), so readers that support series group and sort the issues.
The body is cleaned up for reading: subscribe and share prompts, comment buttons, the paywall marker and app banners are removed.
Buttons that lead somewhere meaningful (e.g. to a book) stay as plain links; YouTube videos, tweets and embedded posts become links or quotes.
Tracking parameters are stripped from links, footnotes link both ways, code blocks wrap, and section headings appear in the reader's table of contents.

Images are embedded for offline reading and optimized for a monochrome e-ink screen.
They are converted to grayscale with a slight contrast boost, fitted to the Boox Go 7 screen (1264×1680) and stored as JPEG.
An image that can't be downloaded or decoded is left out; its caption and the rest of the post stay.

## Automatic Delivery to the Boox

The Mac converts new posts every hour into `output_dir`; Syncthing carries them to the Boox library.
Delivery happens whenever the Mac is awake and the Boox is on Wi-Fi.

### 1. Choose the Output Folder

Set `output_dir` in `config.toml` to the folder you want to sync, e.g. `~/Books/Newsletters`, and add your `[[publications]]`.
Run `uv run paperboy run` once to check that issues appear there.

### 2. Syncthing on the Mac

```sh
brew install syncthing
brew services start syncthing   # starts now and at every login
```

Open the web UI at <http://127.0.0.1:8384>.

### 3. Syncthing on the Boox

1. Install **Syncthing-Fork** from Google Play (or F-Droid) and grant it access to all files.
2. Allow it to run in the background: in the BOOX app management settings, turn off freezing for Syncthing-Fork, and exempt it from battery optimization.
   Without this, the Boox stops Syncthing as soon as the screen locks.
3. In Syncthing-Fork's settings, check the run conditions (e.g. Wi-Fi only) match how you use the device.

### 4. Pair the Devices and Share the Folder

1. In the Mac web UI, choose **Add Remote Device** and scan or paste the Boox's device ID (shown in Syncthing-Fork).
2. Choose **Add Folder** on the Mac: folder path = your `output_dir`, folder type **Send Only**, and share it with the Boox.
3. Accept the share on the Boox: folder path `/storage/emulated/0/Books/Newsletters`, folder type **Receive Only**.

New issues then appear in NeoReader's library under `Books/Newsletters/<Publication>/`.
Issues you delete on the Boox stay deleted; the Mac never recreates processed posts.

### 5. Run Every Hour

```sh
uv run paperboy schedule install    # installs a launchd job and runs it right away
uv run paperboy schedule show       # prints the job definition
uv run paperboy schedule uninstall  # removes it again
```

The job runs at the start of every hour, and right after the Mac wakes if it slept through a run.
It uses the config file you pass (default `./config.toml`) with an absolute path, so keep the project where it is.
Output and errors go to `~/Library/Logs/paperboy/paperboy.log`.

## Development

```sh
uv run pytest
```

Tests run in parallel (pytest-xdist) and go through the pipeline with a mocked Substack API and anonymized, made-up fixtures.
Tests that build EPUBs validate them with epubcheck and are skipped if it is not installed.
epubcheck's image check needs a writable Java temp folder; the test helper points `java.io.tmpdir` at Python's temp folder, so it also works in sandboxes.
Real API responses for exploration belong in the gitignored `samples/` folder.
