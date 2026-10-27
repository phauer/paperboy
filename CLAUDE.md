# paperboy

Turns Substack posts into polished EPUBs for a Boox Go 7 and pushes them to the device via BOOX Drop.

## Agent skills

### Issue tracker

Issues and specs live as local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Lessons Learned

Read the matching file before starting such work:

- [Adding a delivery target](docs/lessons/new-delivery-targets.md): probe the real device first, distrust unofficial clients, deliver once via state, prepare state before a target goes live.
- [Claude Code sandbox](docs/lessons/claude-code-sandbox.md): which commands need the unsandboxed run, temp files, interactive logins.
- [Working on tickets](docs/lessons/working-on-tickets.md): tick only what's done, fold tickets after a spike, park instead of finishing for completeness.

## Architecture

- `Pipeline` is the single entry point for the CLI and the tests (`run` for feeds, `convert` for one post); all I/O (HTTP client, output folder, state store, secrets, notifier, clock) comes in via `Deps`.
- `feed` discovers post URLs from a publication's RSS feed; `state` (SQLite) records processed posts, known publications and the last run, and holds the run lock. `Pipeline.status` reads it (plus whether a cookie is stored) for the `status` command.
- `substack` fetches a post and normalizes it into a source-independent `Post`; other platforms would add their own source module. Paid posts (`audience` ≠ `everyone`) get their full text from `substack.com/api/v1/posts/by-id/<id>` with the cookie, because the cookie doesn't apply on publication hosts. A teaser (body far shorter than `wordcount`) is told apart by the response's `subscription`: a logged-in free subscriber gets a `Post` with `teaser=True` (title suffix "(Teaser)", gray diagonal "TEASER" watermark on the cover, link to the full post); with no login (`subscription` null) or a paid subscription, it raises `AuthExpired`, and the pipeline records the post as `pending_auth` and retries it every run.
- `macos` holds the Keychain secret store and the notifier; tests replace both with in-memory fakes (the `cli` test fixture always injects them). The test transport routes the Boox's address to a fake BOOX Drop server and everything else to the fake Substack.
- `converter` turns a `Post` into EPUB bytes (ebooklib); `delivery` writes it atomically into `<output>/<Publication>/`.
- `cleanup` removes Substack clutter before images are fetched (`remove_clutter`) and strips leftovers, empty wrappers and tracking afterwards (`tidy`).
- `cover` renders the typographic cover (Helvetica Neue on macOS, Pillow's bundled font elsewhere), dropping characters the font has no glyph for (detected by comparing with the font's "missing glyph" box), with the publication's logo above the footer. `substack` takes the logo from the byline's publication (`logo_url`) and routes it through `substackcdn.com`, because raw S3 links can be denied; `converter` downloads it with the pipeline's image fetcher. A missing or broken logo leaves a text-only cover; `converter` also sets the series metadata (EPUB 3 collection and Calibre).
- `boox` talks to the Boox's built-in BOOX Drop web server (undocumented API, probed in ticket 13); `Pipeline.push` uploads every EPUB from the output folder once, remembering pushed files and sizes in `state`, and `run` pushes at its end. An unreachable Boox is normal (it sleeps), not an error.
- `kindle` talks to Amazon's Send to Kindle service (undocumented protocol of the desktop app, ported from `stkclient` onto httpx, ADR 0002): `kindle-login` registers paperboy with a random device serial and stores the signing credential in the Keychain. `run` and `push` send each EPUB once per target; `state` records deliveries per target (`boox`, `kindle`). The publication goes out as the author, because the Kindle ignores series. A rejected login notifies once, like an expired cookie. The test transport routes Amazon's hosts to a fake Send to Kindle server that checks request signatures.
- `schedule` renders and (un)installs the launchd user agents (hourly `run`, `push` every 5 minutes with a Boox); tests redirect its paths and `launchctl` via an autouse fixture, so they never touch the real system.
- `images` replaces remote images with embedded grayscale JPEGs; the pipeline supplies the download function, so all HTTP stays behind `Deps`.

## Conventions

- Code, comments and docs in English.
- Tests assert outcomes through the pipeline seam (files, EPUB contents, epubcheck), not internals.
- Fixtures are anonymized and made up; real responses stay in the gitignored `samples/`.
- Never write the Substack cookie or the Kindle login into files, logs or git.
- The installed launchd jobs run this working tree with the real `config.toml`, so code and config changes go live at the next 5-minute push. Before enabling a new delivery target, prepare the state first (e.g. mark the backlog as delivered), or uninstall the schedule meanwhile.
