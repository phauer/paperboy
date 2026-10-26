# Lessons: Running Commands in the Claude Code Sandbox

Some commands fail inside the Claude Code Bash sandbox and need the unsandboxed run right away. Retrying them sandboxed only costs time.

## Commands That Need the Unsandboxed Run

- **git** with staging or commits, and **brew install**.
- **`gh api`**: TLS verification fails inside the sandbox (`OSStatus -26276`).
- **Requests to the Boox on the LAN** (BOOX Drop at the address in `config.toml`): `curl` fails with exit code 7.
- **Send to Kindle traffic to Amazon**: clients that use `http.client` directly ignore the sandbox's proxy and fail with a DNS error (`gaierror 8`).
- **`uv run paperboy` commands** that touch the Keychain, launchd, the Boox, Amazon or the real state store in `~/Library/Application Support/paperboy/`.

Everything else stays sandboxed, including the test suite, which only talks to fakes.

## Temp Files

`$TMPDIR` is a different folder inside and outside the sandbox. A file written by an unsandboxed command isn't at the sandboxed `$TMPDIR` path. Use the session scratchpad with an absolute path instead.

## Interactive Logins

Commands that read a long pasted line (e.g. the Amazon redirect URL in `kindle-login`) must run in a real terminal, not through Claude Code. On macOS, `input()` hangs on lines over 1024 characters unless `readline` is imported.
