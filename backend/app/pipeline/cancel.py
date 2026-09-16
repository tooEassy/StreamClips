from __future__ import annotations

import os
import signal
import threading

_lock = threading.Lock()
_cancelled: set[str] = set()
_procs: dict[str, object] = {}


class JobCancelled(Exception):
    pass


def is_cancelled(job_id: str) -> bool:
    with _lock:
        return job_id in _cancelled


def request(job_id: str) -> None:
    with _lock:
        _cancelled.add(job_id)
        proc = _procs.get(job_id)
    _kill_proc(proc)


def clear(job_id: str) -> None:
    with _lock:
        _cancelled.discard(job_id)
        _procs.pop(job_id, None)


def register_proc(job_id: str, proc: object) -> None:
    with _lock:
        _procs[job_id] = proc
        stopped = job_id in _cancelled
    if stopped:
        _kill_proc(proc)


def check(job_id: str) -> None:
    if is_cancelled(job_id):
        raise JobCancelled(job_id)


def _kill_proc(proc: object | None) -> None:
    if proc is None:
        return
    pid = getattr(proc, "pid", None)
    if pid:
        try:
            os.killpg(pid, signal.SIGTERM)
        except Exception:
            pass
    terminate = getattr(proc, "terminate", None)
    try:
        if terminate:
            terminate()
    except Exception:
        pass
    try:
        wait = getattr(proc, "wait", None)
        if wait:
            wait(timeout=2)
        return
    except Exception:
        pass
    if pid:
        try:
            os.killpg(pid, signal.SIGKILL)
        except Exception:
            pass
    kill = getattr(proc, "kill", None)
    try:
        if kill:
            kill()
    except Exception:
        pass
