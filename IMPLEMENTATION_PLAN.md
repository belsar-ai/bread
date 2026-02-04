# Bread

Two applications: **CLI** and **GUI**, sharing a common library.

- **lib.py**: Shared utilities -- config loading, constants, btrfs helpers, subvolume discovery.
- **CLI**: fdisk-style interactive tool. Contains all snapshot/rollback/revert logic. Entry point: `bread <subcommand>`
- **GUI**: GTK application. Reads filesystem for display, calls `pkexec bread <subcommand>` for privileged operations. Entry point: `bread-gui`

Config: `/etc/bread.json`

---

### 1. File Structure

```text
/usr/
├── bin/
│   ├── bread                            # CLI entry point
│   └── bread-gui                        # GUI entry point
├── lib/python3/site-packages/bread/
│   ├── __init__.py
│   ├── lib.py                           # Shared: config, btrfs ops, discovery
│   ├── cli/
│   │   ├── __init__.py
│   │   ├── main.py                      # CLI dispatcher
│   │   ├── config.py                    # Interactive config wizard
│   │   ├── snapshot.py                  # Snapshot creation + pruning
│   │   ├── rollback.py                  # fdisk-style command loop + execution
│   │   ├── revert.py                    # Undo execution
│   │   └── purge.py                     # Remove all bread data
│   └── gui/
│       ├── __init__.py
│       ├── app.py                       # GTK application
│       ├── wizard.py                    # First-run config wizard dialog
│       └── window.py                    # Main window (snapshot table)
├── share/
│   ├── polkit-1/actions/
│   │   └── org.bread.policy             # Privilege elevation policy
│   └── applications/
│       └── bread.desktop                # Desktop launcher
/usr/lib/systemd/system/
├── bread-snapshot.service              # RPM-packaged
└── bread-snapshot.timer                # RPM-packaged
/etc/systemd/system/
└── mnt-_bread.mount                    # Created by bread config at runtime
```

---

### 2. Architecture

**Shared library** (`bread/lib.py`):

Config loading, constants, btrfs helpers (`run_cmd`, `btrfs_list`),
`discover_subvolumes()`, `build_snapshot_table()`. Imported by both CLI and GUI.

**CLI** (`bread/cli/`):

All snapshot, rollback, and revert logic lives here. Interactive prompts,
print output. The CLI is the complete implementation.

**GUI** (`bread/gui/`):

GTK4 + libadwaita application. Uses `Adw.Application`, `Adw.ApplicationWindow`,
`Adw.HeaderBar` for native GNOME look. Reads the filesystem directly for display
(snapshot table, config). For privileged operations (rollback, revert, config save),
calls `pkexec bread <subcommand>`. The GUI never reimplements btrfs logic -- it
delegates to the CLI.

No daemon. State lives on the filesystem (config file, snapshot directory, undo
buffer). The systemd timer handles scheduled snapshot creation.

---

### 3. Dispatcher

**Entry point (`/usr/bin/bread`):**

```python
#!/usr/bin/env python3
from bread.cli.main import main
main()
```

**Dispatcher (`bread/cli/main.py`):**

```python
import sys
import argparse
import importlib

COMMANDS = {
    "config":   "Setup wizard",
    "snapshot": "Create snapshots and prune old ones",
    "rollback": "Interactive snapshot recovery",
    "revert":   "Undo last rollback",
    "purge":    "Remove all bread data and config",
}

def main():
    parser = argparse.ArgumentParser(prog="bread", description="Btrfs snapshot manager")
    parser.add_argument("command", choices=COMMANDS.keys(),
                        metavar="command",
                        help="{%(choices)s}")
    parser.add_argument("args", nargs=argparse.REMAINDER)

    if len(sys.argv) < 2:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()
    sys.argv = [args.command] + args.args
    mod = importlib.import_module(f"bread.cli.{args.command}")
    mod.main()
```

---

### 4. Shared Library (`bread/lib.py`)

