import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import sys
from gi.repository import Adw, Gio
from bread import lib


class BreadApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="org.bread.app", flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )

    def do_activate(self):
        win = self.get_active_window()
        if win:
            win.present()
            return

        conf = lib.load_config()
        if conf is None:
            self.show_wizard()
        else:
            self.show_main()

    def show_wizard(self):
        from bread.gui.wizard import WizardWindow

        win = WizardWindow(self)
        win.present()
        for w in list(self.get_windows()):
            if w is not win:
                w.close()

    def show_main(self):
        from bread.gui.window import BreadWindow

        win = BreadWindow(self)
        win.present()
        for w in list(self.get_windows()):
            if w is not win:
                w.close()


def main():
    BreadApp().run(sys.argv)
