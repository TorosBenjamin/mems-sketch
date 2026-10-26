"""What the machine has and how busy it is: the defaults of the performance
settings (requirement QS-11) and when building ahead may run (QS-10).

Only the standard library: the load average on Linux and macOS, the system's
idle time on Windows. Where neither can be had, the machine counts as busy,
so nothing extra runs.
"""

from __future__ import annotations

import os
import sys
import threading
import time

FALLBACK_MEMORY = 8 << 30  # bytes, when the system does not say
_SAMPLE_S = 1.0  # Windows: idle time is measured over this long, at most once per it

_last: tuple[float, int, int] | None = None  # Windows: (when, idle, total) of the last sample
_idle = 0.0
_lock = threading.Lock()


def cores() -> int:
    """How many logical processors the machine has."""
    return os.cpu_count() or 1


def memory() -> int:
    """The machine's memory in bytes (a fallback when the system does not say)."""
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("load", ctypes.c_ulong),
                    ("total", ctypes.c_ulonglong),
                    ("available", ctypes.c_ulonglong),
                    ("total_page", ctypes.c_ulonglong),
                    ("available_page", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("extended", ctypes.c_ulonglong),
                ]

            status = Status()
            status.length = ctypes.sizeof(Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.total)
            return FALLBACK_MEMORY
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return FALLBACK_MEMORY


def default_cache_mb() -> int:
    """The build cache's default limit: a quarter of the memory, 256 MB to 4 GB."""
    return max(256, min(4096, memory() // 4 >> 20))


def idle_cores() -> float | None:
    """About how many processors are idle now, the whole machine counted (other
    programs too); None when the system does not say."""
    if hasattr(os, "getloadavg"):
        try:
            return max(0.0, cores() - os.getloadavg()[0])
        except OSError:
            return None
    if sys.platform == "win32":
        return _windows_idle() * cores()
    return None


def has_spare(needed: float = 1.0) -> bool:
    """Whether at least ``needed`` processors are idle (False when unknown)."""
    idle = idle_cores()
    return idle is not None and idle >= needed


def lower_priority() -> None:
    """Run the calling thread at the lowest priority the system allows (best effort)."""
    try:
        if sys.platform.startswith("linux"):
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
        elif sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetThreadPriority(kernel32.GetCurrentThread(), -15)  # THREAD_PRIORITY_IDLE
    except (AttributeError, OSError):
        pass


def _windows_idle() -> float:
    """The idle share of the processors since the last sample (Windows)."""
    global _last, _idle
    import ctypes

    with _lock:
        now = time.monotonic()
        if _last is not None and now - _last[0] < _SAMPLE_S:
            return _idle
        idle, kernel, user = (ctypes.c_ulonglong() for _ in range(3))
        if not ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return 0.0
        total = kernel.value + user.value  # kernel time includes the idle time
        if _last is not None and total > _last[2]:
            _idle = (idle.value - _last[1]) / (total - _last[2])
        _last = (now, idle.value, total)
        return _idle