```python
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

CONF = None

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return None
    try:
        with open(CONFIG_FILE, 'r') as f:
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
    if not os.path.exists(path): return False
    ret = subprocess.call(["btrfs", "subvolume", "show", path],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return ret == 0

def check_fstab_safety(interactive=True):
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
        if not interactive:
            sys.exit("Refusing to proceed non-interactively. Fix fstab first.")
        if input("Proceed anyway? (y/N): ").lower() != 'y': sys.exit(1)

def btrfs_list():
    """Parse `btrfs subvolume list /` into [(path, top_level), ...]."""
    output = subprocess.check_output(
        ["btrfs", "subvolume", "list", "/"], text=True)
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
    exclude = {SNAP_DIR_NAME, "old", "lost+found"}
    return sorted([
        path for path, top in btrfs_list()
        if top == "5" and "/" not in path and path not in exclude
    ])

def format_ts(ts_str):
    """Convert internal timestamp (YYYYMMDDTHHMMSS) to human-readable."""
    for fmt_in, fmt_out in [("%Y%m%dT%H%M%S", "%Y-%m-%d %H:%M:%S"),
                             ("%Y%m%dT%H%M", "%Y-%m-%d %H:%M")]:
        try: return datetime.strptime(ts_str, fmt_in).strftime(fmt_out)
        except ValueError: continue
    return ts_str

def build_snapshot_table():
    """Build snapshot table from btrfs subvolume list.
    Returns [(ts_str, [subvols]), ...] sorted oldest-first.
    Position in list (1-indexed) = stable session ID."""
    prefix = SNAP_DIR_NAME + "/"
    timestamps = defaultdict(list)

    for path, top in btrfs_list():
        if top != "5" or not path.startswith(prefix):
            continue
        fname = path[len(prefix):]
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

```

---

### 5. CLI: Setup Wizard (`bread/cli/config.py`)

```python
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
            ["findmnt", "-n", "-o", "SOURCE", "-t", "btrfs", "/"], text=True)
        return output.strip()
    except subprocess.CalledProcessError:
        sys.exit("Error: root filesystem is not btrfs.")

def ask_int(prompt):
    while True:
        val = input(f"{prompt}: ").strip()
        if not val:
            print("Value required.")
            continue
        if val.isdigit() and int(val) >= 0: return int(val)
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
    with open(MOUNT_UNIT, 'w') as f:
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

    if os.geteuid() != 0: sys.exit("Root required.")

    device = detect_boot_device()

    # Non-interactive mode (all flags provided) — used by GUI
    if all(v is not None for v in [args.hourly, args.daily, args.weekly, args.monthly]):
        conf = {"device": device, "retention": {
            "hourly": args.hourly, "daily": args.daily,
            "weekly": args.weekly, "monthly": args.monthly,
        }}
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
    with open(lib.CONFIG_FILE, 'w') as f:
        json.dump(conf, f, indent=4)
    os.chmod(lib.CONFIG_FILE, 0o644)
    print("Configuration saved.")

    # Create mount unit
    write_mount_unit(device)

    print("\nTo enable automatic hourly snapshots:")
    print("  systemctl enable --now bread-snapshot.timer")
```

---

### 6. CLI: Snapshot (`bread/cli/snapshot.py`)

```python
import os
import sys
import subprocess
import datetime
import re
from bread import lib

STATS = {'created': 0, 'pruned': 0, 'errors': 0}

def create_snapshot(subvol_name):
    now = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    src = os.path.join(lib.MOUNT_POINT, subvol_name)
    dst = os.path.join(lib.SNAP_DIR, f"{subvol_name}.{now}")

    os.makedirs(lib.SNAP_DIR, exist_ok=True)

    if os.path.exists(dst):
        return

    print(f"[+] Snapshot {subvol_name} -> {os.path.basename(dst)}")
    try:
        lib.run_cmd(["btrfs", "subvolume", "snapshot", "-r", src, dst])
        STATS['created'] += 1
    except Exception:
        STATS['errors'] += 1

def get_snapshots(subvol_name):
    """Get snapshots for a subvolume via btrfs subvolume list. Returns [(datetime, full_path)] newest-first."""
    prefix = lib.SNAP_DIR_NAME + "/" + subvol_name + "."
    snaps = []

    for path, top in lib.btrfs_list():
        if top != "5" or not path.startswith(prefix):
            continue
        ts_str = path[len(prefix):]
        for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
            try:
                dt = datetime.datetime.strptime(ts_str, fmt)
                snaps.append((dt, os.path.join(lib.MOUNT_POINT, path)))
                break
            except ValueError: continue

    return sorted(snaps, key=lambda x: x[0], reverse=True)

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
                lib.run_cmd(["btrfs", "subvolume", "delete", path])
                STATS['pruned'] += 1
            except Exception: STATS['errors'] += 1

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
    group.add_argument("--enable-timer", action="store_true", help="Enable automatic snapshots")
    group.add_argument("--disable-timer", action="store_true", help="Disable automatic snapshots")
    args = parser.parse_args()

    if os.geteuid() != 0: sys.exit("Root required.")

    if args.enable_timer or args.disable_timer:
        timer_control(args.enable_timer)
        return

    lib.init()

    for sub in lib.discover_subvolumes():
        try:
            create_snapshot(sub)
            prune_snapshots(sub)
        except Exception:
            STATS['errors'] += 1

    print(f"Created {STATS['created']} | Pruned {STATS['pruned']} | Errors {STATS['errors']}")
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
  root -> 2025-06-15 12:00:00
  home -> 2025-06-15 12:00:00

Done. Previous state is in old/. Reboot to apply.
```

