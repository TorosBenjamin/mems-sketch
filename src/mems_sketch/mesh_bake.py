"""Baking a mesh in the background (#351): gmsh runs in a process of its own, so
the window stays usable and *Cancel* stops it at once (a process can be ended
cleanly; a thread in gmsh cannot be stopped).

A :class:`Bake` starts the process; :meth:`Bake.poll` (called on a timer) hands
back what it said since: the steps it started (``("step", text)``), then
``("done", mesh)`` or ``("error", text)``.
"""

from __future__ import annotations

import multiprocessing
import time
from dataclasses import dataclass, field

from mems_sketch.core.component import Geometry
from mems_sketch.core.mesh_settings import MeshSettings


@dataclass
class BakeJob:
    """What :func:`mems_sketch.meshing.generate` is called with."""

    geometry: Geometry
    settings: MeshSettings
    variables: dict[str, float] = field(default_factory=dict)
    heights: dict[str, tuple[float, float]] | None = None
    shapes: dict[str, Geometry] = field(default_factory=dict)


def _run(job: BakeJob, connection) -> None:
    """In the bake's process: mesh, saying each step, then the mesh or the error."""
    from mems_sketch import meshing

    try:
        mesh = meshing.generate(
            job.geometry,
            job.settings,
            job.variables,
            heights=job.heights,
            shapes=job.shapes,
            progress=lambda step: connection.send(("step", step)),
        )
    except Exception as exc:  # noqa: BLE001 - said in the window
        connection.send(("error", str(exc)))
    else:
        connection.send(("done", mesh))
    finally:
        connection.close()


class Bake:
    """One mesh being made in another process."""

    def __init__(self, job: BakeJob) -> None:
        context = multiprocessing.get_context("spawn")  # not a fork of a running Qt app
        self._connection, child = context.Pipe(duplex=False)
        self._process = context.Process(target=_run, args=(job, child), daemon=True)
        self._process.start()
        child.close()  # (the process has its own end now)
        self.started = time.monotonic()
        self.finished = False

    @property
    def seconds(self) -> float:
        return time.monotonic() - self.started

    def poll(self) -> list[tuple[str, object]]:
        """What the bake said since the last poll; it is finished once it said
        ``done`` or ``error`` (or its process ended without a word)."""
        said: list[tuple[str, object]] = []
        if self.finished:
            return said
        try:
            while self._connection.poll():
                message = self._connection.recv()
                said.append(message)
                if message[0] in ("done", "error"):
                    self._finish()
                    return said
        except (EOFError, OSError):
            pass
        if not self._process.is_alive() and not self._connection.poll():
            said.append(("error", f"the mesher stopped (exit code {self._process.exitcode})"))
            self._finish()
        return said

    def cancel(self) -> None:
        """Stop it now."""
        if not self.finished:
            self._process.terminate()
            self._finish()

    def _finish(self) -> None:
        self.finished = True
        self._process.join(timeout=5)
        self._connection.close()
