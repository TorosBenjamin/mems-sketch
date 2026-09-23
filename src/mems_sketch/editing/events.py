"""Plain-Python events, used where the GUI once had Qt signals."""

from __future__ import annotations

import sys
from collections.abc import Callable


class Event:
    """Callbacks run in the order they were connected, like a Qt signal with direct
    connections. A callback that raises is reported through ``sys.excepthook`` and
    the others still run, so a failing listener cannot undo what emitted the event.
    """

    def __init__(self) -> None:
        self._slots: list[Callable[..., object]] = []

    def connect(self, slot: Callable[..., object]) -> None:
        self._slots.append(slot)

    def disconnect(self, slot: Callable[..., object]) -> None:
        self._slots.remove(slot)

    def emit(self, *args: object) -> None:
        for slot in list(self._slots):
            try:
                slot(*args)
            except Exception:  # noqa: BLE001 - reported, like Qt does
                sys.excepthook(*sys.exc_info())