**Numbering:** 1 = oldest snapshot, N = newest. Ascending display (newest at bottom,
visible in terminal). Numbers are stable between the initial 10-snapshot view and
the `l` (list all) view.

**Subvolume selection:** Default is All (press Enter). Comma-separated numbers
for individual selection (e.g. `2,3`).

```python
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
                    print(f"\nSelected: {lib.format_ts(ts_str)}")
                    selected = select_subvolumes(subvols)

                    print("\nRollback Plan:")
                    for sub in selected:
                        print(f"  {sub}  →  {lib.format_ts(ts_str)}")

                    confirm = input("\nConfirm? (y/N): ").strip().lower()
                    if confirm == "y":
                        return {sub: ts_str for sub in selected}
                    print("Cancelled.")
                else:
                    print(f"  Invalid number. Range: 1-{len(table)}")
            except ValueError:
                print("  Unknown command. Type 'm' for help.")

def execute_rollback(plan):
    """Execute the rollback plan."""
    # Clear undo buffer
    if os.path.exists(lib.OLD_DIR):
        for item in os.listdir(lib.OLD_DIR):
            lib.run_cmd(["btrfs", "subvolume", "delete", os.path.join(lib.OLD_DIR, item)])
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
    parser.add_argument("--subvols", type=str, help="Comma-separated subvolume names (default: all)")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    if os.geteuid() != 0: sys.exit("Root required.")
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
                print(f"  {sub}  →  {lib.format_ts(ts_str)}")
            if input("\nConfirm? (y/N): ").strip().lower() != "y":
                sys.exit("Cancelled.")
    else:
        # Interactive mode
        plan = command_loop(table)
        if not plan: sys.exit("Cancelled.")

    execute_rollback(plan)
```

---

### 8. CLI: Revert (`bread/cli/revert.py`)

```python
import os
import sys
import subprocess
from bread import lib

def main():
    import argparse
    parser = argparse.ArgumentParser(prog="bread revert")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    if os.geteuid() != 0: sys.exit("Root required.")
    lib.init()
    lib.check_fstab_safety(interactive=not args.yes)

    if not os.path.exists(lib.OLD_DIR): sys.exit("No undo buffer (old/) found.")

    to_revert = [i for i in os.listdir(lib.OLD_DIR)
                 if lib.is_btrfs_subvolume(os.path.join(lib.OLD_DIR, i))]

    if not to_revert: sys.exit("old/ is empty.")

    if not args.yes:
        print(f"Undo Targets: {', '.join(to_revert)}")
        if input("Confirm? (y/N): ").lower() != 'y': sys.exit(0)

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
```

---

### 9. CLI: Purge (`bread/cli/purge.py`)

Removes all bread data so `dnf remove bread` leaves no trace.

```python
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
    if os.geteuid() != 0: sys.exit("Root required.")

    print("This will remove ALL bread snapshots, config, and systemd units.")
    if input("Continue? (y/N): ").lower() != 'y': sys.exit(0)

    # Disable timer
    subprocess.run(["systemctl", "disable", "--now", "bread-snapshot.timer"], check=False)
    print("Timer disabled.")

    # Delete all snapshots
    delete_subvolumes_in(lib.SNAP_DIR)
    if os.path.exists(lib.SNAP_DIR):
        os.rmdir(lib.SNAP_DIR)

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
```

---

### 10. GUI: Application (`bread/gui/`)

GTK4 + libadwaita application. Runs unprivileged for browsing, elevates via
pkexec for privileged operations. Uses `Adw.ApplicationWindow` and
`Adw.HeaderBar` for native GNOME styling.

**First launch (no config):**

```text
┌─────────────────────────────────────────┐
│  Bread - Setup                          │
│                                         │
│  Retention                              │
│  Hourly:   [  ]    Daily:   [  ]        │
│  Weekly:   [  ]    Monthly: [  ]        │
│                                         │
│              [Cancel]  [Save]           │
└─────────────────────────────────────────┘
```

