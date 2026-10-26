# 0002: Deliver to the Kindle via the Send to Kindle Protocol

**Status:** accepted (2026-10-26)

## Context

A Kindle Scribe is being tried next to the Boox Go 7, and new issues should reach both. Kindles don't open raw EPUBs copied over USB, and recent Scribe firmware uses MTP instead of a mountable drive, so sideloading is out. That leaves Amazon's Send to Kindle, which converts EPUBs into Kindle documents. It is reachable two ways: by email to the Kindle's `@kindle.com` address (official, needs an SMTP account and an approved sender), or through the protocol of Amazon's Send to Kindle desktop app (unofficial, implemented by `stkclient` and Papir).

The spike (ticket 17) sent paperboy EPUBs through `stkclient`:

- They convert fine, keep paperboy's cover as the thumbnail and read well on the Scribe.
- The Kindle drops series metadata and has no folders; Collections can only be filled by hand. It shows the title and author passed at send time, not the EPUB's own.
- Amazon doesn't deduplicate: sending a file twice creates a second document.
- There is no call to delete or replace a sent document.
- `stkclient` registers every install with the same hardcoded device serial, so another user's login invalidates ours (`403 Failed to validate DeviceInfoToken`). A login with its own random serial worked.

## Decision

Paperboy sends EPUBs to the Kindle over the Send to Kindle protocol, with its own small client in the `kindle` module (ported from `stkclient`, MIT), built on paperboy's httpx client.

- `paperboy kindle-login` registers paperboy as a Send to Kindle "device" with a random serial and stores the device credential in the Keychain.
- Each EPUB is sent once, to all Kindles of the account, recorded in the state store per target (Boox, Kindle) with its size.
- The post title is sent as the title and the publication as the author, so sorting by author groups the library by publication. The EPUB stays unchanged.
- `run` sends at its end. The Kindle pulls from Amazon's cloud, so it doesn't have to be awake.
- A rejected credential notifies once and asks for `kindle-login`; files wait on the Mac.

## Consequences

- No SMTP account, app password or approved-sender list; setup is one browser login.
- The protocol is undocumented and Amazon can change it (it moved to `kd.amazon.com` in October 2026). The EPUBs stay on the Mac, so a broken send loses nothing, and the fake Send to Kindle server in the tests documents the expected behavior. Email is the fallback if the protocol breaks for good.
- A regenerated file with a different size is sent again and shows up as a second copy on the Kindle.
- Retention (ticket 10) can't delete issues on the Kindle; old documents are removed by hand.
- Amazon's own client ID and device type are reused, as `stkclient` and Papir do.
