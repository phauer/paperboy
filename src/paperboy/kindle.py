"""Sends documents to Kindles over Amazon's Send to Kindle protocol.

The protocol is the one of Amazon's Send to Kindle desktop app; it is undocumented.
Ported from stkclient (https://github.com/maxdjohnson/stkclient, MIT), with a random
device serial per login: stkclient's shared serial lets other users' logins invalidate
ours (see ADR 0002).
"""

import base64
import hashlib
import json
import secrets
import string
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from lxml import etree

CREDENTIAL_NAME = "kindle.device"

_CLIENT_ID = "658490dfb190e494030082836775981fa23be0c2425441860352ba0f55915b43002d"
_DEVICE_TYPE = "A1K6D1WRW0MALS"
_CLIENT_INFO = {"appName": "ShellExtension", "appVersion": "1.1.1.253", "os": "MacOSX_10.14.6_x64", "osArchitecture": "x64"}
_TOKEN_URL = "https://api.amazon.com/auth/token"
_REGISTER_URL = "https://firs-ta-g7g.amazon.com/FirsProxy/registerDeviceWithToken"
_API_URL = "https://kd.amazon.com"
_UPLOAD_TIMEOUT = 120
_HEADERS = {"Accept-Language": "en-US,*", "User-Agent": "Mozilla/5.0"}


class KindleError(Exception):
    """Amazon answered, but rejected the request."""


class KindleAuthError(KindleError):
    """The stored login is no longer valid: run 'paperboy kindle-login'."""


@dataclass(frozen=True)
class Login:
    """A started browser login: open `url`, then finish it with the URL Amazon redirects to."""

    url: str
    verifier: str


@dataclass(frozen=True)
class Device:
    serial: str
    name: str


def start_login() -> Login:
    verifier = _b64url(secrets.token_bytes(32))
    query = {
        "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.ns.oa2": "http://www.amazon.com/ap/ext/oauth/2",
        "openid.ns": "http://specs.openid.net/auth/2.0",
        "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
        "openid.oa2.client_id": f"device:{_CLIENT_ID}",
        "openid.mode": "checkid_setup",
        "openid.oa2.scope": "device_auth_access",
        "openid.oa2.response_type": "code",
        "openid.oa2.code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
        "openid.oa2.code_challenge_method": "S256",
        "openid.return_to": "https://www.amazon.com/gp/sendtokindle",
        "openid.ns.pape": "http://specs.openid.net/extensions/pape/1.0",
        "openid.pape.max_auth_age": "0",
        "accountStatusPolicy": "P1",
        "openid.assoc_handle": "amzn_device_na",
        "pageId": "amzn_device_common_dark",
        "disableLoginPrepopulate": "1",
    }
    return Login(url="https://www.amazon.com/ap/signin?" + urlencode(query), verifier=verifier)


def finish_login(http: httpx.Client, login: Login, redirect_url: str) -> str:
    """Register paperboy as a Send to Kindle device; returns the credential to store as a secret."""
    codes = parse_qs(urlparse(redirect_url.strip()).query).get("openid.oa2.authorization_code")
    if not codes:
        raise KindleError("That URL has no authorization code: copy the full address after signing in")
    response = http.post(_TOKEN_URL, headers={**_HEADERS, "x-amzn-identity-auth-domain": "api.amazon.com"}, json={
        "app_name": "Unknown",
        "client_domain": "DeviceLegacy",
        "client_id": _CLIENT_ID,
        "code_algorithm": "SHA-256",
        "code_verifier": login.verifier,
        "requested_token_type": "access_token",
        "source_token": codes[0],
        "source_token_type": "authorization_code",
    })
    _check(response, "Amazon login", signed=False)
    serial = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(32))
    response = http.post(_REGISTER_URL, headers={**_HEADERS, "Content-Type": "text/xml"}, content=(
        "<?xml version='1.0' encoding='UTF-8'?><request><parameters>"
        f"<deviceType>{_DEVICE_TYPE}</deviceType><deviceSerialNumber>{serial}</deviceSerialNumber>"
        f"<pid>D21NN3GG</pid><authToken>{_xml_escape(response.json()['access_token'])}</authToken>"
        "<authTokenType>AccessToken</authTokenType><softwareVersion>253</softwareVersion>"
        "<os_version>MacOSX_10.14.6_x64</os_version><device_model>paperboy</device_model>"
        "</parameters></request>"
    ))
    _check(response, "Device registration", signed=False)
    root = etree.fromstring(response.content, etree.XMLParser(resolve_entities=False, no_network=True))
    fields = {child.tag: child.text for child in root}
    if not fields.get("device_private_key") or not fields.get("adp_token"):
        raise KindleError("Device registration returned no credentials")
    return json.dumps({"private_key": fields["device_private_key"], "adp_token": fields["adp_token"], "serial": serial})


