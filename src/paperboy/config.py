import tomllib
from dataclasses import dataclass
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    output_dir: Path


def load_config(path: Path) -> Config:
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path} (copy config.example.toml to get started)")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"Invalid TOML in {path}: {e}") from e
    output_dir = data.get("output_dir")
    if not isinstance(output_dir, str) or not output_dir:
        raise ConfigError(f"{path}: 'output_dir' must be a non-empty string")
    return Config(output_dir=Path(output_dir).expanduser())
