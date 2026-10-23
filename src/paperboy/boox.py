"""Talks to the BOOX Drop web server built into Boox devices.

The API is undocumented; it was probed on a Go 7 (see ticket 13). The server only
answers while the device is awake and on Wi-Fi.
"""

import json
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

STORAGE_ROOT = "/storage/emulated/0"
_PING_TIMEOUT = 3
_UPLOAD_TIMEOUT = 120
_SENDER = "paperboy"


class BooxError(Exception):
    """The Boox answered, but rejected the request."""


@dataclass(frozen=True)
class Entry:
    name: str
    is_dir: bool
    size: int


class BooxDrop:
    """A BOOX Drop server. Transport errors (device gone to sleep) propagate as httpx.TransportError."""

    def __init__(self, http: httpx.Client, url: str):
        self._http = http
        self._url = url.rstrip("/")
        self._known_dirs: set[str] = {STORAGE_ROOT}

    def reachable(self) -> bool:
        try:
            response = self._http.get(f"{self._url}/api/ping", timeout=_PING_TIMEOUT)
        except httpx.TransportError:
            return False
        return response.is_success and response.text.strip() == "ok"

    def list_dir(self, path: str) -> list[Entry]:
        """Entries of a folder; a missing folder lists as empty."""
        entries: list[Entry] = []
        while True:
            args = {"dir": path, "refresh": True, "offset": len(entries)}
            data = self._call("GET", "/api/storage", params={"args": json.dumps(args)})["data"]
            page = [Entry(e["name"], bool(e["dir"]), int(e.get("size", 0))) for e in data.get("list", [])]
            entries += page
            if not page or len(entries) >= data.get("count", 0):
                return entries

    def ensure_dir(self, path: str) -> None:
        """Create the folder and its missing parents below the storage root."""
        if path in self._known_dirs:
            return
        parent, name = path.rsplit("/", 1)
        self.ensure_dir(parent)
        if not any(e.is_dir and e.name == name for e in self.list_dir(parent)):
            self._call("POST", "/api/storage/directory", json={"parent": parent, "name": name})
        self._known_dirs.add(path)

    def upload(self, folder: str, name: str, data: bytes) -> None:
        """Write a file into an existing folder, replacing a file of the same name."""
        self._call(
            "POST", "/api/storage/upload", timeout=_UPLOAD_TIMEOUT,
            data={"dir": folder, "sender": _SENDER},
            files={"file": (name, data, "application/epub+zip")},
        )

    def _call(self, method: str, path: str, **kwargs) -> dict:
        response = self._http.request(method, f"{self._url}{path}", **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not response.is_success or not body.get("successful"):
            reason = body.get("message") or f"HTTP {response.status_code}"
            raise BooxError(f"BOOX Drop {path} failed: {reason}")
        return body
