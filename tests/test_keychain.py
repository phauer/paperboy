"""The Keychain store asks the Keychain once per secret, because each read can raise an access prompt."""

import keyring
import pytest
from keyring.backend import KeyringBackend

from paperboy.macos import KeychainStore


class CountingBackend(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}
        self.reads = 0

    def get_password(self, service, username):
        self.reads += 1
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        self.items.pop((service, username), None)


@pytest.fixture
def backend():
    previous = keyring.get_keyring()
    backend = CountingBackend()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


def test_repeated_reads_hit_the_keychain_once(backend):
    backend.items[("paperboy", "cookie")] = "secret"
    store = KeychainStore()

    assert [store.get("cookie") for _ in range(4)] == ["secret"] * 4
    assert backend.reads == 1


def test_a_missing_secret_is_asked_for_once_too(backend):
    store = KeychainStore()

    assert store.get("cookie") is None
    assert store.get("cookie") is None
    assert backend.reads == 1


def test_secrets_are_cached_separately(backend):
    backend.items[("paperboy", "a")] = "1"
    backend.items[("paperboy", "b")] = "2"
    store = KeychainStore()

    assert (store.get("a"), store.get("b"), store.get("a")) == ("1", "2", "1")
    assert backend.reads == 2


def test_set_stores_in_the_keychain_and_updates_the_cache(backend):
    store = KeychainStore()
    assert store.get("cookie") is None

    store.set("cookie", "new")

    assert store.get("cookie") == "new"
    assert backend.items[("paperboy", "cookie")] == "new"
    assert backend.reads == 1
