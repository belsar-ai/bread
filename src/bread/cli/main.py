import sys
import argparse
import importlib

COMMANDS = {
    "config": "Setup wizard",
    "snapshot": "Create snapshots and prune old ones",
    "rollback": "Interactive snapshot recovery",
    "revert": "Undo last rollback",
    "purge": "Remove all bread data and config",
}


def main():
    parser = argparse.ArgumentParser(prog="bread", description="Btrfs snapshot manager")
    parser.add_argument(
        "command", choices=COMMANDS.keys(), metavar="command", help="{%(choices)s}"
    )
    parser.add_argument("args", nargs=argparse.REMAINDER)

    if len(sys.argv) < 2:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()
    sys.argv = [args.command] + args.args
    mod = importlib.import_module(f"bread.cli.{args.command}")
    mod.main()
