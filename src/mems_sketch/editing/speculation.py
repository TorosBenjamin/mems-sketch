"""Building ahead while a value changes (requirement QS-10).

While a whole-number parameter is dragged (a tooth count), the values next to
it are built in the background, so that moving on to one is a cache hit. They
never get in the way:

- **Settings** (QS-11): how many values each way (0: off), how many threads,
  how long a build may take, and what share of the cache they may hold.
- **Only with spare capacity:** each build starts only while a processor of
  the machine is idle (other programs counted), and runs at the lowest
  priority. When the load rises, the next ones are skipped.
- **Only when affordable:** nothing is built ahead of a component that took
  longer than the limit to build last time; large rebuilds are left to the
  real change.
- **Never ahead of real work:** a real change :meth:`Speculator.cancel`\\ s them
  (the engine stops even a build under way), and they build with an engine of
  their own, so they never hold up a real build's turn.
- **Bounded memory:** what they build is the first to go from the cache, and
  holds at most its share of it.
"""

from __future__ import annotations

import threading
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mems_sketch import machine

if TYPE_CHECKING:
    from mems_sketch.editing.session import EditSession
    from mems_sketch.engine import Engine


@dataclass
class AheadSettings:
    """How building ahead may go (the editor's performance settings)."""

    ahead: int = 1  # values each way; 0: off
    threads: int = 1
    max_seconds: float = 1.0  # not for a component that took longer to build


class Speculator:
    """Builds the neighbours of a value being changed, in the background."""

    def __init__(self, session: EditSession, settings: AheadSettings | None = None) -> None:
        self.session = session
        self.settings = settings or AheadSettings()
        self._executor: Executor | None = None
        self._workers = 0
        self._engine: Engine | None = None
        self._stop = threading.Event()
        self.built: list[dict[str, Any]] = []  # the values built ahead, for tests and logs

    def values_around(self, value: float, low: float | None, high: float | None) -> list[int]:
        """The whole numbers next to ``value``, nearest first, within the limits."""
        found = []
        for k in range(1, self.settings.ahead + 1):
            for candidate in (value + k, value - k):
                if (low is None or candidate >= low) and (high is None or candidate <= high):
                    found.append(int(candidate))
        return found

    def around(
        self,
        component: str,
        name: str,
        value: float,
        took: float | None,
        limits: tuple[float | None, float | None] = (None, None),
    ) -> int:
        """Build ``component`` ahead with parameter ``name`` next to ``value`` (the
        other trial values as they are). ``took``: how long it took to build last
        time (None: unknown, so not affordable). Returns how many were queued."""
        self.cancel()
        settings = self.settings
        if settings.ahead <= 0 or took is None or took > settings.max_seconds:
            return 0
        values = self.values_around(value, *limits)
        if not values:
            return 0
        session = self.session
        engine = session.engine.trial(session.project, speculative=True)
        trials = dict(session.trials_for(component))
        self._engine, self._stop = engine, threading.Event()
        executor = self._pool()
        for candidate in values:
            params = {**trials, name: candidate}
            executor.submit(self._build, engine, self._stop, component, params)
        return len(values)

    def cancel(self) -> None:
        """Stop what is being built ahead: a real build comes first."""
        self._stop.set()
        if self._engine is not None:
            self._engine.cancel()
            self._engine = None

    def shutdown(self) -> None:
        self.cancel()
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def _pool(self) -> Executor:
        workers = max(1, self.settings.threads)
        if self._executor is None or workers != self._workers:
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ahead")
            self._workers = workers
        return self._executor

    def _build(self, engine: Engine, stop: threading.Event, component: str, params: dict) -> None:
        if stop.is_set() or not machine.has_spare():
            return
        machine.lower_priority()
        try:
            engine.build_ahead(component, params)
        except Exception:  # noqa: BLE001 - cancelled, or the value does not build: nothing lost
            return
        if not stop.is_set():
            self.built.append(params)