Save calls `pkexec bread config --hourly N --daily N --weekly N --monthly N`
(auto-detects boot device, writes config, creates mount unit). On success,
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
│  [Snapshots: On]  [Rollback]             [Revert Undo]  │
└──────────────────────────────────────────────────────────┘
```

- Full snapshot table in a scrollable list, auto-scrolled to bottom (newest visible)
- Select a row, click Rollback to begin
- Refresh button to rescan the snapshot directory
- Config button reopens the setup dialog
- Snapshots toggle checks timer state (`systemctl is-enabled bread-snapshot.timer`), calls `pkexec bread snapshot --enable-timer` or `--disable-timer` on click

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
- Clicking Rollback calls `pkexec bread rollback --snapshot N --subvols root,home --yes`
- On success: dialog with "Rollback complete. Reboot now?" [Later] [Reboot]
- On error: dialog showing the error message

**Revert button:**

- Shows confirmation: "Undo last rollback? Targets: root, home"
- Calls `pkexec bread revert --yes` on confirm
- Same success/error handling as rollback

**GUI entry point (`/usr/bin/bread-gui`):**

```python
#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
import sys
from bread.gui.app import BreadApp
BreadApp().run(sys.argv)
```

---

### 11. Polkit Policy (`/usr/share/polkit-1/actions/org.bread.policy`)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE policyconfig PUBLIC
 "-//freedesktop//DTD PolicyKit Policy Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/PolicyKit/1/policyconfig.dtd">
<policyconfig>
  <vendor>Bread</vendor>
  <vendor_url>https://github.com/user/bread</vendor_url>

  <action id="org.bread.manage-snapshots">
    <description>Manage Btrfs snapshots</description>
    <message>Authentication is required to manage Btrfs snapshots</message>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">/usr/bin/bread</annotate>
    <annotate key="org.freedesktop.policykit.exec.allow_gui">true</annotate>
  </action>
</policyconfig>
```

---

### 12. Desktop File (`/usr/share/applications/bread.desktop`)

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

### 13. RPM Spec (`bread.spec`)

```spec
Name:           bread
Version:        0.1.0
Release:        1%{?dist}
Summary:        Btrfs snapshot manager with CLI and GUI

License:        GPL-3.0-or-later
URL:            https://github.com/user/bread
Source0:        %{name}-%{version}.tar.gz

Requires:       python3
Requires:       python3-gobject
Requires:       btrfs-progs
Requires:       libadwaita
Requires:       gtk4
Requires:       polkit

BuildArch:      noarch

%description
Bread is a Btrfs snapshot manager. It provides automatic hourly snapshots
with configurable retention, interactive rollback (CLI and GTK GUI), and
one-level undo. The CLI uses an fdisk-style command loop. The GUI elevates
via pkexec for privileged operations.

%install
mkdir -p %{buildroot}%{_bindir}
install -m 755 bin/bread %{buildroot}%{_bindir}/bread
install -m 755 bin/bread-gui %{buildroot}%{_bindir}/bread-gui

mkdir -p %{buildroot}%{python3_sitelib}/bread/cli
mkdir -p %{buildroot}%{python3_sitelib}/bread/gui
cp -a bread/*.py %{buildroot}%{python3_sitelib}/bread/
cp -a bread/cli/*.py %{buildroot}%{python3_sitelib}/bread/cli/
cp -a bread/gui/*.py %{buildroot}%{python3_sitelib}/bread/gui/

mkdir -p %{buildroot}%{_unitdir}
install -m 644 bread-snapshot.service %{buildroot}%{_unitdir}/
install -m 644 bread-snapshot.timer %{buildroot}%{_unitdir}/

mkdir -p %{buildroot}%{_datadir}/polkit-1/actions
install -m 644 org.bread.policy %{buildroot}%{_datadir}/polkit-1/actions/

mkdir -p %{buildroot}%{_datadir}/applications
install -m 644 bread.desktop %{buildroot}%{_datadir}/applications/

%post
systemctl daemon-reload 2>/dev/null || :

%preun
%systemd_preun bread-snapshot.timer bread-snapshot.service

%postun
%systemd_postun bread-snapshot.timer

%files
%{_bindir}/bread
%{_bindir}/bread-gui
%{python3_sitelib}/bread/
%{_unitdir}/bread-snapshot.service
%{_unitdir}/bread-snapshot.timer
%{_datadir}/polkit-1/actions/org.bread.policy
%{_datadir}/applications/bread.desktop
```

---

### 14. Systemd Units

**`/usr/lib/systemd/system/bread-snapshot.service`**

```ini
[Unit]
Description=Run Bread snapshot engine
Requires=mnt-_bread.mount
After=mnt-_bread.mount

[Service]
Type=oneshot
ExecStart=/usr/bin/bread snapshot
Nice=19
IOSchedulingClass=idle
IOSchedulingPriority=7
```

**`/usr/lib/systemd/system/bread-snapshot.timer`**

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
