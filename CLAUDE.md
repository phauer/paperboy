# paperboy

Turns Substack posts into polished EPUBs for a Boox Go 7 and syncs them via Syncthing.

## Agent skills

### Issue tracker

Issues and specs live as local markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Architecture

- `Pipeline` is the single entry point for the CLI and the tests; all I/O (HTTP client, output folder) comes in via `Deps`.
- `substack` fetches a post and normalizes it into a source-independent `Post`; other platforms would add their own source module.
- `converter` turns a `Post` into EPUB bytes (ebooklib); `delivery` writes it atomically into `<output>/<Publication>/`.

## Conventions

- Code, comments and docs in English.
- Tests assert outcomes through the pipeline seam (files, EPUB contents, epubcheck), not internals.
- Fixtures are anonymized and made up; real responses stay in the gitignored `samples/`.
- Never write the Substack cookie into files, logs or git.
