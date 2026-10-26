"""A value field's suggestions (#232): they follow the name at the cursor, show
names and values in two columns with process constants marked, and are chosen
from the keyboard."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from mems_sketch.gui.value_edit import (
    MAKE,
    USE_VALUE,
    ValueEdit,
    ranked,
    suggestions_list,
    suggestions_shown,
)

SCOPE = {"plate": 162.5, "pitch": 10.0, "w_finger": 2.0, "process.undercut": 3.0}


@pytest.fixture
def field(qtbot):
    edit = ValueEdit(5, scope=SCOPE, parameters=["plate", "pitch", "w_finger"])
    edit.make_parameter.connect(lambda value: made.append(value))
    made: list = []
    edit.made = made
    qtbot.addWidget(edit)
    edit.resize(200, 28)
    edit.show()
    qtbot.waitExposed(edit)
    edit.setFocus()
    return edit


def test_names_starting_with_the_word_come_first_then_those_containing_it():
    names = ["plate", "w_pitch", "pitch", "mass.center.x", "process.pin"]
    assert ranked(names, "pi") == ["pitch", "process.pin", "w_pitch"]
    assert ranked(names, "CENTER") == ["mass.center.x"]
    assert ranked(names, "") == names
    assert ranked(names, "zz") == []


def test_typing_offers_what_matches_the_word_at_the_cursor(field):
    field.setText("")
    QTest.keyClicks(field, "2 * p")
    assert field.suggestion_keys() == ["plate", "pitch", "process.undercut"]
    QTest.keyClicks(field, "i")
    assert field.suggestion_keys() == ["pitch"]


def test_nothing_is_offered_while_a_number_is_typed(field):
    field.setText("")
    QTest.keyClicks(field, "11")
    assert not suggestions_shown(field._completer)


def test_rows_have_aligned_values_and_constants_are_marked(field):
    rows = {r.key: r for r in field.suggestions("")}
    assert (rows["plate"].label, rows["plate"].value, rows["plate"].tag) == ("plate", "162.5", "")
    assert rows["process.undercut"].tag == "process"  # in the same list, not a submenu
    assert all("  =" not in r.label for r in rows.values())


def test_arrows_and_tab_insert_at_the_cursor(field):
    field.setText("")
    QTest.keyClicks(field, "plate + w")
    popup = suggestions_list(field._completer)
    assert popup.isVisible()
    QTest.keyClick(popup, Qt.Key.Key_Tab)
    assert field.text() == "plate + w_finger" and not popup.isVisible()
    field.setText("")
    QTest.keyClicks(field, "p")
    QTest.keyClick(popup, Qt.Key.Key_Down)
    QTest.keyClick(popup, Qt.Key.Key_Return)
    assert field.text() == "pitch"


def test_escape_closes_the_list_and_keeps_the_text(field):
    field.setText("")
    QTest.keyClicks(field, "pl")
    QTest.keyClick(suggestions_list(field._completer), Qt.Key.Key_Escape)
    assert not suggestions_shown(field._completer) and field.text() == "pl"


def test_the_button_on_a_number_offers_to_make_a_parameter_first(field):
    field.clearFocus()
    field.show_suggestions()
    keys = field.suggestion_keys()
    assert keys[0] == MAKE and "pitch" in keys
    field._chosen(MAKE)
    assert field.made == [5.0]


def test_the_button_on_an_expression_ranks_by_its_name_and_offers_the_value(field):
    field.setText("2 * pitch")
    field.clearFocus()
    field.show_suggestions()
    keys = field.suggestion_keys()
    assert keys[0] == "pitch" and keys[-2:] == [MAKE, USE_VALUE]
    applied = []
    field.returnPressed.connect(lambda: applied.append(field.text()))
    field._chosen("plate")  # replaces the name at the end, and applies
    assert field.text() == "2 * plate" and applied == ["2 * plate"]
