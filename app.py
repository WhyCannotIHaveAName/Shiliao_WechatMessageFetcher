"""Shiliao Windows desktop GUI."""
import datetime as dt
import os
import sys
from pathlib import Path
from offline_guard import enforce_offline
enforce_offline()
# Source builds use the official Tcl/Tk scripts vendored with this project.
if not getattr(sys,'frozen',False):
    vendor = Path(__file__).resolve().parent/'vendor'
    if (vendor/'tcl8.6'/'init.tcl').is_file():
        os.chdir(Path(__file__).resolve().parent)
        os.environ['TCL_LIBRARY'] = 'vendor/tcl8.6'
        os.environ['TK_LIBRARY'] = 'vendor/tk8.6'
else:
    os.chdir(sys._MEIPASS)
    os.environ['TCL_LIBRARY'] = '_tcl_data'
    os.environ['TK_LIBRARY'] = '_tk_data'
from desktop_ui import App


def self_test(report):
    import io
    import json
    import unittest
    import test_export

    window = App(scaling=2.67)
    window.attributes('-alpha', 0)
    try:
        test_export.check_desktop(window)
    finally:
        window.destroy()
    output = io.StringIO()
    result = unittest.TextTestRunner(stream=output).run(unittest.defaultTestLoader.loadTestsFromModule(test_export))
    Path(report).write_text(json.dumps({'passed': result.wasSuccessful(), 'tests': result.testsRun,
                                       'details': output.getvalue()}, ensure_ascii=False, indent=2),
                            encoding='utf8')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        sys.exit(self_test(sys.argv[2]))
    else:
        App().mainloop()
