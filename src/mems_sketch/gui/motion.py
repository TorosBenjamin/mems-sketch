"""Short animations, so a change on screen can be followed rather than jumped to.

Everything here is brief (``SHORT`` for panels and toasts, ``VIEW`` for the
canvas gliding to a place) and stops at once when something else starts. The
setting *Reduce motion* turns it off: every animation then jumps to its end,
as it does in the tests.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QObject, QVariantAnimation

SHORT = 140  # ms: a panel folding out, a toast fading in or out
VIEW = 260  # ms: the canvas gliding to a place
PULSE = 900  # ms: a shape picked from a panel glows, then fades

_enabled = True
_held_off = False  # the tests: every animation jumps to its end, whatever the setting


def enabled() -> bool:
    """Whether to animate (False with *Reduce motion*)."""
    return _enabled and not _held_off


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = on


def run(
    owner: QObject,
    step: Callable[[float], None],
    duration: int = SHORT,
    done: Callable[[], None] | None = None,
    easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
    key: str = "",
) -> QVariantAnimation | None:
    """Call ``step(t)`` with ``t`` going from 0 to 1 over ``duration`` ms, then
    ``done()``. Without motion, ``step(1)`` and ``done()`` happen at once.

    An animation with the same ``key`` on ``owner`` is stopped first (it does not
    call its ``done``), so a second click takes over from the first.
    """
    name = f"_motion_{key}"
    stop(owner, key)
    if not enabled() or duration <= 0:
        step(1.0)
        if done is not None:
            done()
        return None
    animation = QVariantAnimation(owner)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setDuration(duration)
    animation.setEasingCurve(easing)
    animation.valueChanged.connect(lambda value: step(float(value)))

    def finished() -> None:
        if getattr(owner, name, None) is animation:
            setattr(owner, name, None)
        if done is not None:
            done()

    animation.finished.connect(finished)
    setattr(owner, name, animation)
    animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


def stop(owner: QObject, key: str = "") -> None:
    """Stop the animation ``key`` on ``owner`` where it is (its ``done`` is not called)."""
    name = f"_motion_{key}"
    animation = getattr(owner, name, None)
    if animation is not None:
        try:
            animation.stop()
        except RuntimeError:
            pass
        setattr(owner, name, None)


def running(owner: QObject, key: str = "") -> bool:
    return getattr(owner, f"_motion_{key}", None) is not None


def mix(a: float, b: float, t: float) -> float:
    return a + (b - a) * t
