# Bread Suite

Two applications: **CLI** and **GUI**, sharing a common library.

- **lib.py**: Shared utilities -- config loading, constants, btrfs helpers, subvolume discovery.
- **CLI**: fdisk-style interactive tool. Contains all snapshot/rollback/revert logic. Entry point: `bread <subcommand>`
- **GUI**: GTK application. Reads filesystem for display, calls `pkexec bread <subcommand>` for privileged operations. Entry point: `bread-gui`

Config: `/etc/bread.json` | Log: `/var/log/bread.log`

---

### 1. File Structure

```text
/
├── usr/
│   └── local/
│       ├── bin/
│       │   ├── bread                # CLI entry point
│       │   └── bread-gui            # GUI entry point
│       └── lib/
│           └── bread/
│               ├── __init__.py
│               ├── lib.py           # Shared: config, locking, btrfs ops, discovery
│               ├── cli/
│               │   ├── __init__.py
│               │   ├── main.py      # CLI dispatcher
│               │   ├── config.py    # Interactive config wizard
│               │   ├── snapshot.py  # CLI snapshot wrapper
│               │   ├── rollback.py  # fdisk-style command loop
│               │   └── revert.py    # CLI revert wrapper
│               └── gui/
│                   ├── __init__.py
│                   ├── app.py       # GTK application
│                   ├── wizard.py    # First-run config wizard dialog
│                   └── window.py    # Main window (snapshot table)
├── etc/
│   └── systemd/
│       └── system/
│           ├── bread-snapshot.service
│           └── bread-snapshot.timer
├── usr/
│   └── share/
│       ├── polkit-1/
│       │   └── actions/
│       │       └── org.bread.policy # Privilege elevation policy
│       └── applications/
│           └── bread.desktop        # Desktop launcher
├── install.sh
```

---

### 2. Architecture

**Shared library** (`bread/lib.py`):

Config loading, constants, btrfs helpers (`is_btrfs_subvolume`, `run_cmd`),
`discover_subvolumes()`, locking, signal shielding. Imported by both CLI and GUI.

**CLI** (`bread/cli/`):

All snapshot, rollback, and revert logic lives here. Interactive prompts, argparse,
print output. The CLI is the complete implementation.

**GUI** (`bread/gui/`):

GTK application. Reads the filesystem directly for display (snapshot table, config).
For privileged operations (rollback, revert, config save), calls `pkexec bread <subcommand>`.
The GUI never reimplements btrfs logic -- it delegates to the CLI.

No daemon. State lives on the filesystem (config file, snapshot directory, undo
buffer). The systemd timer handles scheduled snapshot creation.

---

### 3. Dispatcher (`/usr/local/bin/bread`)

```python
#!/usr/bin/env python3
import sys
import importlib

sys.path.insert(0, "/usr/local/lib")

USAGE = """Usage: bread <command> [options]

Commands:
  config     Setup wizard
  snapshot   Create snapshots and prune old ones
  rollback   Interactive snapshot recovery
  revert     Undo last rollback"""

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in ("config", "snapshot", "rollback", "revert"):
        print(f"Unknown command: {cmd}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        sys.exit(1)

    sys.argv = sys.argv[1:]  # shift so subcommand's argparse sees its own args
    mod = importlib.import_module(f"bread.cli.{cmd}")
    mod.main()

if __name__ == "__main__":
    main()
```

---

### 4. Shared Library (`bread/lib.py`)

