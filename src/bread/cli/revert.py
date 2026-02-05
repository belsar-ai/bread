import os
import sys
import subprocess
from bread import lib


def main():
    import argparse

    parser = argparse.ArgumentParser(prog="bread revert")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    if os.geteuid() != 0:
        sys.exit("Root required.")
    lib.init()
    lib.check_fstab_safety(interactive=not args.yes)

    if not os.path.exists(lib.OLD_DIR):
        sys.exit("No undo buffer (old/) found.")

    to_revert = [
        i
        for i in os.listdir(lib.OLD_DIR)
        if lib.is_btrfs_subvolume(os.path.join(lib.OLD_DIR, i))
    ]

    if not to_revert:
        sys.exit("old/ is empty.")

    if not args.yes:
        print(f"Undo Targets: {', '.join(to_revert)}")
        if input("Confirm? (y/N): ").lower() != "y":
            sys.exit(0)

    for sub in to_revert:
        live = os.path.join(lib.MOUNT_POINT, sub)
        old = os.path.join(lib.OLD_DIR, sub)
        temp = os.path.join(lib.MOUNT_POINT, f"{sub}_revert_tmp")

        os.rename(live, temp)
        os.rename(old, live)
        os.rename(temp, old)
        print(f"  Reverted {sub}")

    subprocess.run(["sync"])
    print("Done. Reboot to apply.")
