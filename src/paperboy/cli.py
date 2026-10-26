"""Turn Substack posts into EPUBs for e-ink readers."""

import argparse
import getpass
import logging
import sys
from datetime import datetime
from pathlib import Path

import httpx

from paperboy.config import Config, ConfigError, load_config
from paperboy.kindle import KindleError, start_login
from paperboy.macos import KeychainStore, MacNotifier, Notifier, SecretStore
from paperboy import schedule
from paperboy.pipeline import Deps, Pipeline, PushReport, Status
from paperboy.state import AlreadyRunning, StateStore, StoredPost
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
    if args.command == "push" and not (config.boox or config.kindle):
        print(f"error: no [boox] or [kindle] section configured in {args.config}", file=sys.stderr)
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
            if args.command == "kindle-login":
                return _kindle_login(pipeline)
            if args.command == "push":
                return _push(pipeline, config, args.quiet)
            if args.command == "status":
                return _status(pipeline.status(config.publications, config.kindle))
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
        report = pipeline.run(config.publications, config.boox, config.kindle)
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
    pushes = [push for push in (report.push, report.kindle) if push]
    for push in pushes:
        _print_push(push)
    return 1 if report.errors or any(push.errors for push in pushes) else 0


def _push(pipeline: Pipeline, config: Config, quiet: bool) -> int:
    try:
        reports = pipeline.push(config.boox, config.kindle)
    except AlreadyRunning:
        return 0  # the running run pushes when it's done
    for report in reports:
        if not quiet or report.pushed or report.errors:
            _print_push(report)
    return 1 if any(report.errors for report in reports) else 0


def _print_push(report: PushReport) -> None:
    for path in report.pushed:
        print(f"{'pushed' if report.target == 'Boox' else 'sent to the Kindle'}: {path}")
    for error in report.errors:
        print(f"error: {error}")
    done = f"{len(report.pushed)} {'pushed to' if report.target == 'Boox' else 'sent to'} the {report.target}"
    if not report.reachable:
        why = "it's not reachable (asleep or off Wi-Fi)" if report.target == "Boox" else "Send to Kindle is not reachable"
        print(f"{done}; {why}, {report.waiting} files wait on the Mac.")
    elif report.pushed or report.errors:
        print(f"{done}, {len(report.errors)} errors")
    else:
        print(f"The {report.target} is up to date.")


def _status(status: Status) -> int:
    if status.last_run is None:
        print("Last run: none yet")
    else:
        errors = status.last_run.errors
        print(f"Last run: {_local(status.last_run.finished_at, '%Y-%m-%d %H:%M')}, "
              + (f"{len(errors)} error{'s' if len(errors) > 1 else ''}:" if errors else "no errors"))
        for error in errors:
            print(f"  - {error}")
    print(f"Substack cookie: {'stored' if status.cookie_stored else 'not stored'}")
    if status.kindle:
        login = "stored" if status.kindle.login_stored else "not stored (run 'paperboy kindle-login')"
        waiting = f", {status.kindle.waiting} issues not sent yet" if status.kindle.waiting else ", all issues sent"
        print(f"Kindle login: {login}{waiting}")
    for publication in status.publications:
        summary = f"{publication.done} delivered"
        if publication.backfill:
            summary += f", {publication.backfill} older posts skipped at first run"
        print(f"\n{publication.host}: {summary}")
        _print_posts("Waiting for login", publication.pending_auth)
        _print_posts("Failed", publication.failed)
        _print_posts("Skipped", publication.skipped)
    print()
    if status.all_good:
        print("All good: nothing is waiting or failed.")
    elif any(p.pending_auth for p in status.publications):
        print("Paid posts wait for a valid cookie (or a subscription): run 'paperboy set-cookie' with a fresh one.")
    return 0


def _print_posts(label: str, posts: list[StoredPost]) -> None:
    if posts:
        print(f"  {label} ({len(posts)}):")
    for post in posts:
        print(f"    {_local(post.updated_at, '%Y-%m-%d')}  {post.url}")
        if post.reason:
            print(f"                {post.reason.removesuffix(f': {post.url}')}")


def _local(at: datetime, pattern: str) -> str:
    return at.astimezone().strftime(pattern)


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


def _kindle_login(pipeline: Pipeline) -> int:
    login = start_login()
    print("1. Open this address in a browser and sign in with the Amazon account of your Kindle:\n")
    print(f"{login.url}\n")
    print("2. Amazon then shows an amazon.com page that may look empty or broken. Copy its full address.\n")
    if sys.stdin.isatty():
        import readline  # noqa: F401  # without it, input() hangs on lines over 1024 characters on macOS

        redirect_url = input("3. Paste the address here: ")
    else:
        redirect_url = sys.stdin.readline()
    try:
        devices = pipeline.kindle_login(login, redirect_url)
    except (KindleError, httpx.HTTPError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("Kindle login stored in the Keychain.")
    if devices:
        print(f"New issues go to: {', '.join(device.name for device in devices)}")
    else:
        print("warning: this Amazon account has no Kindle to send to yet.")
    return 0


def _schedule(action: str, config_path: Path) -> int:
    if action == "uninstall":
        removed = schedule.uninstall()
        print("Scheduled jobs removed." if removed else "No scheduled job was installed.")
        return 0
    try:
        config = load_config(config_path)
        if not config.publications:  # fail now, not silently every hour
            raise ConfigError(f"no [[publications]] configured in {config_path}")
        if action == "show":
            sys.stdout.write(schedule.render_plist(config_path).decode())
            if config.boox:
                sys.stdout.write(schedule.render_push_plist(config_path).decode())
            return 0
        plists = schedule.install(config_path, push=config.boox is not None)
    except (ConfigError, schedule.ScheduleError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"Hourly run installed ({plists[0]}); it also runs right now and after the Mac wakes.")
    if config.boox:
        print(f"Push to the Boox every {schedule.PUSH_INTERVAL // 60} minutes installed ({plists[1]}).")
    print(f"Log: {schedule.LOG_FILE}")
    return 0


def _parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=Path("config.toml"), help="config file (default: ./config.toml)")
    parser = argparse.ArgumentParser(prog="paperboy", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", parents=[common], help="convert all new posts of the configured publications")
    push = commands.add_parser("push", parents=[common], help="deliver EPUBs the Boox and Kindle haven't received yet")
    push.add_argument("--quiet", action="store_true", help="print only when something was pushed or failed")
    commands.add_parser("status", parents=[common], help="show waiting, failed and skipped posts and the last run")
    commands.add_parser("set-cookie", parents=[common], help="store the Substack session cookie in the Keychain")
    commands.add_parser("kindle-login", parents=[common], help="log in to Send to Kindle and store the login in the Keychain")
    scheduling = commands.add_parser("schedule", parents=[common], help="run every hour (and push every 5 minutes) via launchd")
    scheduling.add_argument("action", choices=["install", "uninstall", "show"])
    convert = commands.add_parser("convert", parents=[common], help="convert a single post URL into an EPUB")
    convert.add_argument("url", help="post URL, e.g. https://example.substack.com/p/some-post")
    convert.add_argument("--output", type=Path, help="output folder, overrides output_dir from the config")
    return parser


if __name__ == "__main__":
    sys.exit(main())
