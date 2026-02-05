import os
import sys
import json
import subprocess
from bread import lib

MOUNT_UNIT = "/etc/systemd/system/mnt-_bread.mount"


def detect_boot_device():
    """Find the btrfs device backing /."""
    try:
        output = subprocess.check_output(
            ["findmnt", "-n", "-o", "SOURCE", "-t", "btrfs", "/"], text=True
        )
        return output.strip()
    except subprocess.CalledProcessError:
        sys.exit("Error: root filesystem is not btrfs.")


def ask_int(prompt):
    while True:
        val = input(f"{prompt}: ").strip()
        if not val:
            print("Value required.")
            continue
        if val.isdigit() and int(val) >= 0:
            return int(val)
        print("Non-negative integer required.")


def write_mount_unit(device):
    """Write systemd mount unit for btrfs top-level."""
    unit = f"""[Unit]
Description=Mount btrfs top-level for Bread

[Mount]
What={device}
Where={lib.MOUNT_POINT}
Type=btrfs
Options=subvolid=5

[Install]
WantedBy=local-fs.target
"""
    with open(MOUNT_UNIT, "w") as f:
        f.write(unit)
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "enable", "--now", "mnt-_bread.mount"], check=True)
    print(f"Mount enabled ({lib.MOUNT_POINT}).")


def main():
    import argparse

    parser = argparse.ArgumentParser(prog="bread config")
    parser.add_argument("--hourly", type=int, help="Hourly retention count")
    parser.add_argument("--daily", type=int, help="Daily retention count")
    parser.add_argument("--weekly", type=int, help="Weekly retention count")
    parser.add_argument("--monthly", type=int, help="Monthly retention count")
    args = parser.parse_args()

    if os.geteuid() != 0:
        sys.exit("Root required.")

    device = detect_boot_device()

    # Non-interactive mode (all flags provided) — used by GUI
    if all(v is not None for v in [args.hourly, args.daily, args.weekly, args.monthly]):
        conf = {
            "device": device,
            "retention": {
                "hourly": args.hourly,
                "daily": args.daily,
                "weekly": args.weekly,
                "monthly": args.monthly,
            },
        }
    else:
        # Interactive mode
        print("--- Bread Configuration ---\n")
        print(f"Detected boot device: {device}")
        print("\n[ Retention (number of snapshots to keep per period) ]")
        conf = {"device": device, "retention": {}}
        conf["retention"]["hourly"] = ask_int("Hourly")
        conf["retention"]["daily"] = ask_int("Daily")
        conf["retention"]["weekly"] = ask_int("Weekly")
        conf["retention"]["monthly"] = ask_int("Monthly")

    # Save config
    with open(lib.CONFIG_FILE, "w") as f:
        json.dump(conf, f, indent=4)
    os.chmod(lib.CONFIG_FILE, 0o644)
    print("Configuration saved.")

    # Create mount unit
    write_mount_unit(device)

    print("\nTo enable automatic hourly snapshots:")
    print("  systemctl enable --now bread-snapshot.timer")
