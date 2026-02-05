import os
import sys
import json
import subprocess
import re
from collections import defaultdict
from datetime import datetime

CONFIG_FILE = "/etc/bread.json"
MOUNT_POINT = "/mnt/_bread"
SNAP_DIR_NAME = "_bread_snapshots"
SNAP_DIR = os.path.join(MOUNT_POINT, SNAP_DIR_NAME)
OLD_DIR = os.path.join(MOUNT_POINT, "old")
BOOT_BACKUP_DIR = os.path.join(MOUNT_POINT, "_bread_boot")

CONF = None


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return None


def init():
    """Load config into CONF global. Called by commands that need config."""
    global CONF
    CONF = load_config()
    if CONF is None:
        sys.exit("No configuration found. Run 'bread config' first.")


def run_cmd(cmd, check=True):
    try:
        subprocess.run(cmd, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        print(f"Error: {e.stderr.decode().strip()}", file=sys.stderr)
        raise


def is_btrfs_subvolume(path):
    if not os.path.exists(path):
        return False
    ret = subprocess.call(
        ["btrfs", "subvolume", "show", path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return ret == 0


def check_fstab_safety(interactive=True):
    issues = []
    try:
        with open("/etc/fstab", "r") as f:
            for line in f:
                if (
                    "btrfs" in line
                    and "subvolid=" in line
                    and not line.strip().startswith("#")
                ):
                    issues.append(line.strip())
    except Exception:
        pass
    if issues:
        print("!!! DANGER: FSTAB USES SUBVOLID !!! (Boot will fail after rollback)")
        for i in issues:
            print(f"  {i}")
        if not interactive:
            sys.exit("Refusing to proceed non-interactively. Fix fstab first.")
        if input("Proceed anyway? (y/N): ").lower() != "y":
            sys.exit(1)


def btrfs_list():
    """Parse `btrfs subvolume list /` into [(path, top_level), ...]."""
    output = subprocess.check_output(["btrfs", "subvolume", "list", "/"], text=True)
    results = []
    for line in output.strip().splitlines():
        parts = line.split()
        # ID <id> gen <gen> top level <top> path <path>
        top_level = parts[6]
        path = parts[8]
        results.append((path, top_level))
    return results


def discover_subvolumes():
    """Find live subvolumes (top-level children, excluding bread internals)."""
    exclude = {SNAP_DIR_NAME, "_bread_boot", "old", "lost+found"}
    return sorted(
        [
            path
            for path, top in btrfs_list()
            if top == "5" and "/" not in path and path not in exclude
        ]
    )


def format_ts(ts_str):
    """Convert internal timestamp (YYYYMMDDTHHMMSS) to human-readable."""
    for fmt_in, fmt_out in [
        ("%Y%m%dT%H%M%S", "%Y-%m-%d %H:%M:%S"),
        ("%Y%m%dT%H%M", "%Y-%m-%d %H:%M"),
    ]:
        try:
            return datetime.strptime(ts_str, fmt_in).strftime(fmt_out)
        except ValueError:
            continue
    return ts_str


def build_snapshot_table():
    """Build snapshot table from snapshot directory listing.
    Returns [(ts_str, [subvols]), ...] sorted oldest-first.
    Position in list (1-indexed) = stable session ID.
    Uses os.listdir (no root required) so CLI and GUI share one code path."""
    if not os.path.exists(SNAP_DIR):
        return []
    timestamps = defaultdict(list)

    for fname in os.listdir(SNAP_DIR):
        if fname.startswith("."):
            continue
        m = re.match(r"^(.+)\.(\d{8}T(?:\d{6}|\d{4}))$", fname)
        if not m:
            continue
        subvol, ts_str = m.groups()
        for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
            try:
                datetime.strptime(ts_str, fmt)
                timestamps[ts_str].append(subvol)
                break
            except ValueError:
                continue

    return [(ts, sorted(subs)) for ts, subs in sorted(timestamps.items())]


def get_machine_id():
    """Read machine-id for BLS entry filenames."""
    with open("/etc/machine-id") as f:
        return f.read().strip()


def backup_kernel():
    """Backup current kernel to _bread_boot/ if not already present.
    Returns the kernel version string."""
    ver = os.uname().release
    dest = os.path.join(BOOT_BACKUP_DIR, ver)
    if os.path.exists(dest):
        return ver
    os.makedirs(dest, exist_ok=True)
    mid = get_machine_id()
    files = {
        f"/boot/vmlinuz-{ver}": "vmlinuz",
        f"/boot/initramfs-{ver}.img": "initramfs.img",
        f"/boot/loader/entries/{mid}-{ver}.conf": "bls.conf",
    }
    import shutil

    for src, name in files.items():
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, name))
    return ver


BOOT_RESTORED_MARKER = os.path.join(MOUNT_POINT, ".bread_restored_kernel")


def _remove_kernel_from_boot(ver):
    """Remove a kernel's files from /boot."""
    mid = get_machine_id()
    for path in [
        f"/boot/vmlinuz-{ver}",
        f"/boot/initramfs-{ver}.img",
        f"/boot/loader/entries/{mid}-{ver}.conf",
    ]:
        if os.path.exists(path):
            os.remove(path)


def _clean_previous_restore():
    """Remove the previously bread-restored kernel from /boot, if any."""
    if not os.path.exists(BOOT_RESTORED_MARKER):
        return
    with open(BOOT_RESTORED_MARKER) as f:
        prev_ver = f.read().strip()
    if prev_ver and os.path.exists(f"/boot/vmlinuz-{prev_ver}"):
        _remove_kernel_from_boot(prev_ver)
        print(f"  Removed previous bread kernel {prev_ver} from /boot")


def restore_kernel(ver):
    """Restore kernel from _bread_boot/ to /boot.
    Removes any previously bread-restored kernel first, then sets GRUB default."""
    src_dir = os.path.join(BOOT_BACKUP_DIR, ver)
    if not os.path.exists(src_dir):
        return False

    # Clean up previous bread-restored kernel (keep only one at a time)
    _clean_previous_restore()

    # Restore if not already in /boot (may already be there as a system kernel)
    if not os.path.exists(f"/boot/vmlinuz-{ver}"):
        import shutil

        mid = get_machine_id()
        restores = {
            "vmlinuz": f"/boot/vmlinuz-{ver}",
            "initramfs.img": f"/boot/initramfs-{ver}.img",
            "bls.conf": f"/boot/loader/entries/{mid}-{ver}.conf",
        }
        for name, dst in restores.items():
            src = os.path.join(src_dir, name)
            if os.path.exists(src):
                shutil.copy2(src, dst)
        print(f"  Restored kernel {ver} to /boot")

        # Track what we put in /boot
        with open(BOOT_RESTORED_MARKER, "w") as f:
            f.write(ver)
    else:
        # Kernel already in /boot (system-managed), clear marker
        if os.path.exists(BOOT_RESTORED_MARKER):
            os.remove(BOOT_RESTORED_MARKER)

    # Set as default boot entry
    subprocess.run(["grubby", "--set-default", f"/boot/vmlinuz-{ver}"], check=True)
    print(f"  Set default boot kernel: {ver}")
    return True


def snapshot_kernel_version(ts_str):
    """Read the kernel version marker for a snapshot timestamp."""
    marker = os.path.join(SNAP_DIR, f".kernel.{ts_str}")
    if os.path.exists(marker):
        with open(marker) as f:
            return f.read().strip()
    return None


def write_kernel_marker(ts_str, ver):
    """Write kernel version marker for a snapshot timestamp."""
    marker = os.path.join(SNAP_DIR, f".kernel.{ts_str}")
    with open(marker, "w") as f:
        f.write(ver)


def prune_kernel_backups():
    """Remove orphaned kernel markers and unreferenced kernel backups."""
    if not os.path.exists(SNAP_DIR):
        return
    # Find which timestamps still have snapshots
    live_timestamps = set()
    for fname in os.listdir(SNAP_DIR):
        m = re.match(r"^.+\.(\d{8}T(?:\d{6}|\d{4}))$", fname)
        if m:
            live_timestamps.add(m.group(1))
    # Remove orphaned kernel markers, collect referenced kernel versions
    referenced = set()
    for fname in os.listdir(SNAP_DIR):
        if not fname.startswith(".kernel."):
            continue
        ts = fname[len(".kernel.") :]
        path = os.path.join(SNAP_DIR, fname)
        if ts not in live_timestamps:
            os.remove(path)
        else:
            with open(path) as f:
                referenced.add(f.read().strip())
    # Remove unreferenced kernel backups
    if not os.path.exists(BOOT_BACKUP_DIR):
        return
    import shutil

    for ver in os.listdir(BOOT_BACKUP_DIR):
        if ver not in referenced:
            shutil.rmtree(os.path.join(BOOT_BACKUP_DIR, ver))
            print(f"  Removed kernel backup {ver}")