```python
import os
import sys
import json
import fcntl
import subprocess
import signal
import re
from datetime import datetime

CONFIG_FILE = "/etc/bread.json"
LOG_FILE = "/var/log/bread.log"
LOCK_FILE = "/var/lock/bread.lock"

DEFAULT_CONF = {
    "mount_point": "/mnt/btrfs_pool",
    "snapshot_dir_name": "_btrbk_snap",
    "retention": {"hourly": 48, "daily": 14, "weekly": 4, "monthly": 6},
    "safety_retention": 3,
    "broken_retention": 1
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                conf = json.load(f)
                for k, v in DEFAULT_CONF.items():
                    if k not in conf: conf[k] = v
                return conf
        except Exception: return DEFAULT_CONF
    return DEFAULT_CONF

CONF = load_config()
MOUNT_POINT = CONF["mount_point"]
SNAP_DIR = os.path.join(MOUNT_POINT, CONF["snapshot_dir_name"])
OLD_DIR = os.path.join(MOUNT_POINT, "old")
TEMP_SUFFIX = "_swap_tmp"

class SignalShield:
    def __enter__(self):
        self.orig_int = signal.getsignal(signal.SIGINT)
        self.orig_term = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        return self
    def __exit__(self, exc_type, exc_value, tb):
        signal.signal(signal.SIGINT, self.orig_int)
        signal.signal(signal.SIGTERM, self.orig_term)

class LockManager:
    def __enter__(self):
        self.fd = None
        self.lock_fd = None
        try:
            self.fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
            self.lock_fd = os.fdopen(self.fd, 'w')
            fcntl.lockf(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self.lock_fd
        except (IOError, OSError):
            if self.lock_fd: self.lock_fd.close()
            elif self.fd: os.close(self.fd)
            sys.exit("Error: Locked. Is another bread command running?")

    def __exit__(self, exc_type, exc_value, tb):
        if self.lock_fd: self.lock_fd.close()

def log(msg, console=True, dry_run=False):
    prefix = "[DRY RUN] " if dry_run else ""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] {prefix}{msg}"
    if os.geteuid() == 0 and not dry_run:
        try:
            with open(LOG_FILE, "a") as f: f.write(entry + "\n")
        except Exception: pass
    if console: print(f"{prefix}{msg}")

def run_cmd(cmd, desc=None, check=True, dry_run=False):
    if dry_run:
        log(f"Would execute: {' '.join(cmd)}", dry_run=True)
        return
    if desc: log(desc)
    try:
        subprocess.run(cmd, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode().strip()
        log(f"Command failed: {' '.join(cmd)}\nStderr: {err_msg}", console=False)
        print(f"Error: {err_msg}", file=sys.stderr)
        raise

def is_btrfs_subvolume(path):
    if not os.path.exists(path): return False
    ret = subprocess.call(["btrfs", "subvolume", "show", path],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return ret == 0

def check_mount_sanity():
    if not os.path.exists(MOUNT_POINT):
        sys.exit(f"Error: Mount point {MOUNT_POINT} does not exist.")
    try:
        output = subprocess.check_output(["btrfs", "subvolume", "show", MOUNT_POINT],
                                       stderr=subprocess.STDOUT, text=True)
        if "Subvolume ID: 5" not in output and "is the top-level subvolume" not in output:
            print(f"WARNING: {MOUNT_POINT} is not top-level (ID 5).")
            if input("Proceed anyway? (y/N): ").lower() != 'y': sys.exit(1)
    except Exception: pass

def check_fstab_safety():
    issues = []
    try:
        with open("/etc/fstab", "r") as f:
            for line in f:
                if "btrfs" in line and "subvolid=" in line and not line.strip().startswith("#"):
                    issues.append(line.strip())
    except Exception: pass
    if issues:
        print("!!! DANGER: FSTAB USES SUBVOLID !!! (Boot will fail after rollback)")
        for i in issues: print(f"  {i}")
        if input("Proceed anyway? (y/N): ").lower() != 'y': sys.exit(1)

def discover_subvolumes():
    """Find all btrfs subvolumes that are direct children of MOUNT_POINT."""
    exclude = {CONF["snapshot_dir_name"], "old", "lost+found"}
    subvols = []
    for item in os.listdir(MOUNT_POINT):
        if item in exclude or item.endswith(TEMP_SUFFIX):
            continue
        path = os.path.join(MOUNT_POINT, item)
        if os.path.isdir(path) and is_btrfs_subvolume(path):
            subvols.append(item)
    return sorted(subvols)

def validate_name(name):
    if not re.match(r'^[a-zA-Z0-9_\-.]+$', name) or '..' in name:
        raise ValueError(f"Invalid name '{name}'. Use alphanumeric, _, -, .")
```

---

### 5. CLI: Setup Wizard (`bread/cli/config.py`)

