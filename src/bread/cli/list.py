from bread import lib


def print_table(table, show_all=False):
    """Print snapshot table. Default: last 10. show_all: everything."""
    if not table:
        print("  No snapshots found.")
        return
    start = 0 if show_all else max(0, len(table) - 10)
    print(f"\n  {'#':>4}  {'Timestamp':<21}  Subvolumes")
    for i in range(start, len(table)):
        num = i + 1
        ts_str, subvols = table[i]
        print(f"  {num:>4}  {lib.format_ts(ts_str):<21}  {', '.join(subvols)}")


def main():
    import argparse

    parser = argparse.ArgumentParser(prog="bread list")
    parser.add_argument("--all", action="store_true", help="Show all snapshots")
    args = parser.parse_args()

    table = lib.build_snapshot_table()
    print_table(table, show_all=args.all)
