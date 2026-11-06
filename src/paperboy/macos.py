"""Secrets and notifications: the two macOS integrations, behind small interfaces."""

import logging
import subprocess
from typing import Protocol

import keyring

log = logging.getLogger(__name__)

_SERVICE = "paperboy"


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...


class KeychainStore:
    """Stores secrets in the macOS login Keychain.

    Reads are cached per process: every Keychain read can raise an access prompt, and the pipeline asks for the
    cookie once per post.
    """

    def __init__(self) -> None:
        self._cache: dict[str, str | None] = {}

    def get(self, name: str) -> str | None:
        if name not in self._cache:
            self._cache[name] = keyring.get_password(_SERVICE, name)
        return self._cache[name]

    def set(self, name: str, value: str) -> None:
        keyring.set_password(_SERVICE, name, value)
        self._cache[name] = value


class Notifier(Protocol):
    def notify(self, title: str, message: str) -> None: ...


class MacNotifier:
    def notify(self, title: str, message: str) -> None:
        # Pass texts as arguments, so quotes in them can't break the AppleScript.
        script = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run"]
        try:
            subprocess.run(["osascript", *script, title, message], check=True, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("Could not show notification '%s': %s", message, e)
