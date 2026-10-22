"""Per-turn budget, isolated across concurrent discussion workers."""
import time
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps


@dataclass
class TurnBudget:
    deadline: float
    attempts_left: int = 2  # primary and one fallback, shared across protocols

    def claim(self):
        if self.attempts_left <= 0 or self.remaining() <= 0:
            return False
        self.attempts_left -= 1
        return True

    def remaining(self):
        return max(0.0, self.deadline - time.monotonic())


_current = ContextVar('turn_budget', default=None)


def remaining_timeout(configured):
    budget = _current.get()
    return min(configured, budget.remaining()) if budget else configured


def budgeted_turn(timeout):
    """Create a fresh budget per invocation, resetting it even on exceptions."""
    def decorate(method):
        @wraps(method)
        def wrapped(*args, **kwargs):
            seconds = timeout(*args, **kwargs) if callable(timeout) else timeout
            token = _current.set(TurnBudget(time.monotonic() + seconds))
            try:
                return method(*args, **kwargs)
            finally:
                _current.reset(token)
        return wrapped
    return decorate


def claim_attempt():
    budget = _current.get()
    return budget is None or budget.claim()