```python
import os
import sys
import json
import copy
from bread import lib

def ask(prompt, default):
    val = input(f"{prompt} [{default}]: ").strip()
    return val if val else default

def ask_int(prompt, default):
    while True:
        val = input(f"{prompt} [{default}]: ").strip()
        if not val: return default
        if val.isdigit(): return int(val)
        print("Integer required.")

def main():
    if os.geteuid() != 0: sys.exit("Root required.")

    print("--- Bread Configuration ---")
    conf = copy.deepcopy(lib.DEFAULT_CONF)

    conf["mount_point"] = ask("Btrfs Mount Point", conf["mount_point"])

    s_dir = ask("Snapshot Dir Name", conf["snapshot_dir_name"])
    try:
        lib.validate_name(s_dir)
        conf["snapshot_dir_name"] = s_dir
    except ValueError as e:
        sys.exit(f"Error: {e}")

    print("\n[ Retention ]")
    conf["retention"]["hourly"] = ask_int("Hourly", 48)
    conf["retention"]["daily"] = ask_int("Daily", 14)
    conf["retention"]["weekly"] = ask_int("Weekly", 4)
    conf["retention"]["monthly"] = ask_int("Monthly", 6)
    conf["safety_retention"] = ask_int("Safety Snapshots", 3)
    conf["broken_retention"] = ask_int("Broken Snapshots", 1)

    if not os.path.ismount(conf["mount_point"]):
        print(f"\n! WARNING: {conf['mount_point']} is not a mount point.")
        if input("! Save anyway? (y/N): ").lower() != 'y': sys.exit(1)

    try:
        with open(lib.CONFIG_FILE, 'w') as f:
            json.dump(conf, f, indent=4)
        os.chmod(lib.CONFIG_FILE, 0o644)
        print("Configuration saved.")
    except Exception as e:
        sys.exit(f"Error: {e}")
```

---

### 6. CLI: Snapshot (`bread/cli/snapshot.py`)

```python
import os
import sys
import datetime
import glob
import argparse
from bread import lib

STATS = {'created': 0, 'pruned': 0, 'errors': 0, 'skipped': 0}
DRY_RUN = False

def create_snapshot(subvol_name):
    now = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    src = os.path.join(lib.MOUNT_POINT, subvol_name)
    dst = os.path.join(lib.SNAP_DIR, f"{subvol_name}.{now}")

    if not os.path.exists(lib.SNAP_DIR):
        if not DRY_RUN: os.makedirs(lib.SNAP_DIR, exist_ok=True)

    if os.path.exists(dst):
        STATS['skipped'] += 1
        return

    print(f"[+] Snapshot {subvol_name} -> {os.path.basename(dst)}")
    try:
        lib.run_cmd(["btrfs", "subvolume", "snapshot", "-r", src, dst], dry_run=DRY_RUN)
        STATS['created'] += 1
    except Exception:
        STATS['errors'] += 1

def get_snapshots(subvol_name):
    pattern = os.path.join(lib.SNAP_DIR, f"{subvol_name}.[0-9]*")
    snaps = []
    prefix_len = len(subvol_name) + 1

    for path in glob.glob(pattern):
        fname = os.path.basename(path)
        if len(fname) <= prefix_len: continue
        ts_str = fname[prefix_len:]

        for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
            try:
                dt = datetime.datetime.strptime(ts_str, fmt)
                snaps.append((dt, path))
                break
            except ValueError: continue

    return sorted(snaps, key=lambda x: x[0], reverse=True)

def prune_special_snapshots(subvol_name, suffix, retention):
    pattern = os.path.join(lib.SNAP_DIR, f"{subvol_name}.{suffix}.*")
    snaps = sorted(glob.glob(pattern), reverse=True)

    for path in snaps[retention:]:
        print(f"[-] Pruning {suffix}: {os.path.basename(path)}")
        try:
            lib.run_cmd(["btrfs", "subvolume", "delete", path], dry_run=DRY_RUN)
            STATS['pruned'] += 1
        except Exception: STATS['errors'] += 1

def prune_snapshots(subvol_name):
    snaps = get_snapshots(subvol_name)
    if not snaps: return

    keep_paths = set()
    if snaps: keep_paths.add(snaps[0][1])

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
    add_bucket(r['hourly'], lambda d: d.strftime("%Y%m%d%H"))
    add_bucket(r['daily'],  lambda d: d.strftime("%Y%m%d"))
    add_bucket(r['weekly'], lambda d: f"{d.isocalendar()[0]}-{d.isocalendar()[1]}")
    add_bucket(r['monthly'], lambda d: d.strftime("%Y%m"))

    for dt, path in snaps:
        if path not in keep_paths:
            print(f"[-] Pruning {os.path.basename(path)}")
            try:
                lib.run_cmd(["btrfs", "subvolume", "delete", path], dry_run=DRY_RUN)
                STATS['pruned'] += 1
            except Exception: STATS['errors'] += 1

def main():
    global DRY_RUN
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    DRY_RUN = args.dry_run

    if os.geteuid() != 0 and not DRY_RUN: sys.exit("Root required.")

    with lib.LockManager():
        for sub in lib.discover_subvolumes():
            try:
                create_snapshot(sub)
                prune_snapshots(sub)
                prune_special_snapshots(sub, "SAFETY", lib.CONF.get("safety_retention", 3))
                prune_special_snapshots(sub, "BROKEN", lib.CONF.get("broken_retention", 1))
            except Exception:
                STATS['errors'] += 1

        summary = f"Created {STATS['created']} | Pruned {STATS['pruned']} | Errors {STATS['errors']}"
        print(f"{'[DRY RUN] ' if DRY_RUN else ''}SUMMARY: {summary}")
```

