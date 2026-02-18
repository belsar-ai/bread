import os
import sys
import subprocess
from bread import lib


def select_subvolumes(available):
    """Prompt for subvolume selection. Enter = All. Supports comma-separated."""
    print("\nRoll back which subvolumes?")
    options = ["All (Recommended)"] + list(available)
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
    lib.clear_old_buffer()
    if not os.path.exists(lib.OLD_DIR):
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
    parser.add_argument("snapshot", type=int, help="Snapshot number (from bread list)")
    parser.add_argument(
        "--subvols", type=str, help="Comma-separated subvolume names (default: all)"
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    args = parser.parse_args()

    if os.geteuid() != 0:
        sys.exit("Root required.")
    lib.init()
    lib.check_fstab_safety(interactive=not args.yes)

    table = lib.build_snapshot_table()
    if args.snapshot < 1 or args.snapshot > len(table):
        sys.exit(f"Invalid snapshot number. Range: 1-{len(table)}")

    ts_str, available = table[args.snapshot - 1]

    if args.subvols:
        selected = [s.strip() for s in args.subvols.split(",")]
        for s in selected:
            if s not in available:
                sys.exit(f"Subvolume '{s}' not in snapshot {args.snapshot}")
    else:
        selected = select_subvolumes(available)

    plan = {sub: available[sub] for sub in selected}

    print("\nRollback Plan:")
    for sub in selected:
        date, time = lib.format_ts(available[sub])
        print(f"  {sub}  \u2192  {date} {time}")

    if not args.yes:
        if input("\nConfirm? (y/N): ").strip().lower() != "y":
            sys.exit("Cancelled.")

    execute_rollback(plan, ts_str)
