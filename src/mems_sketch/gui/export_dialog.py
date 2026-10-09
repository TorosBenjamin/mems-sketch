"""The export settings dialog, built from an exporter's declared options
(:class:`~mems_sketch.export.base.ExportOption`), so no format needs code of
its own here. The values last used are remembered per format."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mems_sketch.export.base import ExportOption

_HUGE = 1e12  # the range of a number option without limits


def _key(format_name: str, option: ExportOption) -> str:
    return f"export/{format_name}/{option.name}"


def remembered(settings, format_name: str, options: tuple[ExportOption, ...]) -> dict[str, Any]:
    """The values last used for a format's options; defaults where none is
    stored or the stored one no longer fits."""
    values: dict[str, Any] = {}
    for option in options:
        stored = settings.value(_key(format_name, option))
        value = option.default
        if stored is not None:
            try:
                value = option.parse(str(stored))
            except ValueError:
                pass
        values[option.name] = value
    return values


def remember(settings, format_name: str, options: tuple[ExportOption, ...], values) -> None:
    for option in options:
        settings.set_value(_key(format_name, option), str(values[option.name]))


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
        form = QFormLayout()
        for option in options:
            editor = self._editor(option, values.get(option.name, option.default))
            editor.setToolTip(option.help)
            self.editors[option.name] = editor
            if isinstance(editor, QCheckBox):  # the check box carries its label
                form.addRow(editor)
            else:
                form.addRow(option.label, editor)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
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
        line.setPlaceholderText(option.help)
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
