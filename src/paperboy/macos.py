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
    """Stores secrets in the macOS login Keychain."""

    def get(self, name: str) -> str | None:
        return keyring.get_password(_SERVICE, name)

    def set(self, name: str, value: str) -> None:
        keyring.set_password(_SERVICE, name, value)


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
