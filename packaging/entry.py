"""What the bundled app runs (packaging/mems-sketch.spec): the editor, or with
--version or --self-test a line or a check (mems_sketch.gui.app.main)."""

import sys

from mems_sketch.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
