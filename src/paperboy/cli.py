import argparse
import sys
from pathlib import Path

import httpx

from paperboy.config import ConfigError, load_config
from paperboy.pipeline import Deps, Pipeline
from paperboy.substack import SourceError

_USER_AGENT = "paperboy/0.1 (personal e-reader sync)"


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        output_dir = args.output or load_config(args.config).output_dir
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    with httpx.Client(
        transport=transport, timeout=30, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
    ) as http:
        pipeline = Pipeline(Deps(http=http, output_dir=output_dir))
        try:
            path = pipeline.convert(args.url)
        except SourceError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    print(path)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperboy", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    convert = commands.add_parser("convert", help="convert a single post URL into an EPUB")
    convert.add_argument("url", help="post URL, e.g. https://example.substack.com/p/some-post")
    convert.add_argument("--config", type=Path, default=Path("config.toml"), help="config file (default: ./config.toml)")
    convert.add_argument("--output", type=Path, help="output folder, overrides output_dir from the config")
    return parser


if __name__ == "__main__":
    sys.exit(main())