---

### 7. CLI: Rollback (`bread/cli/rollback.py`)

fdisk-style interactive command loop. No curses dependency.

**Interaction model:**

```text
$ bread rollback

Bread Rollback
──────────────────────────────────────────────────
    #  Timestamp             Subvolumes
  191  2025-06-15 05:00:00   root, home
  192  2025-06-15 06:00:00   root, home
  193  2025-06-15 07:00:00   root
  194  2025-06-15 08:00:00   root, home
  ...
  200  2025-06-15 14:00:00   root, home

Command (m for help): m

  Commands:
  #     Select snapshot by number
  l     List all snapshots
  m     Show this help
  q     Quit

Command (m for help): 198

Selected: 2025-06-15 12:00:00

Roll back which subvolumes?
  1) All (Recommended)
  2) root
  3) home

Select [1]:

Rollback Plan:
  root  →  2025-06-15 12:00:00
  home  →  2025-06-15 12:00:00

Confirm? (y/N): y
>> Phase -1: Safety Snapshots...
...
```

**Numbering:** 1 = oldest snapshot, N = newest. Ascending display (newest at bottom,
visible in terminal). Numbers are stable between the initial 10-snapshot view and
the `l` (list all) view.

**Subvolume selection:** Default is All (press Enter). Comma-separated numbers
for individual selection (e.g. `2,3`).

