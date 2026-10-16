"""Bounded, copy-isolated results for one version of an open ledger."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import fields, is_dataclass
from functools import wraps
from inspect import signature
from sys import getsizeof

import numpy as np
import pandas as pd


def _freeze(value):
    if is_dataclass(value) and not isinstance(value, type):
        return (type(value), tuple((f.name, _freeze(getattr(value, f.name))) for f in fields(value)))
    if isinstance(value, dict):
        return frozenset((_freeze(k), _freeze(v)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(v) for v in value)
    return value


def _size(value):
    if isinstance(value, pd.DataFrame):
        return int(value.memory_usage(index=True, deep=True).sum())
    if isinstance(value, pd.Series):
        return int(value.memory_usage(index=True, deep=True))
    if isinstance(value, np.ndarray):
        return value.nbytes
    if is_dataclass(value) and not isinstance(value, type):
        return getsizeof(value) + sum(_size(getattr(value, f.name)) for f in fields(value))
    if isinstance(value, dict):
        return getsizeof(value) + sum(_size(k) + _size(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return getsizeof(value) + sum(_size(v) for v in value)
    return getsizeof(value)


class ReadCache:
    def __init__(self, entries=32, budget=64 << 20):
        self.entries = entries
        self.budget = budget
        self._values = OrderedDict()
        self._bytes = 0
        self._stamp = None

    def get(self, stamp, key, build, current_stamp):
        if stamp != self._stamp:
            self._values.clear()
            self._bytes = 0
            self._stamp = stamp
        if key in self._values:
            value, size = self._values.pop(key)
            self._values[key] = (value, size)
            return deepcopy(value)
        value = build()
        # A read that also wrote must not publish a result under its old stamp.
        if current_stamp() == stamp:
            size = _size(value)
            if size <= self.budget:
                while self._values and (len(self._values) >= self.entries or self._bytes + size > self.budget):
                    _, (_, old_size) = self._values.popitem(last=False)
                    self._bytes -= old_size
                self._values[key] = (deepcopy(value), size)
                self._bytes += size
        return value


def cached_read(fn):
    """Cache a deterministic Store reader; arguments and results stay isolated.

    The Store owns invalidation, including SQL writes outside its public API,
    other connections, rollback, undo/redo and the date rolling over.
    """
    sig = signature(fn)

    @wraps(fn)
    def read(store, *args, **kwargs):
        bound = sig.bind(store, *args, **kwargs)
        bound.apply_defaults()
        values = list(bound.arguments.values())[1:]
        # An explicitly unseeded simulation must still draw fresh randomness.
        if 'seed' in bound.arguments and bound.arguments['seed'] is None:
            return fn(store, *args, **kwargs)
        key = (fn.__module__, fn.__qualname__, _freeze(values))
        return store.cached(key, lambda: fn(store, *args, **kwargs))
    return read