class SendToKindle:
    """The Send to Kindle API, signed with a stored device credential."""

    def __init__(self, http: httpx.Client, credential: str):
        self._http = http
        try:
            data = json.loads(credential)
            key = serialization.load_pem_private_key(data["private_key"].encode(), password=None)
            self._adp_token = data["adp_token"]
        except (ValueError, KeyError, TypeError) as e:
            raise KindleAuthError(f"The stored Kindle login is unreadable: {e}") from e
        if not isinstance(key, rsa.RSAPrivateKey):
            raise KindleAuthError("The stored Kindle login has no RSA key")
        self._key = key

    def devices(self) -> list[Device]:
        """The account's Kindles that can receive documents."""
        data = self._call("/import/kindle-doc/get-device-list", {})
        return [Device(d["deviceSerialNumber"], d["deviceName"]) for d in data.get("ownedDevices", [])]

    def send(self, data: bytes, *, title: str, author: str, serials: list[str]) -> str:
        """Upload an EPUB and deliver it to the given Kindles; returns Amazon's id for the document."""
        upload = self._call("/import/upload-url", {"fileSize": len(data)})
        response = self._http.put(upload["uploadUrl"], content=data, headers=_HEADERS, timeout=_UPLOAD_TIMEOUT)
        _check(response, "Send to Kindle upload", signed=False)
        sent = self._call("/import/kindle-doc/send-to-kindle", {
            "documentMetadata": {"author": author, "crc32": 0, "inputFormat": "epub", "title": title},
            "archive": True,
            "fileSize": len(data),
            "forceConvert": False,
            "sendTimeMillis": time.time_ns() // 1_000_000,
            "shouldExtractWebContent": False,
            "stkToken": upload["stkToken"],
            "targetDevices": serials,
        })
        return sent["sku"]

    def _call(self, path: str, body: dict) -> dict:
        content = json.dumps({"clientInfo": _CLIENT_INFO, **body})
        response = self._http.post(f"{_API_URL}{path}", content=content, headers={
            **_HEADERS,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-ADP-Request-Digest": self._digest("POST", path, content),
            "X-ADP-Authentication-Token": self._adp_token,
        })
        _check(response, f"Send to Kindle {path}")
        data = response.json()
        if data.get("statusCode", 0) != 0:
            raise KindleError(f"Send to Kindle {path} failed: status {data['statusCode']}")
        return data

    def _digest(self, method: str, path: str, body: str) -> str:
        date = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        digest = hashlib.sha256("\n".join([method, path, date, body, self._adp_token]).encode()).digest()
        # Amazon expects the raw digest signed with PKCS#1 v1.5, without the ASN.1 DigestInfo wrapper.
        signature = self._key.sign(digest, padding.PKCS1v15(), utils.NoDigestInfo())
        return f"{base64.b64encode(signature).decode()}:{date}"


def _check(response: httpx.Response, what: str, signed: bool = True) -> None:
    """Raise for a failed response; a rejected signature on a signed call means the login is gone."""
    if response.is_success:
        return
    try:
        body = response.json()
    except ValueError:
        body = None
    reason = (body.get("Message") or body.get("error_description")) if isinstance(body, dict) else None
    message = f"{what} failed: HTTP {response.status_code} {reason or response.text[:200]}".strip()
    raise (KindleAuthError if signed and response.status_code in (401, 403) else KindleError)(message)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
