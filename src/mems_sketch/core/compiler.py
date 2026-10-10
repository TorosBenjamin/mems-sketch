"""The compiler: turns a project (plain data) into geometry.

Every component build is keyed by a **fingerprint**: a hash of everything the
result depends on (the component's definition, the fingerprints of the
components it references, the parameter values and the process constants).
Built geometry is cached under that key, so unchanged components are never
rebuilt: 50 identical springs are built once, and editing one component only
rebuilds it and the components that contain it.

A :class:`Compiler` owns the cache and can live as long as the application.
Because keys are content hashes, the cache stays correct across edits without
any invalidation. The cache is in memory; the keys are designed so it can later
be persisted (e.g. in SQLite) unchanged.

A :class:`Session` compiles one snapshot of a project. It must not outlive
changes to that project.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from mems_sketch.core.component import Component, Geometry, Params, resolve_params
from mems_sketch.core.imports import ImportedComponent
from mems_sketch.core.levels import Stack
from mems_sketch.core.shapes import Evaluator, NodePath, NodeRecord, Point, Shape
from mems_sketch.core.user_component import UserComponent

if TYPE_CHECKING:
    from mems_sketch.core.project import Project

DEFAULT_CACHE_ENTRIES = 4096
Compiled = tuple[Geometry, dict[str, Point]]  # geometry and declared alignment points


class Compiler:
    def __init__(self, max_entries: int = DEFAULT_CACHE_ENTRIES) -> None:
        self.max_entries = max_entries
        self._cache: OrderedDict[str, Compiled] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def session(self, project: Project) -> Session:
        return Session(self, project)

    def clear(self) -> None:
        self._cache.clear()

    def _get(self, key: str) -> Compiled | None:
        geometry = self._cache.get(key)
        if geometry is None:
            self.misses += 1
            return None
        self.hits += 1
        self._cache.move_to_end(key)
        return geometry

    def _put(self, key: str, compiled: Compiled) -> None:
        self._cache[key] = compiled
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)


class Session:
    """Compiles one snapshot of a project."""

    def __init__(self, compiler: Compiler, project: Project) -> None:
        self.compiler = compiler
        self.project = project
        self.scope = project.process.scope()
        self.stack = Stack.of(project.process.levels, project.process.default_level)
        self._scope_key = json.dumps(self.scope, sort_keys=True) + self.stack.key()
        self._fingerprints: dict[str, str] = {}
        self._components: dict[str, Component] = {}

    # -- lookup ------------------------------------------------------------

    def component(self, name: str, context: str | None = None) -> Component:
        """A buildable (and cached) component for ``name`` as written in the
        component ``context`` (None: a unique name, from outside any component)."""
        qualified = self.project.qualify(name, context)
        if qualified not in self._components:
            found = self.project.definition(qualified)
            if qualified in self.project.imports:
                inner = ImportedComponent(self.project.imports[qualified])
            elif found is None:
                raise KeyError(f"unknown component '{qualified}'")
            else:
                definition, inside = found
                inner = UserComponent(
                    definition, lambda n, c=inside: self.component(n, c), self.scope, self.stack
                )
            self._components[qualified] = _CachedComponent(inner, qualified, self)
        return self._components[qualified]

    def fingerprint(self, qualified: str, _visiting: frozenset[str] = frozenset()) -> str:
        """Hash of a component's definition and, recursively, everything it references."""
        if qualified in self._fingerprints:
            return self._fingerprints[qualified]
        if qualified in _visiting:
            raise ValueError(f"circular component reference involving '{qualified}'")
        found = self.project.definition(qualified)
        if qualified in self.project.imports:
            imported = self.project.imports[qualified]
            layers = sorted(imported.layers.items())
            digest = _hash("import", imported.digest, imported.cell, repr(layers))
        elif found is None:
            raise KeyError(f"unknown component '{qualified}'")
        else:
            definition, context = found
            children = sorted(
                self.fingerprint(self.project.qualify(ref, context), _visiting | {qualified})
                for ref in definition.references()
            )
            digest = _hash("user", definition.model_dump_json(), *children)
        self._fingerprints[qualified] = digest
        return digest

    # -- building ----------------------------------------------------------

    def compile(
        self, qualified: str, inner: Component, params: Params, level: str | None = None
    ) -> Compiled:
        key = _hash(
            self.fingerprint(qualified), params.model_dump_json(), self._scope_key, repr(level)
        )
        cached = self.compiler._get(key)
        if cached is None:
            cached = inner.compile(params, level)
            self.compiler._put(key, cached)
        return cached

    def level(self, component: str) -> str | None:
        """The level of the layer stack a component is on when built on its own
        (its tab, an export): its default level, else the process's."""
        return self.stack.top_level(self.component(component).default_level)

    def variables(self, component: str, params: dict[str, Any] | None = None) -> dict[str, float]:
        """Resolved parameters of a component (defaults unless given) plus ``process.*``."""
        built = self.component(component)
        resolved = resolve_params(built, params or {}, self.scope)
        return {**self.scope, **{k: float(v) for k, v in resolved.model_dump().items()}}

    def points(self, component: str, params: dict[str, Any] | None = None) -> dict[str, Point]:
        """Declared alignment points of a component (defaults unless given)."""
        built = self.component(component)
        return built.compile(
            resolve_params(built, params or {}, self.scope), self.level(component)
        )[1]

    def render(self, component: str, params: dict[str, Any] | None = None) -> Geometry:
        """Merged geometry of a component. The result is a fresh copy the caller may modify."""
        built = self.component(component)
        params = resolve_params(built, params or {}, self.scope)
        return built.compile(params, self.level(component))[0].merged()

    def inspect(
        self, component: str, params: dict[str, Any] | None = None
    ) -> dict[NodePath, NodeRecord]:
        """Evaluate a local component's shape tree and record every node (for the GUI).

        Records are placed in the frame of the list holding each node; see
        :func:`frame_of` to bring them into the component's frame. If the
        evaluation fails, what was evaluated so far is returned.
        """
        found = self.project.definition(component)
        record: dict[NodePath, NodeRecord] = {}
        if found is None:  # a built-in has no shape tree
            return record
        definition, context = found
        evaluator = Evaluator(
            lambda n: self.component(n, context), record, self.level(component), self.stack
        )
        # On failure, what was evaluated so far is still useful to show.
        with contextlib.suppress(Exception):
            evaluator.render(definition.shapes, self.variables(component, params))
        return record

    def render_shapes(
        self, shapes: list[Shape], variables: dict[str, float], context: str | None = None
    ) -> Geometry:
        """Evaluate loose shapes in a scope, e.g. one node of a component being edited
        (``context``: that component, for the names its references use and its level)."""
        level = self.level(context) if context is not None else self.stack.top_level(None)
        evaluator = Evaluator(lambda n: self.component(n, context), level=level, stack=self.stack)
        return evaluator.render(shapes, variables)


class _CachedComponent(Component):
    """Wraps a component so that builds go through the session's cache.

    Cached geometry is shared: callers must treat it as read-only.
    """

    def __init__(self, inner: Component, qualified: str, session: Session) -> None:
        self.inner = inner
        self.type_name = qualified
        self.Params = inner.Params
        self.internal = inner.internal
        self.default_level = inner.default_level
        self._session = session

    def build(self, params: Params) -> Geometry:
        return self.compile(params)[0]

    def points(self, params: Params) -> dict[str, Point]:
        return self.compile(params)[1]

    def compile(self, params: Params, level: str | None = None) -> Compiled:
        return self._session.compile(self.type_name, self.inner, params, level)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _hash(*parts: str) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode())
        digest.update(b"\0")
    return digest.hexdigest()
