import os
import subprocess
import sys
from bread import lib


def format_table(table):
    """Format snapshot table as string."""
    if not table:
        return "  No snapshots found.\n"
    lines = [f"  {'#':>4}  {'Timestamp':<21}  Subvolumes"]
    for i, (ts_str, subvols) in enumerate(table):
        num = i + 1
        lines.append(f"  {num:>4}  {lib.format_ts(ts_str):<21}  {', '.join(subvols)}")
    return "\n".join(lines) + "\n"


def print_recent(table, count=10):
    """Print the most recent snapshots."""
    if not table:
        print("  No snapshots found.")
        return
    start = max(0, len(table) - count)
    recent = table[start:]
    print(f"  {'#':>4}  {'Timestamp':<21}  Subvolumes")
    for i, (ts_str, subvols) in enumerate(recent, start=start):
        num = i + 1
        print(f"  {num:>4}  {lib.format_ts(ts_str):<21}  {', '.join(subvols)}")


def main():
    table = lib.build_snapshot_table()
    output = format_table(table)

    pager = os.environ.get("PAGER", "less")
    if sys.stdout.isatty():
        try:
            proc = subprocess.Popen([pager], stdin=subprocess.PIPE)
            proc.communicate(input=output.encode())
        except (FileNotFoundError, BrokenPipeError):
            print(output, end="")
    else:
        print(output, end="")