```python
import os
import sys
import re
import shutil
import argparse
import subprocess
from collections import defaultdict
from datetime import datetime
from bread import lib

DRY_RUN = False

def format_ts(ts_str):
    """Convert internal timestamp (YYYYMMDDTHHMMSS) to human-readable."""
    for fmt_in, fmt_out in [("%Y%m%dT%H%M%S", "%Y-%m-%d %H:%M:%S"),
                             ("%Y%m%dT%H%M", "%Y-%m-%d %H:%M")]:
        try: return datetime.strptime(ts_str, fmt_in).strftime(fmt_out)
        except ValueError: continue
    return ts_str

def build_snapshot_table():
    """Scan snapshot dir. Returns [(ts_str, [subvols]), ...] sorted oldest-first.
    Position in list (1-indexed) = stable session ID."""
    if not os.path.exists(lib.SNAP_DIR):
        return []

    timestamps = defaultdict(list)
    for fname in os.listdir(lib.SNAP_DIR):
        if "SAFETY" in fname or "BROKEN" in fname:
            continue
        m = re.match(r'^(.+)\.(\d{8}T(?:\d{6}|\d{4}))$', fname)
        if not m:
            continue
        subvol, ts_str = m.groups()
        for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
            try:
                datetime.strptime(ts_str, fmt)
                timestamps[ts_str].append(subvol)
                break
            except ValueError: continue

    return [(ts, sorted(subs)) for ts, subs in sorted(timestamps.items())]

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
        print(f"  {num:>4}  {format_ts(ts_str):<21}  {', '.join(subvols)}")

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
    print("─" * 50)
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
                    print(f"\nSelected: {format_ts(ts_str)}")
                    selected = select_subvolumes(subvols)

                    print("\nRollback Plan:")
                    for sub in selected:
                        print(f"  {sub}  →  {format_ts(ts_str)}")

                    confirm = input("\nConfirm? (y/N): ").strip().lower()
                    if confirm == "y":
                        return {sub: ts_str for sub in selected}
                    print("Cancelled.")
                else:
                    print(f"  Invalid number. Range: 1-{len(table)}")
            except ValueError:
                print("  Unknown command. Type 'm' for help.")

def execute_rollback(plan):
    """Execute the rollback plan. Same atomic swap logic with safety phases."""
    with lib.LockManager():
        lib.log(f">> Rollback Plan: {plan}", dry_run=DRY_RUN)

        # Cleanup Stale Temps
        for item in os.listdir(lib.MOUNT_POINT):
            if item.endswith(lib.TEMP_SUFFIX):
                path = os.path.join(lib.MOUNT_POINT, item)
                if lib.is_btrfs_subvolume(path):
                    lib.run_cmd(["btrfs", "subvolume", "delete", path], check=False, dry_run=DRY_RUN)
                else:
                    if not DRY_RUN: shutil.rmtree(path)

        # Phase -1: Safety Snapshots
        print(">> Phase -1: Safety Snapshots...")
        now = datetime.now().strftime("%Y%m%dT%H%M%S")
        created_safety = []
        try:
            for sub in plan.keys():
                src = os.path.join(lib.MOUNT_POINT, sub)
                dst = os.path.join(lib.SNAP_DIR, f"{sub}.SAFETY.{now}")
                if os.path.exists(dst): continue
                lib.run_cmd(["btrfs", "subvolume", "snapshot", "-r", src, dst], dry_run=DRY_RUN)
                created_safety.append(dst)
        except Exception as e:
            lib.log(f"CRITICAL: Safety snapshot failed: {e}", console=False)
            print("Aborting. Cleaning up partial safety snapshots...")
            for s in created_safety:
                lib.run_cmd(["btrfs", "subvolume", "delete", s], check=False, dry_run=DRY_RUN)
            sys.exit(1)

        # Phase 0: Flush Undo Buffer
        print(">> Phase 0: Clearing Undo Buffer (old/)...")
        if not os.path.exists(lib.OLD_DIR) and not DRY_RUN:
            os.makedirs(lib.OLD_DIR)

        if os.path.exists(lib.OLD_DIR):
            lib.log(f"Flushing undo buffer: {os.listdir(lib.OLD_DIR)}", dry_run=DRY_RUN)
            for item in os.listdir(lib.OLD_DIR):
                path = os.path.join(lib.OLD_DIR, item)
                if lib.is_btrfs_subvolume(path):
                    lib.run_cmd(["btrfs", "subvolume", "delete", path], dry_run=DRY_RUN)
                else:
                    if not DRY_RUN: shutil.rmtree(path)

        # Phase 1: Prepare Temps (writable snapshots from read-only originals)
        print(">> Phase 1: Prepare Temps...")
        temp_map = {}
        try:
            for sub, ts in plan.items():
                src = os.path.join(lib.SNAP_DIR, f"{sub}.{ts}")
                tmp = os.path.join(lib.MOUNT_POINT, f"{sub}{lib.TEMP_SUFFIX}")

                if not DRY_RUN and subprocess.call(["btrfs", "subvolume", "show", src],
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
                    raise Exception(f"Snapshot {src} corrupted")

                lib.run_cmd(["btrfs", "subvolume", "snapshot", src, tmp], dry_run=DRY_RUN)
                temp_map[sub] = tmp
        except Exception as e:
            lib.log(f"Phase 1 Error: {e}")
            for t in temp_map.values():
                lib.run_cmd(["btrfs", "subvolume", "delete", t], check=False, dry_run=DRY_RUN)
            sys.exit(1)

        # Phase 2: Atomic Swap (signal-shielded)
        print(">> Phase 2: Swapping...")
        with lib.SignalShield():
            completed = []
            mid_swap = False
            order = sorted(plan.keys(), key=lambda x: 1 if x == 'root' else 0)

            try:
                for sub in order:
                    live = os.path.join(lib.MOUNT_POINT, sub)
                    old = os.path.join(lib.OLD_DIR, sub)
                    temp = temp_map[sub]

                    mid_swap = False
                    if os.path.exists(live):
                        if DRY_RUN: print(f"mv {live} -> {old}")
                        else:
                            os.rename(live, old)
                            mid_swap = True

                    if DRY_RUN: print(f"mv {temp} -> {live}")
                    else:
                        os.rename(temp, live)

                    mid_swap = False
                    completed.append(sub)
                    lib.log(f"Swapped {sub}")

            except Exception as e:
                lib.log(f"CRITICAL SWAP FAILURE: {e}")
                if mid_swap:
                    try: os.rename(old, live)
                    except: lib.log("FATAL: Could not undo mid-swap rename")

                for sub in reversed(completed):
                    l = os.path.join(lib.MOUNT_POINT, sub)
                    o = os.path.join(lib.OLD_DIR, sub)
                    t = temp_map[sub]
                    try:
                        if os.path.exists(l): os.rename(l, t)
                        if os.path.exists(o): os.rename(o, l)
                    except: lib.log(f"FATAL: Unwind failed for {sub}")
                sys.exit(1)

        if not DRY_RUN: subprocess.run(["sync"])
        print("\n>> DONE. Previous state is in old/.")
        lib.log("Rollback success")

    try:
        if not DRY_RUN and input("Reboot? [Y/n]: ").strip().lower() in ['', 'y', 'yes']:
            subprocess.run(["reboot"])
    except: pass

def main():
    global DRY_RUN
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    DRY_RUN = args.dry_run

    if os.geteuid() != 0 and not DRY_RUN: sys.exit("Root required.")
    lib.check_mount_sanity()
    lib.check_fstab_safety()

    table = build_snapshot_table()
    plan = command_loop(table)
    if not plan: sys.exit("Cancelled.")

    execute_rollback(plan)
```

