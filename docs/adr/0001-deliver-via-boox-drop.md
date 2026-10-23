# 0001 — Deliver to the Boox via BOOX Drop Instead of Syncthing

**Status:** accepted (2026-10-24)

## Context

The Mac converts newsletters into EPUBs; they have to reach the NeoReader library on the Boox Go 7 without manual steps.
The original plan was Syncthing: the Mac folder send-only, a Syncthing-Fork folder on the Boox receive-only.
That needs an app on the Boox, exempted from app freezing and battery optimization, plus pairing on both sides.

Boox devices ship with BOOX Drop, a web server for transferring files from a browser.
Probing it on the Go 7 showed a plain HTTP API without authentication that can list folders, create folders, upload and delete files (details in ticket 13).
The server stops while the device sleeps and comes back after waking, without opening the BOOX Drop screen.
Uploaded files appear in the NeoReader library without a refresh.

## Decision

The Mac pushes EPUBs over BOOX Drop into `Books/Newsletters/<Publication>/`.
Syncthing is dropped.

- `run` pushes at its end; a second launchd job pushes every 5 minutes, because the Boox is only reachable while it's awake.
- Each file is pushed once, recorded with its size in the state store.
  Issues deleted on the Boox stay deleted; a regenerated file with a different size replaces the old copy.
- An unreachable Boox is the normal case, not an error: files wait on the Mac.

## Consequences

- Nothing to install, pair or exempt on the Boox; setup is the device's address in `config.toml` and a fixed IP in the router.
- The API is undocumented; a firmware update can change it.
  The EPUBs stay on the Mac, so a broken push loses nothing, and the fake BOOX Drop server in the tests documents the expected behavior.
- Delivery needs the Mac and the Boox awake at the same time; the 5-minute push keeps the wait short.
- The Mac now owns deletions on the Boox (retention, ticket 10) through the same API, instead of Syncthing propagating them.
- Only Boox devices are supported as push targets; another e-reader would need its own delivery module.
