import subprocess
from gi.repository import Adw, Gtk
from bread import lib


class WizardWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Bread - Setup")
        self.set_default_size(400, 300)
        self.set_resizable(False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        box.append(header)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )

        title = Gtk.Label(label="Retention", xalign=0)
        title.add_css_class("title-2")
        content.append(title)

        subtitle = Gtk.Label(label="Number of snapshots to keep per period", xalign=0)
        subtitle.add_css_class("dim-label")
        content.append(subtitle)

        grid = Gtk.Grid(row_spacing=8, column_spacing=12)

        self.spins = {}
        fields = [
            ("Hourly", 0, 0),
            ("Daily", 0, 2),
            ("Weekly", 1, 0),
            ("Monthly", 1, 2),
        ]
        for label_text, row, col in fields:
            label = Gtk.Label(label=f"{label_text}:", xalign=1)
            spin = Gtk.SpinButton.new_with_range(0, 999, 1)
            self.spins[label_text.lower()] = spin
            grid.attach(label, col, row, 1, 1)
            grid.attach(spin, col + 1, row, 1, 1)

        content.append(grid)

        # Pre-fill from existing config
        conf = lib.load_config()
        if conf and "retention" in conf:
            r = conf["retention"]
            for key in ("hourly", "daily", "weekly", "monthly"):
                if key in r:
                    self.spins[key].set_value(r[key])

        btn_box = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=12)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: self.close())
        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save)
        btn_box.append(cancel_btn)
        btn_box.append(save_btn)
        content.append(btn_box)

        box.append(content)
        self.set_content(box)

    def _on_save(self, btn):
        vals = {k: int(s.get_value()) for k, s in self.spins.items()}
        cmd = [
            "pkexec",
            "bread",
            "config",
            "--hourly",
            str(vals["hourly"]),
            "--daily",
            str(vals["daily"]),
            "--weekly",
            str(vals["weekly"]),
            "--monthly",
            str(vals["monthly"]),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Error",
                body=e.stderr.strip() or "Configuration failed.",
            )
            dialog.add_response("ok", "OK")
            dialog.present()
            return

        self.get_application().show_main()
