import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_BACKFILL = 3
DEFAULT_STATE_FILE = "~/Library/Application Support/paperboy/state.db"
DEFAULT_BOOX_FOLDER = "Books/Newsletters"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Publication:
    host: str
    backfill: int


@dataclass(frozen=True)
class Boox:
    url: str  # BOOX Drop base URL, e.g. http://192.168.1.50:8085
    folder: str  # library folder relative to the Boox's internal storage


@dataclass(frozen=True)
class Config:
    output_dir: Path
    state_file: Path
    publications: list[Publication]
    boox: Boox | None = None
    kindle: bool = False  # send to the account's Kindles via Send to Kindle


def load_config(path: Path) -> Config:
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path} (copy config.example.toml to get started)")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in {path}: {e}") from e
    return Config(
        output_dir=_path(data, "output_dir", None, path),
        state_file=_path(data, "state_file", DEFAULT_STATE_FILE, path),
        publications=_publications(data, _backfill(data.get("backfill", DEFAULT_BACKFILL), path, "backfill"), path),
        boox=_boox(data, path),
        kindle=_kindle(data, path),
    )


def _kindle(data: dict, path: Path) -> bool:
    if "kindle" not in data:
        return False
    if not isinstance(data["kindle"], dict):
        raise ConfigError(f"{path}: 'kindle' must be a [kindle] section")
    return True


def _boox(data: dict, path: Path) -> Boox | None:
    if "boox" not in data:
        return None
    section = data["boox"]
    url = section.get("url") if isinstance(section, dict) else None
    parsed = urlparse(url) if isinstance(url, str) else None
    if not parsed or parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConfigError(f'{path}: [boox] needs a \'url\' like "http://192.168.1.50:8085" (shown in BOOX Drop)')
    folder = section.get("folder", DEFAULT_BOOX_FOLDER)
    if not isinstance(folder, str) or not folder.strip("/ "):
        raise ConfigError(f"{path}: [boox] 'folder' must be a non-empty path like \"{DEFAULT_BOOX_FOLDER}\"")
    return Boox(url=url.rstrip("/"), folder=folder.strip("/ "))


def _path(data: dict, key: str, default: str | None, path: Path) -> Path:
    value = data.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{path}: '{key}' must be a non-empty string")
    return Path(value).expanduser()


def _backfill(value: object, path: Path, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ConfigError(f"{path}: '{where}' must be a whole number >= 0")
    return value


def _publications(data: dict, default_backfill: int, path: Path) -> list[Publication]:
    entries = data.get("publications", [])
    if not isinstance(entries, list):
        raise ConfigError(f"{path}: 'publications' must be a list of [[publications]] tables")
    publications: dict[str, Publication] = {}
    for number, entry in enumerate(entries, start=1):
        where = f"publication #{number}"
        url = entry.get("url") if isinstance(entry, dict) else None
        parsed = urlparse(url if isinstance(url, str) and "://" in url else f"https://{url}")
        if not isinstance(url, str) or not parsed.hostname:
            raise ConfigError(f"{path}: {where} needs a 'url' like \"https://example.substack.com\"")
        if parsed.hostname in publications:
            raise ConfigError(f"{path}: {where} duplicates {parsed.hostname}")
        backfill = _backfill(entry.get("backfill", default_backfill), path, f"{where}.backfill")
        publications[parsed.hostname] = Publication(host=parsed.hostname, backfill=backfill)
    return list(publications.values())
