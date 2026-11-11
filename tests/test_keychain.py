"""The Keychain store talks to `/usr/bin/security`, trusts only that tool and asks once per secret."""

import subprocess

import pytest

from paperboy import macos
from paperboy.macos import KeychainError, KeychainStore


class FakeSecurity:
    """Stands in for `/usr/bin/security`: an in-memory keychain plus a log of the calls."""

    def __init__(self) -> None:
        self.items: dict[str, str] = {}
        self.calls: list[list[str]] = []
        self.fail_with: tuple[int, str] | None = None

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        assert command[0] == "/usr/bin/security"
        verb, args = command[1], command[2:]
        name = args[args.index("-a") + 1]
        if self.fail_with and verb != "delete-generic-password":
            code, message = self.fail_with
            return subprocess.CompletedProcess(command, code, "", message)
        if verb == "find-generic-password":
            if name not in self.items:
                return subprocess.CompletedProcess(command, 44, "", "could not be found")
            return subprocess.CompletedProcess(command, 0, self.items[name] + "\n", "")
        if verb == "add-generic-password":
            self.items[name] = args[args.index("-w") + 1]
            return subprocess.CompletedProcess(command, 0, "", "")
        if verb == "delete-generic-password":
            found = self.items.pop(name, None) is not None
            return subprocess.CompletedProcess(command, 0 if found else 44, "", "")
        raise AssertionError(command)

    def verbs(self) -> list[str]:
        return [call[1] for call in self.calls]


@pytest.fixture
def security(monkeypatch):
    fake = FakeSecurity()
    monkeypatch.setattr(macos.subprocess, "run", fake)
    return fake


def test_repeated_reads_hit_the_keychain_once(security):
    security.items["cookie"] = "secret"
    store = KeychainStore()

    assert [store.get("cookie") for _ in range(4)] == ["secret"] * 4
    assert security.verbs() == ["find-generic-password"]


def test_a_missing_secret_is_asked_for_once_too(security):
    store = KeychainStore()

    assert store.get("cookie") is None
    assert store.get("cookie") is None
    assert security.verbs() == ["find-generic-password"]


def test_secrets_are_cached_separately(security):
    security.items.update({"a": "1", "b": "2"})
    store = KeychainStore()

    assert (store.get("a"), store.get("b"), store.get("a")) == ("1", "2", "1")
    assert security.verbs() == ["find-generic-password"] * 2


def test_set_stores_in_the_keychain_and_updates_the_cache(security):
    store = KeychainStore()
    assert store.get("cookie") is None

    store.set("cookie", "new")

    assert store.get("cookie") == "new"
    assert security.items["cookie"] == "new"
    assert security.verbs().count("find-generic-password") == 1


def test_set_replaces_an_existing_item_instead_of_updating_it(security):
    security.items["cookie"] = "old"

    KeychainStore().set("cookie", "new")

    assert security.verbs() == ["delete-generic-password", "add-generic-password"]
    assert security.items["cookie"] == "new"


def test_items_trust_only_the_security_tool(security):
    KeychainStore().set("cookie", "x")

    add = security.calls[-1]
    assert add[add.index("-T") + 1] == "/usr/bin/security"
    assert "-A" not in add


def test_values_with_special_characters_survive(security):
    store = KeychainStore()
    store.set("cookie", "s%3A abc=+/; \"q\"")

    assert KeychainStore().get("cookie") == "s%3A abc=+/; \"q\""


def test_a_failing_read_is_an_error_not_a_missing_secret(security):
    security.fail_with = (36, "User interaction is not allowed.")

    with pytest.raises(KeychainError, match="User interaction is not allowed"):
        KeychainStore().get("cookie")


def test_a_failing_write_is_an_error_and_does_not_fill_the_cache(security):
    store = KeychainStore()
    security.fail_with = (1, "denied")

    with pytest.raises(KeychainError, match="denied"):
        store.set("cookie", "x")

    security.fail_with = None
    assert store.get("cookie") is None
