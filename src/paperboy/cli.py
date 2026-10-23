"""Turn Substack posts into EPUBs for e-ink readers."""

import argparse
import logging
import sys
from pathlib import Path

import httpx

from paperboy.config import Config, ConfigError, load_config
from paperboy.pipeline import Deps, Pipeline
from paperboy.state import AlreadyRunning, StateStore
from paperboy.substack import SourceError

_USER_AGENT = "paperboy/0.1 (personal e-reader sync)"


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # it logs every request at INFO
    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.command == "run" and not config.publications:
        print(f"error: no [[publications]] configured in {args.config}", file=sys.stderr)
        return 2

    state = StateStore(config.state_file)
    try:
        with httpx.Client(
            transport=transport, timeout=30, follow_redirects=True, headers={"User-Agent": _USER_AGENT}
        ) as http:
            output_dir = getattr(args, "output", None) or config.output_dir
            pipeline = Pipeline(Deps(http=http, output_dir=output_dir, state=state))
            return _run(pipeline, config) if args.command == "run" else _convert(pipeline, args.url)
    finally:
        state.close()


def _convert(pipeline: Pipeline, url: str) -> int:
    try:
        path = pipeline.convert(url)
    except SourceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(path)
    return 0


def _run(pipeline: Pipeline, config: Config) -> int:
    try:
        report = pipeline.run(config.publications)
    except AlreadyRunning:
        print("Another run is in progress, nothing to do.")
        return 0
    for path in report.converted:
        print(path)
    print(f"{len(report.converted)} converted, {report.skipped} skipped, {len(report.errors)} errors")
    return 1 if report.errors else 0


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=Path("config.toml"), help="config file (default: ./config.toml)")
    parser = argparse.ArgumentParser(prog="paperboy", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", parents=[common], help="convert all new posts of the configured publications")
    convert = commands.add_parser("convert", parents=[common], help="convert a single post URL into an EPUB")
    convert.add_argument("url", help="post URL, e.g. https://example.substack.com/p/some-post")
    convert.add_argument("--output", type=Path, help="output folder, overrides output_dir from the config")
    return parser


if __name__ == "__main__":
    sys.exit(main())
