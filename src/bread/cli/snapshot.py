import os
import sys
import subprocess
import datetime
from bread import lib

STATS = {"created": 0, "pruned": 0, "errors": 0}


def create_snapshot(subvol_name, now):
    src = os.path.join(lib.MOUNT_POINT, subvol_name)
    dst = os.path.join(lib.SNAP_DIR, f"{subvol_name}.{now}")

    os.makedirs(lib.SNAP_DIR, exist_ok=True)

    if os.path.exists(dst):
        return

    print(f"[+] Snapshot {subvol_name} -> {os.path.basename(dst)}")
    try:
        lib.run_cmd(["btrfs", "subvolume", "snapshot", "-r", src, dst])
        STATS["created"] += 1
    except Exception:
        STATS["errors"] += 1


def get_snapshots(subvol_name):
    """Get snapshots for a subvolume from directory listing. Returns [(datetime, full_path)] newest-first."""
    if not os.path.exists(lib.SNAP_DIR):
        return []
    prefix = subvol_name + "."
    snaps = []

    for fname in os.listdir(lib.SNAP_DIR):
        if not fname.startswith(prefix):
            continue
        ts_str = fname[len(prefix) :]
        for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
            try:
                dt = datetime.datetime.strptime(ts_str, fmt)
                snaps.append((dt, os.path.join(lib.SNAP_DIR, fname)))
                break
            except ValueError:
                continue

    return sorted(snaps, key=lambda x: x[0], reverse=True)


def prune_snapshots(subvol_name):
    snaps = get_snapshots(subvol_name)
    if not snaps:
        return

    keep_paths = set()
    if snaps:
        keep_paths.add(snaps[0][1])

    def add_bucket(count, key_func):
        seen = set()
        kept = 0
        for dt, path in snaps:
            interval = key_func(dt)
            if interval not in seen and kept < count:
                keep_paths.add(path)
                seen.add(interval)
                kept += 1

    r = lib.CONF["retention"]
    add_bucket(r["hourly"], lambda d: d.strftime("%Y%m%d%H"))
    add_bucket(r["daily"], lambda d: d.strftime("%Y%m%d"))
    add_bucket(r["weekly"], lambda d: f"{d.isocalendar()[0]}-{d.isocalendar()[1]}")
    add_bucket(r["monthly"], lambda d: d.strftime("%Y%m"))

    for dt, path in snaps:
        if path not in keep_paths:
            print(f"[-] Pruning {os.path.basename(path)}")
            try:
                lib.run_cmd(["btrfs", "subvolume", "delete", path])
                STATS["pruned"] += 1
            except Exception:
                STATS["errors"] += 1


def timer_control(enable):
    """Enable or disable the snapshot timer."""
    action = "enable" if enable else "disable"
    subprocess.run(["systemctl", action, "--now", "bread-snapshot.timer"], check=True)
    state = "enabled" if enable else "disabled"
    print(f"Automatic snapshots {state}.")


def main():
    import argparse

    parser = argparse.ArgumentParser(prog="bread snapshot")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--enable-timer", action="store_true", help="Enable automatic snapshots"
    )
    group.add_argument(
        "--disable-timer", action="store_true", help="Disable automatic snapshots"
    )
    args = parser.parse_args()

    if os.geteuid() != 0:
        sys.exit("Root required.")

    if args.enable_timer or args.disable_timer:
        timer_control(args.enable_timer)
        return

    lib.init()

    # Clear undo buffer to reclaim space
    lib.clear_old_buffer()

    now = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    for sub in lib.discover_subvolumes():
        try:
            create_snapshot(sub, now)
            prune_snapshots(sub)
        except Exception:
            STATS["errors"] += 1

    # Backup current kernel and write marker for this timestamp
    if STATS["created"] > 0:
        ver = lib.backup_kernel()
        lib.write_kernel_marker(now, ver)

    # Clean up kernel backups no longer referenced by any snapshot
    lib.prune_kernel_backups()

    print(
        f"Created {STATS['created']} | Pruned {STATS['pruned']} | Errors {STATS['errors']}"
    )
