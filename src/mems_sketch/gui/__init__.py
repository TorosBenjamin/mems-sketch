"""PySide6 desktop GUI. Install with ``pip install mems-sketch[gui]``; run ``mems-sketch``."""

from PySide6.QtCore import QLocale

# Every number shown or typed uses ".", whatever the system's locale, as
# expressions, project files and scripts do, and never groups thousands: a
# value copied from a field must read back the same (#231).
_numbers = QLocale.c()
_numbers.setNumberOptions(QLocale.NumberOption.OmitGroupSeparator)
QLocale.setDefault(_numbers)
