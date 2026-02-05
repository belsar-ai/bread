import os
import sys
import subprocess
from bread import lib

MOUNT_UNIT = "/etc/systemd/system/mnt-_bread.mount"


def delete_subvolumes_in(directory):
    """Delete all btrfs subvolumes inside a directory."""
    if not os.path.exists(directory):
        return
    for item in os.listdir(directory):
        path = os.path.join(directory, item)
        if lib.is_btrfs_subvolume(path):
            lib.run_cmd(["btrfs", "subvolume", "delete", path])
            print(f"  Deleted {path}")


def main():
    if os.geteuid() != 0:
        sys.exit("Root required.")

    print("This will remove ALL bread snapshots, config, and systemd units.")
    if input("Continue? (y/N): ").lower() != "y":
        sys.exit(0)

    # Disable timer
    subprocess.run(
        ["systemctl", "disable", "--now", "bread-snapshot.timer"], check=False
    )
    print("Timer disabled.")

    # Delete all snapshots and kernel markers
    delete_subvolumes_in(lib.SNAP_DIR)
    if os.path.exists(lib.SNAP_DIR):
        # Remove kernel marker files
        for f in os.listdir(lib.SNAP_DIR):
            if f.startswith(".kernel."):
                os.remove(os.path.join(lib.SNAP_DIR, f))
        os.rmdir(lib.SNAP_DIR)

    # Remove bread-restored kernel from /boot and clean up backups
    lib._clean_previous_restore()
    import shutil

    if os.path.exists(lib.BOOT_BACKUP_DIR):
        shutil.rmtree(lib.BOOT_BACKUP_DIR)
    if os.path.exists(lib.BOOT_RESTORED_MARKER):
        os.remove(lib.BOOT_RESTORED_MARKER)
    print("Kernel backups removed.")

    # Delete undo buffer
    delete_subvolumes_in(lib.OLD_DIR)
    if os.path.exists(lib.OLD_DIR):
        os.rmdir(lib.OLD_DIR)

    # Unmount and remove mount unit
    subprocess.run(["systemctl", "disable", "--now", "mnt-_bread.mount"], check=False)
    if os.path.exists(MOUNT_UNIT):
        os.remove(MOUNT_UNIT)
        subprocess.run(["systemctl", "daemon-reload"], check=False)
    if os.path.exists(lib.MOUNT_POINT):
        os.rmdir(lib.MOUNT_POINT)
    print("Mount removed.")

    # Remove config
    if os.path.exists(lib.CONFIG_FILE):
        os.remove(lib.CONFIG_FILE)
    print("Config removed.")

    print("\nPurge complete. Run 'dnf remove bread' to finish uninstall.")