---

### 8. CLI: Revert (`bread/cli/revert.py`)

```python
import os
import sys
import argparse
import subprocess
import shutil
from datetime import datetime
from bread import lib

DRY_RUN = False

def main():
    global DRY_RUN
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    DRY_RUN = args.dry_run

    if os.geteuid() != 0 and not DRY_RUN: sys.exit("Root required.")

    lib.check_mount_sanity()
    lib.check_fstab_safety()

    if not os.path.exists(lib.OLD_DIR): sys.exit("No undo buffer (old/) found.")

    to_revert = [i for i in os.listdir(lib.OLD_DIR)
                 if lib.is_btrfs_subvolume(os.path.join(lib.OLD_DIR, i))]

    if not to_revert: sys.exit("old/ is empty.")

    print(f"Undo Targets: {', '.join(to_revert)}")
    if input("Confirm Undo (Swap Live <-> Old)? (y/N): ").lower() != 'y': sys.exit(0)

    with lib.LockManager():
        lib.log(f"Starting Undo for {to_revert}", dry_run=DRY_RUN)

        # Cleanup Stale Temps
        for item in os.listdir(lib.MOUNT_POINT):
            if item.endswith(lib.TEMP_SUFFIX):
                path = os.path.join(lib.MOUNT_POINT, item)
                if lib.is_btrfs_subvolume(path):
                    lib.run_cmd(["btrfs", "subvolume", "delete", path], check=False, dry_run=DRY_RUN)
                else:
                    if not DRY_RUN: shutil.rmtree(path)

        # Phase -1: Safety Snapshot (BROKEN)
        now = datetime.now().strftime("%Y%m%dT%H%M%S")
        created_safety = []
        try:
            for sub in to_revert:
                src = os.path.join(lib.MOUNT_POINT, sub)
                dst = os.path.join(lib.SNAP_DIR, f"{sub}.BROKEN.{now}")
                if os.path.exists(src):
                    lib.run_cmd(["btrfs", "subvolume", "snapshot", "-r", src, dst], dry_run=DRY_RUN)
                    created_safety.append(dst)
        except Exception as e:
            lib.log(f"Safety snapshot failed: {e}")
            print("Cleaning up partial snapshots...")
            for s in created_safety:
                lib.run_cmd(["btrfs", "subvolume", "delete", s], check=False, dry_run=DRY_RUN)
            sys.exit(1)

        # Phase 2: Toggle Logic (swap live <-> old)
        with lib.SignalShield():
            completed = []
            order = sorted(to_revert, key=lambda x: 1 if x == 'root' else 0)

            try:
                for sub in order:
                    live = os.path.join(lib.MOUNT_POINT, sub)
                    old = os.path.join(lib.OLD_DIR, sub)
                    temp = os.path.join(lib.MOUNT_POINT, f"{sub}{lib.TEMP_SUFFIX}")

                    step = 0
                    if os.path.exists(live):
                        if DRY_RUN: print(f"mv {live} -> {temp}")
                        else: os.rename(live, temp)
                        step = 1

                    if DRY_RUN: print(f"mv {old} -> {live}")
                    else: os.rename(old, live)
                    step = 2

                    if os.path.exists(temp) or DRY_RUN:
                        if DRY_RUN: print(f"mv {temp} -> {old}")
                        else: os.rename(temp, old)
                        step = 3

                    completed.append(sub)
                    lib.log(f"Reverted {sub}")

            except Exception as e:
                lib.log(f"Undo Failed at step {step}: {e}")
                print("CRITICAL ERROR. Attempting to reverse undo...")

                # Mid-swap recovery for CURRENT subvolume
                if step == 2:
                    try: os.rename(live, old)
                    except: lib.log("FATAL: Failed to return Old to Old")
                    try: os.rename(temp, live)
                    except: lib.log("FATAL: Failed to return Live to Live")
                elif step == 1:
                    try: os.rename(temp, live)
                    except: lib.log("FATAL: Failed to return Live to Live")

                # Reverse completed subvolumes
                for sub in reversed(completed):
                    l = os.path.join(lib.MOUNT_POINT, sub)
                    o = os.path.join(lib.OLD_DIR, sub)
                    t = os.path.join(lib.MOUNT_POINT, f"{sub}{lib.TEMP_SUFFIX}")
                    try:
                        if os.path.exists(l): os.rename(l, t)
                        if os.path.exists(o): os.rename(o, l)
                        if os.path.exists(t): os.rename(t, o)
                    except:
                        lib.log(f"FATAL: Recovery failed for {sub}")
                sys.exit(1)

        if not DRY_RUN: subprocess.run(["sync"])
        print("Undo Complete. Previous state is back in old/.")
```

