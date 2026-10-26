# Lessons: Adding a Delivery Target

Learned while adding the Boox via BOOX Drop (ticket 13, ADR 0001) and the Kindle via Send to Kindle (tickets 17 and 18, ADR 0002).

## Probe the Real Device or Service First

Run a short spike against the real thing before designing around it. With BOOX Drop, the undocumented details decided the code: the JSON `args` parameter, `code -1` for an existing folder, and the server coming back after the device wakes. With Send to Kindle, a few test sends answered the design questions. Make each test send answer one question. A marker in the title passed through the API (`[TPE] …`) proved that the Kindle shows the title from the send call, not the EPUB's own. That settled "publication as author" without changing the EPUB.

When something looks wrong on the device (e.g. a missing cover), check the file first. It once was an EPUB generated before the cover existed.

## Read an Unofficial Client's Code Before Trusting It

`stkclient` registers every install with the same hardcoded device serial, so any other user's login invalidates ours. The symptom showed up hours later as `403 Failed to validate DeviceInfoToken`. Its issue tracker had the explanation in a closed PR. Before depending on a reverse-engineered client, read its code for shared identifiers, credentials and HTTP handling. Also search its issues for the error you see.

Port the few calls you need onto paperboy's httpx client instead of adding the library. That keeps every request behind `Deps` and testable with the fake transport. Check the port against the real API with a harmless call (e.g. listing devices) before building on it.

## Deliver Once, via State

"Upload whatever is missing" would re-deliver issues the user deleted on the device. Every target records what it received in the state store, keyed by file and size. Amazon doesn't deduplicate either: a second send creates a second document.

## Prepare the State Before a Target Goes Live

The installed launchd jobs run this working tree with the real `config.toml`. A new section in the config is live at the next 5-minute push. When the Kindle was enabled, the push job sent all 12 existing EPUBs before the backlog could be marked as sent. Mark the backlog first, or uninstall the schedule while you prepare.

## Make the Fake Strict

The fake Send to Kindle server checks request signatures against the keys it issued. That way a revoked login produces the real 403 in tests. Prove the fake can fail: a test with a tampered signature must get the 403.
