# bread

Btrfs snapshot manager for Fedora with CLI and GUI.

- Automatic hourly snapshots via systemd timer
- Configurable retention policy
- Interactive rollback with subvolume selection

## Install

```bash
sudo dnf copr enable belsar/bread
sudo dnf install bread
```

## Usage

```bash
bread                    # show recent snapshots
bread list               # show all snapshots (paged)
bread config             # setup wizard
bread snapshot           # create snapshots and prune old ones
bread rollback <N>       # roll back to snapshot N
bread rollback <N> -y    # roll back without confirmation
bread revert             # undo last rollback
bread-gui                # launch the GUI
```

## License

Apache-2.0