---

### 9. GUI: Application (`bread/gui/`)

GTK application. Runs unprivileged for browsing, elevates via pkexec for
privileged operations.

**First launch (no config):**

```text
┌─────────────────────────────────────────┐
│  Bread - Setup                          │
│                                         │
│  Btrfs Mount Point:  [/mnt/btrfs_pool]  │
│  Snapshot Dir Name:  [_btrbk_snap    ]  │
│                                         │
│  Retention                              │
│  Hourly:   [48]    Daily:   [14]        │
│  Weekly:   [ 4]    Monthly: [ 6]        │
│  Safety:   [ 3]    Broken:  [ 1]        │
│                                         │
│              [Cancel]  [Save]           │
└─────────────────────────────────────────┘
```

Save calls `pkexec` to write `/etc/bread.json` (needs root). On success,
transitions to the main window.

**Main window:**

```text
┌──────────────────────────────────────────────────────────┐
│  Bread                                    [Config] [⟳]  │
│─────────────────────────────────────────────────────────│
│    #  Timestamp             Subvolumes                   │
│    1  2025-01-15 03:00:00   root, home                   │
│    2  2025-01-22 03:00:00   root, home                   │
│    3  2025-02-01 03:00:00   root, home                   │
│   ..  .................     ..........                   │
│  197  2025-06-15 11:00:00   root, home                   │
│  198  2025-06-15 12:00:00   root                     │ ▲ │
│  199  2025-06-15 13:00:00   root, home               │ █ │
│▸ 200  2025-06-15 14:00:00   root, home               │ ▼ │
│─────────────────────────────────────────────────────────│
│  [Rollback]                              [Revert Undo]  │
└──────────────────────────────────────────────────────────┘
```

