# paperboy

Turns Substack posts into polished EPUBs for a Boox Go 7 and pushes them to the device via BOOX Drop.

## Agent skills

### Issue tracker

Issues and specs live as local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Architecture

- `Pipeline` is the single entry point for the CLI and the tests (`run` for feeds, `convert` for one post); all I/O (HTTP client, output folder, state store, secrets, notifier, clock) comes in via `Deps`.
- `feed` discovers post URLs from a publication's RSS feed; `state` (SQLite) records processed posts, known publications and the last run, and holds the run lock.
  `Pipeline.status` reads it (plus whether a cookie is stored) for the `status` command.
- `substack` fetches a post and normalizes it into a source-independent `Post`; other platforms would add their own source module.
  Paid posts (`audience` ≠ `everyone`) get their full text from `substack.com/api/v1/posts/by-id/<id>` with the cookie, because the cookie doesn't apply on publication hosts.
  A teaser (body far shorter than `wordcount`) raises `AuthExpired`; the pipeline records the post as `pending_auth` and retries it every run.
- `macos` holds the Keychain secret store and the notifier; tests replace both with in-memory fakes (the `cli` test fixture always injects them).
  The test transport routes the Boox's address to a fake BOOX Drop server and everything else to the fake Substack.
- `converter` turns a `Post` into EPUB bytes (ebooklib); `delivery` writes it atomically into `<output>/<Publication>/`.
- `cleanup` removes Substack clutter before images are fetched (`remove_clutter`) and strips leftovers, empty wrappers and tracking afterwards (`tidy`).
- `cover` renders the typographic cover (Helvetica Neue on macOS, Pillow's bundled font elsewhere); `converter` also sets the series metadata (EPUB 3 collection and Calibre).
- `boox` talks to the Boox's built-in BOOX Drop web server (undocumented API, probed in ticket 13); `Pipeline.push` uploads every EPUB from the output folder once, remembering pushed files and sizes in `state`, and `run` pushes at its end.
  An unreachable Boox is normal (it sleeps), not an error.
- `schedule` renders and (un)installs the launchd user agents (hourly `run`, `push` every 5 minutes with a Boox); tests redirect its paths and `launchctl` via an autouse fixture, so they never touch the real system.
- `images` replaces remote images with embedded grayscale JPEGs; the pipeline supplies the download function, so all HTTP stays behind `Deps`.

## Conventions

- Code, comments and docs in English.
- Tests assert outcomes through the pipeline seam (files, EPUB contents, epubcheck), not internals.
- Fixtures are anonymized and made up; real responses stay in the gitignored `samples/`.
- Never write the Substack cookie into files, logs or git.
