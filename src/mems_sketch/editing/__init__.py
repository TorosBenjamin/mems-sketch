"""Editing projects: the backend every frontend (GUI, CLI, scripts) edits through.

session = EditSession.open_project("examples/resonator")
session.set_active("suspension")
session.components.make([((0, 0),), ((0, 1),)], "spring_with_anchor")
session.save()
"""

from mems_sketch.editing.events import Event
from mems_sketch.editing.moves import DragPlan
from mems_sketch.editing.session import EditSession, new_project

__all__ = ["DragPlan", "EditSession", "Event", "new_project"]
