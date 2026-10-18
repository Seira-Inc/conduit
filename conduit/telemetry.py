"""Per-experiment model usage, measured from provider responses rather than estimated."""
from contextlib import contextmanager
from contextvars import ContextVar

_active = ContextVar("conduit_usage", default=None)


@contextmanager
def measure_models():
    meter = {"model_calls": 0, "usage": []}
    token = _active.set(meter)
    try:
        yield meter
    finally:
        _active.reset(token)


def record_model(usage):
    meter = _active.get()
    if meter is not None:
        meter["model_calls"] += 1
        meter["usage"].append(usage)