- Full snapshot table in a scrollable list, auto-scrolled to bottom (newest visible)
- Select a row, click Rollback to begin
- Refresh button to rescan the snapshot directory
- Config button reopens the setup dialog

**Rollback confirmation dialog:**

```text
┌──────────────────────────────────────────┐
│  Rollback to 2025-06-15 12:00:00        │
│                                          │
│  Select subvolumes:                      │
│  ☑ All (Recommended)                     │
│  ☑ root                                  │
│  ☑ home                                  │
│                                          │
│            [Cancel]  [Rollback]          │
└──────────────────────────────────────────┘
```

- All checkboxes checked by default
- "All (Recommended)" toggles all others
- Unchecking a subvolume unchecks "All (Recommended)"
- Clicking Rollback calls `pkexec bread rollback ...` (core execution, elevated)
- On success: dialog with "Rollback complete. Reboot now?" [Later] [Reboot]
- On error: dialog showing the error message

**Revert button:**

- Shows confirmation: "Undo last rollback? Targets: root, home"
- Calls `pkexec bread revert` on confirm
- Same success/error handling as rollback

**GUI entry point (`/usr/local/bin/bread-gui`):**

```python
#!/usr/bin/env python3
import sys
sys.path.insert(0, "/usr/local/lib")
from bread.gui.app import BreadApp
BreadApp().run(sys.argv)
```

---

### 10. Polkit Policy (`/usr/share/polkit-1/actions/org.bread.policy`)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <vendor>Bread Suite</vendor>
  <vendor_url>https://github.com/user/bread</vendor_url>

  <action id="org.bread.manage-snapshots">
    <description>Manage Btrfs snapshots</description>
    <message>Authentication is required to manage Btrfs snapshots</message>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/local/bin/bread</annotate>
    <annotate key="org.freedesktop.policykit.exec.allow_gui">true</annotate>
  </action>
</policyconfig>
```

---

### 11. Desktop File (`/usr/share/applications/bread.desktop`)

```ini
[Desktop Entry]
Name=Bread
Comment=Btrfs snapshot manager
Exec=bread-gui
Icon=drive-harddisk
Terminal=false
Type=Application
Categories=System;
```

---

### 12. Installer (`install.sh`)

```bash
#!/bin/bash
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root"
  exit 1
fi

echo "Installing Bread Suite..."

# Shared library
mkdir -p /usr/local/lib/bread
cp bread/__init__.py bread/lib.py /usr/local/lib/bread/
chmod 644 /usr/local/lib/bread/*.py

# CLI
mkdir -p /usr/local/lib/bread/cli
cp bread/cli/__init__.py bread/cli/main.py bread/cli/config.py bread/cli/snapshot.py bread/cli/rollback.py bread/cli/revert.py /usr/local/lib/bread/cli/
chmod 644 /usr/local/lib/bread/cli/*.py

cp bin/bread /usr/local/bin/bread
chmod +x /usr/local/bin/bread

# GUI
mkdir -p /usr/local/lib/bread/gui
cp bread/gui/__init__.py bread/gui/app.py bread/gui/wizard.py bread/gui/window.py /usr/local/lib/bread/gui/
chmod 644 /usr/local/lib/bread/gui/*.py

cp bin/bread-gui /usr/local/bin/bread-gui
chmod +x /usr/local/bin/bread-gui

# Polkit + Desktop
cp org.bread.policy /usr/share/polkit-1/actions/
cp bread.desktop /usr/share/applications/

# Systemd
cp bread-snapshot.service bread-snapshot.timer /etc/systemd/system/
systemctl daemon-reload

echo "Installation Complete."
echo "1. Run 'bread config' or launch Bread from your application menu."
echo "2. Enable: systemctl enable --now bread-snapshot.timer"
```

---

### 13. Systemd Units

**`/etc/systemd/system/bread-snapshot.service`**

```ini
[Unit]
Description=Run Bread snapshot engine

[Service]
Type=oneshot
ExecStart=/usr/local/bin/bread snapshot
Nice=19
IOSchedulingClass=idle
IOSchedulingPriority=7
```

**`/etc/systemd/system/bread-snapshot.timer`**

```ini
[Unit]
Description=Run Bread snapshot hourly

[Timer]
OnCalendar=hourly
Persistent=true
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
```
