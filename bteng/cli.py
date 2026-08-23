"""CLI entry point: bteng <command> [options]."""

from __future__ import annotations

import argparse
import sys
import time
from importlib.metadata import PackageNotFoundError, version


def _get_version() -> str:
    try:
        return version("bteng")
    except PackageNotFoundError:
        from bteng import __version__
        return __version__


_LICENSE_DOC = "THIRD_PARTY_LICENSES.md"


def cmd_licenses(args: argparse.Namespace) -> int:
    """Print a bundled license document, or show where it lives on disk."""
    from importlib.resources import files

    try:
        resource = files("bteng").joinpath("licenses", _LICENSE_DOC)
    except (ModuleNotFoundError, FileNotFoundError) as exc:
        print(f"[bteng] License documents not found: {exc}", file=sys.stderr)
        return 1

    if args.path:
        print(resource)
        return 0

    try:
        print(resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError) as exc:
        print(f"[bteng] Could not read license document: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from bteng.blackboard.blackboard import Blackboard
    from bteng.core.engine import BehaviorTreeEngine
    from bteng.factory.factory import NodeFactory
    from bteng.logging.tracer import ExecutionTracer
    from bteng.plugins.loader import load_plugin_file

    for plugin in args.plugin or []:
        load_plugin_file(plugin)

    tracer = ExecutionTracer() if args.log or args.verbose else None
    bb = Blackboard.create("cli")

    try:
        engine = BehaviorTreeEngine.from_xml(
            args.xml_file,
            tree_id=args.tree,
            blackboard=bb,
            tracer=tracer,
            hz=args.hz,
        )
    except Exception as exc:
        print(f"[bteng] Error loading tree: {exc}", file=sys.stderr)
        return 1

    print(f"[bteng] Running tree from {args.xml_file!r}")
    try:
        status = engine.run_until_complete(max_ticks=args.max_ticks)
    except KeyboardInterrupt:
        engine.halt()
        status = None
        print("\n[bteng] Halted by user.")

    print(f"[bteng] Final status: {status}")

    if tracer:
        if args.verbose:
            tracer.print_summary()
        if args.log:
            tracer.save(args.log)
            print(f"[bteng] Execution log saved to {args.log!r}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="bteng",
        description="BTEng Behavior Tree Engine",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_get_version()}",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    # -- run -----------------------------------------------------------
    run_p = sub.add_parser("run", help="Run a behavior tree from XML")
    run_p.add_argument("xml_file", help="Path to XML tree definition")
    run_p.add_argument("--tree", default=None, help="Tree ID to run")
    run_p.add_argument("--hz", type=float, default=None, help="Tick frequency (Hz)")
    run_p.add_argument("--max-ticks", type=int, dest="max_ticks", default=None)
    run_p.add_argument("--log", default=None, help="Save execution log (JSON)")
    run_p.add_argument("--plugin", action="append", default=[], metavar="FILE")
    run_p.add_argument("-v", "--verbose", action="store_true")

    # -- licenses ------------------------------------------------------
    lic_p = sub.add_parser(
        "licenses",
        help="Print BTEng's third-party license audit",
    )
    lic_p.add_argument(
        "--path",
        action="store_true",
        help="Print the file location instead of its contents",
    )

    args = parser.parse_args()

    if args.command == "run":
        sys.exit(cmd_run(args))
    elif args.command == "licenses":
        sys.exit(cmd_licenses(args))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
