"""Turn Substack posts into EPUBs for e-ink readers."""

import argparse
import getpass
import logging
import sys
from pathlib import Path

import httpx

from paperboy.config import Config, ConfigError, load_config
from paperboy.macos import KeychainStore, MacNotifier, Notifier, SecretStore
from paperboy import schedule
from paperboy.pipeline import Deps, Pipeline
from paperboy.state import AlreadyRunning, StateStore
from paperboy.substack import COOKIE_NAME, AuthExpired, SourceError, normalize_cookie

_USER_AGENT = "paperboy/0.1 (personal e-reader sync)"


def main(
    argv: list[str] | None = None,
    transport: httpx.BaseTransport | None = None,
    secrets: SecretStore | None = None,
    notifier: Notifier | None = None,
) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # it logs every request at INFO
    if args.command == "schedule":
        return _schedule(args.action, args.config)
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
            deps = Deps(
                http=http, output_dir=output_dir, state=state,
                secrets=secrets or KeychainStore(), notifier=notifier or MacNotifier(),
            )
            pipeline = Pipeline(deps)
            if args.command == "set-cookie":
                return _set_cookie(pipeline, deps.secrets)
            return _run(pipeline, config) if args.command == "run" else _convert(pipeline, args.url)
    finally:
        state.close()


def _convert(pipeline: Pipeline, url: str) -> int:
    try:
        path = pipeline.convert(url)
    except (SourceError, AuthExpired) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(path)
    return 0


def _run(pipeline: Pipeline, config: Config) -> int:
    logging.info("Run started for %d publications", len(config.publications))
    try:
        report = pipeline.run(config.publications)
    except AlreadyRunning:
        print("Another run is in progress, nothing to do.")
        return 0
    for path in report.converted:
        print(path)
    print(
        f"{len(report.converted)} converted, {report.skipped} skipped, "
        f"{report.pending_auth} waiting for login, {len(report.errors)} errors"
    )
    if report.pending_auth:
        print("Paid posts need a valid Substack cookie: run 'paperboy set-cookie'.")
    return 1 if report.errors else 0


def _set_cookie(pipeline: Pipeline, secrets: SecretStore) -> int:
    # Read from a hidden prompt or stdin, never from argv, so the cookie stays out of shell history.
    raw = getpass.getpass(f"Paste the {COOKIE_NAME} cookie: ") if sys.stdin.isatty() else sys.stdin.readline()
    if not raw.strip() and not sys.stdin.isatty():
        print(
            "error: no cookie received on stdin. Run this in a terminal for a hidden prompt, "
            "or copy the cookie and pipe it in: pbpaste | paperboy set-cookie",
            file=sys.stderr,
        )
        return 2
    try:
        cookie = normalize_cookie(raw)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    secrets.set(COOKIE_NAME, cookie)
    pipeline.cookie_updated()
    print("Cookie stored in the Keychain. Waiting paid posts are converted on the next run.")
    return 0


def _schedule(action: str, config: Path) -> int:
    if action == "show":
        sys.stdout.write(schedule.render_plist(config).decode())
        return 0
    if action == "uninstall":
        removed = schedule.uninstall()
        print("Hourly run removed." if removed else "No hourly run was installed.")
        return 0
    try:
        if not load_config(config).publications:  # fail now, not silently every hour
            raise ConfigError(f"no [[publications]] configured in {config}")
        plist = schedule.install(config)
    except (ConfigError, schedule.ScheduleError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"Hourly run installed ({plist}); it also runs right now and after the Mac wakes.")
    print(f"Log: {schedule.LOG_FILE}")
    return 0


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=Path("config.toml"), help="config file (default: ./config.toml)")
    parser = argparse.ArgumentParser(prog="paperboy", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", parents=[common], help="convert all new posts of the configured publications")
    commands.add_parser("set-cookie", parents=[common], help="store the Substack session cookie in the Keychain")
    scheduling = commands.add_parser("schedule", parents=[common], help="run every hour via launchd")
    scheduling.add_argument("action", choices=["install", "uninstall", "show"])
    convert = commands.add_parser("convert", parents=[common], help="convert a single post URL into an EPUB")
    convert.add_argument("url", help="post URL, e.g. https://example.substack.com/p/some-post")
    convert.add_argument("--output", type=Path, help="output folder, overrides output_dir from the config")
    return parser


if __name__ == "__main__":
    sys.exit(main())
