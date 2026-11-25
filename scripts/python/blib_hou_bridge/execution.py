"""Cooperative checkpoints shared by host commands and the task scheduler."""
from __future__ import annotations
from contextvars import ContextVar

_current = ContextVar("blib_task_context", default=None)


class TaskCancelled(RuntimeError):
    pass


class TaskRejected(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def current():
    return _current.get()


def checkpoint():
    context = current()
    if context is not None:
        context.checkpoint()


def start_effects():
    context = current()
    if context is not None:
        context.start_effects()


def progress(completed, total, partial=None):
    context = current()
    if context is not None:
        context.progress(completed, total, partial)
