import os
import sys
import subprocess
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


def select_subvolumes(available):
    """Prompt for subvolume selection. Enter = All. Supports comma-separated."""
    print("\nRoll back which subvolumes?")
    options = ["All (Recommended)"] + available
    for i, opt in enumerate(options, 1):
        print(f"  {i}) {opt}")

    while True:
        choice = input("\nSelect [1]: ").strip()
        if not choice or choice == "1":
            return list(available)
        try:
            indices = [int(x.strip()) for x in choice.split(",")]
            selected = []
            for idx in indices:
                if 2 <= idx <= len(options):
                    selected.append(options[idx - 1])
                else:
                    raise ValueError
            if selected:
                return selected
        except ValueError:
            pass
        print("  Invalid selection.")


def command_loop(table):
    """fdisk-style command loop. Returns plan dict {subvol: ts_str} or None."""
    print("\nBread Rollback")
    print("\u2500" * 50)
    print_table(table)

    while True:
        try:
            cmd = input("\nCommand (m for help): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        if not cmd:
            continue
        elif cmd == "q":
            return None
        elif cmd == "m":
            print("\n  Commands:")
            print("  #     Select snapshot by number")
            print("  l     List all snapshots")
            print("  m     Show this help")
            print("  q     Quit")
        elif cmd == "l":
            print_table(table, show_all=True)
        else:
            try:
                num = int(cmd)
                if 1 <= num <= len(table):
                    ts_str, subvols = table[num - 1]
                    print(f"\nSelected: {lib.format_ts(ts_str)}")
                    selected = select_subvolumes(subvols)

                    print("\nRollback Plan:")
                    for sub in selected:
                        print(f"  {sub}  \u2192  {lib.format_ts(ts_str)}")

                    confirm = input("\nConfirm? (y/N): ").strip().lower()
                    if confirm == "y":
                        return {sub: ts_str for sub in selected}
                    print("Cancelled.")
                else:
                    print(f"  Invalid number. Range: 1-{len(table)}")
            except ValueError:
                print("  Unknown command. Type 'm' for help.")


def execute_rollback(plan, ts_str):
    """Execute the rollback plan."""
    # Restore kernel to /boot if needed
    ver = lib.snapshot_kernel_version(ts_str)
    if ver:
        if not lib.restore_kernel(ver):
            sys.exit(f"Error: kernel {ver} backup not found. Cannot safely roll back.")
    else:
        print(
            "  Warning: no kernel marker for this snapshot (pre-boot-backup snapshot)"
        )

    # Clear undo buffer
    if os.path.exists(lib.OLD_DIR):
        for item in os.listdir(lib.OLD_DIR):
            lib.run_cmd(
                ["btrfs", "subvolume", "delete", os.path.join(lib.OLD_DIR, item)]
            )
    else:
        os.makedirs(lib.OLD_DIR)

    # For each subvolume: move live to old, snapshot restore to live
    for sub, ts in plan.items():
        live = os.path.join(lib.MOUNT_POINT, sub)
        old = os.path.join(lib.OLD_DIR, sub)
        snap = os.path.join(lib.SNAP_DIR, f"{sub}.{ts}")

        os.rename(live, old)
        lib.run_cmd(["btrfs", "subvolume", "snapshot", snap, live])
        print(f"  {sub} -> {ts}")

    subprocess.run(["sync"])
    print("\nDone. Previous state is in old/. Reboot to apply.")


def main():
    import argparse

    parser = argparse.ArgumentParser(prog="bread rollback")
    parser.add_argument("--snapshot", type=int, help="Snapshot number (1-indexed)")
    parser.add_argument(
        "--subvols", type=str, help="Comma-separated subvolume names (default: all)"
    )
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    if os.geteuid() != 0:
        sys.exit("Root required.")
    lib.init()
    lib.check_fstab_safety(interactive=not args.yes)

    table = lib.build_snapshot_table()

    # Non-interactive mode (--snapshot provided) — used by GUI
    if args.snapshot is not None:
        if args.snapshot < 1 or args.snapshot > len(table):
            sys.exit(f"Invalid snapshot number. Range: 1-{len(table)}")
        ts_str, available = table[args.snapshot - 1]
        if args.subvols:
            selected = [s.strip() for s in args.subvols.split(",")]
            for s in selected:
                if s not in available:
                    sys.exit(f"Subvolume '{s}' not in snapshot {args.snapshot}")
        else:
            selected = available
        plan = {sub: ts_str for sub in selected}
        if not args.yes:
            print("Rollback Plan:")
            for sub in selected:
                print(f"  {sub}  \u2192  {lib.format_ts(ts_str)}")
            if input("\nConfirm? (y/N): ").strip().lower() != "y":
                sys.exit("Cancelled.")
    else:
        # Interactive mode
        plan = command_loop(table)
        if not plan:
            sys.exit("Cancelled.")
        ts_str = next(iter(plan.values()))  # All entries share same timestamp

    execute_rollback(plan, ts_str)
