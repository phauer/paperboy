"""Secrets and notifications: the two macOS integrations, behind small interfaces."""

import logging
import subprocess
from typing import Protocol

log = logging.getLogger(__name__)

_SERVICE = "paperboy"


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...


class KeychainError(Exception):
    pass


_SECURITY = "/usr/bin/security"
_NOT_FOUND = 44


class KeychainStore:
    """Stores secrets in the macOS login Keychain through the `security` tool.

    The items trust only `/usr/bin/security`, a binary whose path never changes. Reading them through Python's
    Keychain API would trust the interpreter instead, and every Homebrew upgrade of it raises a new password prompt.
    Reads are cached per process, because the pipeline asks for the cookie once per post.
    """

    def __init__(self) -> None:
        self._cache: dict[str, str | None] = {}

    def get(self, name: str) -> str | None:
        if name not in self._cache:
            result = self._security("find-generic-password", "-s", _SERVICE, "-a", name, "-w")
            if result.returncode == _NOT_FOUND:
                self._cache[name] = None
            elif result.returncode != 0:
                raise KeychainError(f"Can't read '{name}' from the Keychain: {result.stderr.strip()}")
            else:
                self._cache[name] = result.stdout.rstrip("\n")
        return self._cache[name]

    def set(self, name: str, value: str) -> None:
        # Delete first: an existing item keeps the access list of whoever created it.
        self._security("delete-generic-password", "-s", _SERVICE, "-a", name)
        # The value travels as an argument and is briefly visible in `ps` to this user's processes.
        result = self._security("add-generic-password", "-s", _SERVICE, "-a", name, "-w", value, "-T", _SECURITY)
        if result.returncode != 0:
            raise KeychainError(f"Can't store '{name}' in the Keychain: {result.stderr.strip()}")
        self._cache[name] = value

    @staticmethod
    def _security(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([_SECURITY, *args], capture_output=True, text=True, timeout=30)


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
