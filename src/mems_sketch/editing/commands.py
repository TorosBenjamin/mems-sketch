"""The base class of an :class:`~mems_sketch.editing.session.EditSession`'s command groups."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mems_sketch.editing.session import EditSession


class Commands:
    """A group of edit commands; each runs as one transaction (``self.session.edit``)."""

    def __init__(self, session: EditSession) -> None:
        self.session = session
