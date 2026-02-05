import sys
import argparse
import importlib

from bread import lib

COMMANDS = {
    "list": "List snapshots",
    "config": "Setup wizard",
    "snapshot": "Create snapshots and prune old ones",
    "rollback": "Interactive snapshot recovery",
    "revert": "Undo last rollback",
    "purge": "Remove all bread data and config",
}


def main():
    parser = argparse.ArgumentParser(prog="bread", description="Btrfs snapshot manager")
    parser.add_argument(
        "command",
        nargs="?",
        choices=COMMANDS.keys(),
        metavar="command",
        help="{%(choices)s}",
    )
    parser.add_argument("args", nargs=argparse.REMAINDER)

    args = parser.parse_args()

    if args.command is None:
        if lib.load_config() is None:
            args.command = "config"
        else:
            args.command = "list"

    sys.argv = [args.command] + args.args
    mod = importlib.import_module(f"bread.cli.{args.command}")
    mod.main()
