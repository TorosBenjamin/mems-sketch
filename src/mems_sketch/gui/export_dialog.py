"""The export settings dialog, built from an exporter's declared options
(:class:`~mems_sketch.export.base.ExportOption`), so no format needs code of
its own here. The values last used are remembered per project and format,
and only where they differ from the project's own (the process's grid), so
another project starts from its own again."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.export.base import ExportOption
from mems_sketch.gui import widgets

_HUGE = 1e12  # the range of a number option without limits


def project_scope(folder: Path | None) -> str:
    """Where a project's remembered values are kept: one place per folder (an
    unsaved project shares one)."""
    if folder is None:
        return "unsaved"
    return hashlib.sha1(str(Path(folder).resolve()).encode()).hexdigest()[:16]


def _key(scope: str, format_name: str, option: ExportOption) -> str:
    return f"export/{scope}/{format_name}/{option.name}"


def _start(option: ExportOption, defaults: dict | None) -> Any:
    return (defaults or {}).get(option.name, option.default)


def remembered(
    settings,
    format_name: str,
    options: tuple[ExportOption, ...],
    defaults: dict | None = None,
    scope: str = "unsaved",
) -> dict[str, Any]:
    """The values last used for a format's options in the project ``scope``;
    where none is stored or the stored one no longer fits, ``defaults`` (e.g.
    the process's grid), else the option's own default."""
    values: dict[str, Any] = {}
    for option in options:
        stored = settings.value(_key(scope, format_name, option))
        value = _start(option, defaults)
        if stored is not None:
            try:
                value = option.parse(str(stored))
            except ValueError:
                pass
        values[option.name] = value
    return values


def remember(
    settings,
    format_name: str,
    options: tuple[ExportOption, ...],
    values,
    defaults: dict | None = None,
    scope: str = "unsaved",
) -> None:
    """Keep the values that differ from where they start (``defaults``, else the
    option's own), for the project ``scope``; forget the others."""
    for option in options:
        key, value = _key(scope, format_name, option), values[option.name]
        if value == _start(option, defaults):
            settings.remove_value(key)
        else:
            settings.set_value(key, str(value))


FIELD_WIDTH = 200  # px


class ExportOptionsDialog(QDialog):
    def __init__(
        self,
        title: str,
        options: tuple[ExportOption, ...],
        values: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Export as {title}")
        self.options = options
        self.editors: dict[str, QWidget] = {}
        form = widgets.form()
        for option in options:
            editor = self._editor(option, values.get(option.name, option.default))
            editor.setToolTip(option.help)
            self.editors[option.name] = editor
            if not isinstance(editor, QCheckBox):
                editor.setMinimumWidth(FIELD_WIDTH)
            shown = widgets.with_help(editor, option.help, stretch=False) if option.help else editor
            if isinstance(editor, QCheckBox):  # the check box carries its label
                form.addRow(shown)
            else:
                form.addRow(option.label, shown)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = widgets.dialog_margins(QVBoxLayout(self))
        layout.addLayout(form)
        layout.addStretch(1)
        layout.addWidget(buttons)

    @staticmethod
    def _editor(option: ExportOption, value: Any) -> QWidget:
        if option.value_type is bool:
            box = QCheckBox(option.label)
            box.setChecked(bool(value))
            return box
        if option.value_type in (int, float):
            spin = QSpinBox() if option.value_type is int else QDoubleSpinBox()
            if isinstance(spin, QDoubleSpinBox):
                spin.setDecimals(option.decimals)
                spin.setSingleStep(10.0**-option.decimals)
            low = option.minimum if option.minimum is not None else -_HUGE
            high = option.maximum if option.maximum is not None else _HUGE
            if option.value_type is int:
                spin.setRange(int(max(low, -(2**31))), int(min(high, 2**31 - 1)))
            else:
                spin.setRange(low, high)
            spin.setValue(value)
            spin.setSuffix(option.suffix)
            return spin
        if option.choices:
            combo = QComboBox()
            combo.addItems(list(option.choices))
            combo.setCurrentText(str(value))
            return combo
        line = QLineEdit(str(value))
        line.setPlaceholderText(option.placeholder)
        return line

    def values(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for option in self.options:
            editor = self.editors[option.name]
            if isinstance(editor, QCheckBox):
                value: Any = editor.isChecked()
            elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
                value = editor.value()
            elif isinstance(editor, QComboBox):
                value = editor.currentText()
            else:
                value = editor.text()
            values[option.name] = option.check(value)
        return values
