import os
import subprocess
from gi.repository import Adw, Gtk, GLib
from bread import lib


class BreadWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Bread")
        self.set_default_size(650, 500)

        self.table = []
        self.selected_idx = None

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header bar
        header = Adw.HeaderBar()
        config_btn = Gtk.Button(label="Config")
        config_btn.connect("clicked", self._on_config)
        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.connect("clicked", lambda _: self._refresh())
        header.pack_end(refresh_btn)
        header.pack_end(config_btn)
        main_box.append(header)

        # Snapshot list
        self.scroll = Gtk.ScrolledWindow(vexpand=True)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._on_row_selected)
        self.scroll.set_child(self.listbox)
        main_box.append(self.scroll)

        main_box.append(Gtk.Separator())

        # Bottom bar
        bottom = Gtk.Box(
            spacing=8, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8
        )

        self.timer_btn = Gtk.ToggleButton(label="Snapshots: Off")
        self.timer_btn.connect("toggled", self._on_timer_toggle)
        bottom.append(self.timer_btn)

        bottom.append(Gtk.Box(hexpand=True))  # spacer

        self.rollback_btn = Gtk.Button(label="Rollback")
        self.rollback_btn.set_sensitive(False)
        self.rollback_btn.connect("clicked", self._on_rollback)
        bottom.append(self.rollback_btn)

        self.revert_btn = Gtk.Button(label="Revert Undo")
        self.revert_btn.connect("clicked", self._on_revert)
        bottom.append(self.revert_btn)

        main_box.append(bottom)
        self.set_content(main_box)

        self._refresh()
        self._update_timer_state()

    def _refresh(self):
        self.table = lib.build_snapshot_table()
        self.selected_idx = None
        self.rollback_btn.set_sensitive(False)

        while True:
            row = self.listbox.get_first_child()
            if row is None:
                break
            self.listbox.remove(row)

        # Header row
        hdr = self._make_row("#", "Timestamp", "Subvolumes", bold=True)
        hdr.set_selectable(False)
        self.listbox.append(hdr)

        for i, (ts_str, subvols) in enumerate(self.table):
            self.listbox.append(
                self._make_row(str(i + 1), lib.format_ts(ts_str), ", ".join(subvols))
            )

        GLib.idle_add(self._scroll_to_bottom)

    def _make_row(self, num, ts, subs, bold=False):
        box = Gtk.Box(
            spacing=12, margin_start=8, margin_end=8, margin_top=4, margin_bottom=4
        )

        num_label = Gtk.Label(label=num, xalign=1, width_chars=5)
        ts_label = Gtk.Label(label=ts, xalign=0, width_chars=21)
        sub_label = Gtk.Label(label=subs, xalign=0, hexpand=True)

        if bold:
            for lbl in (num_label, ts_label, sub_label):
                lbl.add_css_class("heading")
        else:
            num_label.add_css_class("dim-label")

        for lbl in (num_label, ts_label):
            lbl.add_css_class("monospace")

        box.append(num_label)
        box.append(ts_label)
        box.append(sub_label)

        row = Gtk.ListBoxRow()
        row.set_child(box)
        return row

    def _scroll_to_bottom(self):
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_upper())
        return False

    def _on_row_selected(self, listbox, row):
        if row is None or row.get_index() == 0:
            self.selected_idx = None
            self.rollback_btn.set_sensitive(False)
            return
        self.selected_idx = row.get_index() - 1  # offset for header row
        self.rollback_btn.set_sensitive(True)

    def _update_timer_state(self):
        try:
            result = subprocess.run(
                ["systemctl", "is-enabled", "bread-snapshot.timer"],
                capture_output=True,
                text=True,
            )
            enabled = result.stdout.strip() == "enabled"
        except Exception:
            enabled = False
        self.timer_btn.handler_block_by_func(self._on_timer_toggle)
        self.timer_btn.set_active(enabled)
        self.timer_btn.set_label(f"Snapshots: {'On' if enabled else 'Off'}")
        self.timer_btn.handler_unblock_by_func(self._on_timer_toggle)

    def _on_timer_toggle(self, btn):
        flag = "--enable-timer" if btn.get_active() else "--disable-timer"
        try:
            subprocess.run(
                ["pkexec", "bread", "snapshot", flag],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            self._show_error(e.stderr.strip() or "Failed to toggle timer.")
        self._update_timer_state()

    def _on_config(self, btn):
        self.get_application().show_wizard()

    def _on_rollback(self, btn):
        if self.selected_idx is None:
            return
        if self.selected_idx < 0 or self.selected_idx >= len(self.table):
            return
        ts_str, subvols = self.table[self.selected_idx]
        num = self.selected_idx + 1

        dialog = RollbackDialog(self, num, ts_str, subvols)
        dialog.present()

    def _on_revert(self, btn):
        if not os.path.exists(lib.OLD_DIR):
            self._show_error("No undo buffer found.")
            return

        try:
            items = sorted(
                i
                for i in os.listdir(lib.OLD_DIR)
                if os.path.isdir(os.path.join(lib.OLD_DIR, i))
            )
        except Exception:
            items = []

        if not items:
            self._show_error("Undo buffer is empty.")
            return

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Undo Last Rollback?",
            body=f"Targets: {', '.join(items)}",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("revert", "Revert")
        dialog.set_response_appearance("revert", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_revert_response)
        dialog.present()

    def _on_revert_response(self, dialog, response):
        if response != "revert":
            return
        try:
            subprocess.run(
                ["pkexec", "bread", "revert", "--yes"],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            self._show_error(e.stderr.strip() or "Revert failed.")
            return
        self._show_reboot_dialog("Revert complete.")

    def _show_error(self, msg):
        dialog = Adw.MessageDialog(transient_for=self, heading="Error", body=msg)
        dialog.add_response("ok", "OK")
        dialog.present()

    def _show_reboot_dialog(self, msg):
        dialog = Adw.MessageDialog(
            transient_for=self, heading=msg, body="Reboot now to apply changes."
        )
        dialog.add_response("later", "Later")
        dialog.add_response("reboot", "Reboot")
        dialog.set_response_appearance("reboot", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_reboot_response)
        dialog.present()

    def _on_reboot_response(self, dialog, response):
        if response == "reboot":
            subprocess.run(["systemctl", "reboot"])
        self._refresh()


class RollbackDialog(Adw.MessageDialog):
    def __init__(self, parent, num, ts_str, subvols):
        super().__init__(
            transient_for=parent,
            heading=f"Rollback to {lib.format_ts(ts_str)}",
            body="Select subvolumes:",
        )

        self.num = num
        self.ts_str = ts_str
        self.subvols = subvols
        self.parent_win = parent

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        self.all_check = Gtk.CheckButton(label="All (Recommended)")
        self.all_check.set_active(True)
        self.all_check.connect("toggled", self._on_all_toggled)
        box.append(self.all_check)

        self.sub_checks = {}
        for sv in subvols:
            cb = Gtk.CheckButton(label=sv)
            cb.set_active(True)
            cb.connect("toggled", self._on_sub_toggled)
            self.sub_checks[sv] = cb
            box.append(cb)

        self.set_extra_child(box)

        self.add_response("cancel", "Cancel")
        self.add_response("rollback", "Rollback")
        self.set_response_appearance("rollback", Adw.ResponseAppearance.DESTRUCTIVE)
        self.connect("response", self._on_response)

    def _on_all_toggled(self, btn):
        active = btn.get_active()
        for cb in self.sub_checks.values():
            cb.handler_block_by_func(self._on_sub_toggled)
            cb.set_active(active)
            cb.handler_unblock_by_func(self._on_sub_toggled)

    def _on_sub_toggled(self, btn):
        all_checked = all(cb.get_active() for cb in self.sub_checks.values())
        self.all_check.handler_block_by_func(self._on_all_toggled)
        self.all_check.set_active(all_checked)
        self.all_check.handler_unblock_by_func(self._on_all_toggled)

    def _on_response(self, dialog, response):
        if response != "rollback":
            return
        selected = [sv for sv, cb in self.sub_checks.items() if cb.get_active()]
        if not selected:
            return

        cmd = [
            "pkexec",
            "bread",
            "rollback",
            "--snapshot",
            str(self.num),
            "--subvols",
            ",".join(selected),
            "--yes",
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            self.parent_win._show_error(e.stderr.strip() or "Rollback failed.")
            return
        self.parent_win._show_reboot_dialog("Rollback complete.")
