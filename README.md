# paperboy

Turns Substack posts into clean EPUBs for e-ink readers like the Boox Go 7.

## Status

Early work in progress. It converts new free and paid posts of the configured publications every hour and pushes them to the Boox via BOOX Drop.

## Disclaimer

paperboy is a personal tool and not affiliated with or endorsed by Substack. It reads what you can already read: free posts, and paid posts of publications you subscribe to, using your own login cookie. The cookie stays in your macOS Keychain and is only ever sent to Substack. The EPUBs are meant for your own reading, so please don't share them and keep supporting the authors you read. You are responsible for using the tool in line with Substack's terms; it checks each feed once per hour. It is released under the [MIT License](LICENSE), without any warranty.

## System Requirements

- **macOS.** paperboy is built and tested on macOS only. It stores the Substack cookie in the macOS Keychain, shows notifications through `osascript` and runs on a schedule as launchd user agents. Linux and Windows are not supported.
- **Python 3.13 and [uv](https://docs.astral.sh/uv/).** uv installs Python and the dependencies for you with `uv sync`.
- **A Boox e-reader for automatic delivery.** The only supported delivery channel is BOOX Drop, the web server built into the Boox, which `push` uploads to over your local network. It is tested with a Boox Go 7. Without a Boox, paperboy still writes the EPUBs to your output folder, and you can get them onto another reader yourself, for example with a synced folder or by hand.
- **A Substack account for paid posts.** Free posts need nothing; paid posts need your login cookie, see Paid Posts below.
- **epubcheck (optional).** Only the tests use it (`brew install epubcheck`).

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), see System Requirements below.

```sh
uv sync
cp config.example.toml config.toml   # then set output_dir and your publications
```

## Paid Posts

Paid posts need your Substack login cookie. Copy it once from your browser and store it in the macOS Keychain:

1. Log in at [substack.com](https://substack.com) in your browser.
2. Open the developer tools (Chrome: `⌥⌘I`) → **Application** → **Cookies** → `https://substack.com`.
3. Copy the value of `substack.sid`.
4. Run `uv run paperboy set-cookie` in a terminal and paste it (input stays hidden). Without an interactive terminal, pipe it in from the clipboard instead: `pbpaste | uv run paperboy set-cookie`.

The cookie is sent only to `substack.com`, never stored in files or logs, and only read from the Keychain. macOS may ask once whether Python may access the Keychain item; choose **Always Allow** so scheduled runs work.

When the cookie expires, paid posts are held back instead of delivered as teasers. You get one macOS notification; after `set-cookie` with a fresh cookie, the next run delivers all waiting posts.

## Usage

```sh
uv run paperboy run                                    # all new posts of the configured publications
uv run paperboy convert https://<publication>/p/<slug>  # one specific post
uv run paperboy status                                 # waiting, failed and skipped posts, last run
uv run paperboy set-cookie                             # store the Substack cookie
uv run paperboy schedule install                       # run every hour (see below)
```

EPUBs land in `<output_dir>/<Publication>/YYYY-MM-DD <Title>.epub`. Both commands accept `--config <file>`; `convert` also accepts `--output <folder>` to override `output_dir`.

`run` discovers new posts through each publication's RSS feed. The first time it sees a publication, it converts only the newest posts (`backfill`, default 3) and marks older ones as skipped. After that, every new post is converted exactly once, even if you delete the file. A failing feed or post doesn't stop the others; posts that failed with a network or server error are retried on the next run. Only one `run` can be active at a time. `run` exits with code 1 if any errors occurred, so schedulers can flag them.

`status` shows the pipeline's health without touching the network. It lists, per publication, the posts that wait for login, failed or were skipped for a reason, each with the date of its last attempt. Posts skipped as backfill are only counted. It also shows when the last `run` finished and its errors, and whether a cookie is stored (never its value). When nothing waits or failed, it says so.

Each EPUB has a title page (title, subtitle, author, publication, date), the post body and a link to the original at the end. Its generated cover shows publication, title, author and date in large type, so issues are easy to tell apart as thumbnails. Publication, author and date are right-aligned, so NeoReader's badges on the left ("NEW", progress, "EPUB") don't cover them. The publication is set as the series (indexed by date), so readers that support series group and sort the issues. The body is cleaned up for reading: subscribe and share prompts, comment buttons, the paywall marker and app banners are removed. Buttons that lead somewhere meaningful (e.g. to a book) stay as plain links; YouTube videos, tweets and embedded posts become links or quotes. Tracking parameters are stripped from links, footnotes link both ways, code blocks wrap, and section headings appear in the reader's table of contents.

Images are embedded for offline reading and optimized for a monochrome e-ink screen. They are converted to grayscale with a slight contrast boost, fitted to the Boox Go 7 screen (1264×1680) and stored as JPEG. An image that can't be downloaded or decoded is left out; its caption and the rest of the post stay.

## Automatic Delivery to the Boox

The Mac converts new posts every hour into `output_dir` and pushes them to the Boox over **BOOX Drop**, the web server built into Boox devices. Nothing needs to be installed on the Boox. The Boox's server only answers while the device is awake and on Wi-Fi, so the Mac tries every 5 minutes; until then, new issues wait on the Mac.

### 1. Choose the Output Folder

Set `output_dir` in `config.toml`, e.g. `~/Books/Newsletters`, and add your `[[publications]]`. Run `uv run paperboy run` once to check that issues appear there.

### 2. Connect the Boox

1. On the Boox, open **BOOX Drop** once. It shows the device's address, e.g. `http://192.168.1.50:8085`.
2. Give the Boox a fixed IP address in your router, so the address doesn't change.
3. Add it to `config.toml`:

   ```toml
   [boox]
   url = "http://192.168.1.50:8085"
   # folder = "Books/Newsletters"   # library folder on the Boox (default)
   ```

4. Push once by hand:

   ```sh
   uv run paperboy push
   ```

Issues land in `Books/Newsletters/<Publication>/` and appear in NeoReader's library without a refresh. You don't need to keep the BOOX Drop screen open; the server survives short sleeps. After a longer sleep (e.g. overnight) the Boox may stop the server, so open BOOX Drop again; waiting issues are pushed within 5 minutes.

Each issue is pushed once. Issues you delete on the Boox stay deleted, and the Mac never recreates processed posts. An issue that is converted again with a different file size replaces the copy on the Boox.

### 3. Run on a Schedule

```sh
uv run paperboy schedule install    # installs the launchd jobs and runs once right away
uv run paperboy schedule show       # prints the job definitions
uv run paperboy schedule uninstall  # removes them again
```

Two jobs are installed:

- **Hourly run:** at the start of every hour, and right after the Mac wakes if it slept through a run; it converts new posts and pushes them.
- **Push every 5 minutes** (only with a `[boox]` section): delivers waiting issues as soon as the Boox is awake; it stays silent when there's nothing to do.

They use the config file you pass (default `./config.toml`) with an absolute path, so keep the project where it is. Run `schedule install` again after adding or removing the `[boox]` section. Output and errors go to `~/Library/Logs/paperboy/paperboy.log`.

## Development

```sh
uv run pytest
```

Tests run in parallel (pytest-xdist) and go through the pipeline with a mocked Substack API and anonymized, made-up fixtures. Tests that build EPUBs validate them with epubcheck and are skipped if it is not installed. epubcheck's image check needs a writable Java temp folder; the test helper points `java.io.tmpdir` at Python's temp folder, so it also works in sandboxes. Real API responses for exploration belong in the gitignored `samples/` folder.
