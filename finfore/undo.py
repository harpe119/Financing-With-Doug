"""Undo/redo for everything that touches the database.

The unit of undo is the whole database file. That sounds heavy-handed until you
look at what a single user action actually does: an import writes transactions,
saves a column mapping, rewrites categories, upserts recurring patterns, links
loans and records a checkpoint — six tables, in one gesture the user thinks of
as "I imported my CSV". Journalling those individually would mean six presses of
Ctrl-Z to undo one thing, and any table this app grows later would silently not
be covered.

So a snapshot is taken *before* an action and kept compressed in memory. The
files involved are small — a ledger with 10,000 transactions is a couple of
megabytes, and zlib takes roughly 80% of that back — and the stack is bounded on
both depth and total bytes, so the memory cost stays in the tens of megabytes
even for someone who imports all afternoon.

Two things make this safe rather than merely convenient:

* **Snapshots are taken at action boundaries, not statement boundaries.**
  :meth:`group` nests, so the outermost caller decides what "one change" means.
  Every mutating :class:`~finfore.store.Store` method opens a group of its own,
  which means anything called on its own is undoable without the caller doing
  anything, while a composite action wrapped in one explicit group collapses to
  a single entry.

* **An action that changed nothing leaves no entry.** The file is compared
  before and after, so a dialog that was opened and cancelled, or a no-op edit
  that re-saved identical values, does not push a step that appears to do
  something and does not. Ctrl-Z that visibly does nothing is worse than no
  Ctrl-Z at all — the user presses it again, and again, past the change they
  actually wanted back.

Restoring closes the connection, rewrites the file and reopens, rather than
handing back a new ``Store``. Pages hold a reference to the store object, so
replacing its contents in place is what keeps them pointed at live data.
"""
from __future__ import annotations

import zlib
from contextlib import contextmanager
from dataclasses import dataclass

DEPTH = 25              # steps kept, oldest dropped first
BUDGET = 128 << 20      # ceiling on total compressed bytes held


@dataclass(frozen=True)
class Step:
    """One reversible action: what it was called, and the state before it."""
    label: str
    blob: bytes

    @property
    def nbytes(self) -> int:
        return len(self.blob)


class UndoStack:
    """Snapshot-based undo/redo bound to one open :class:`Store`.

    Not thread-safe, and deliberately so: it is driven from Qt slots on the GUI
    thread, and a lock here would give the false impression that a background
    import could safely write through it.
    """

    def __init__(self, store, depth: int = DEPTH, budget: int = BUDGET):
        self._store = store
        self._depth_limit = max(1, int(depth))
        self._budget = int(budget)
        self._undo: list[Step] = []
        self._redo: list[Step] = []
        self._open = 0              # nesting level of group()
        self._suspended = 0

    # ---------------- recording ----------------
    @contextmanager
    def group(self, label: str):
        """Treat everything inside as one undoable step called ``label``.

        Nesting is by design: only the outermost group records, so wrapping a
        composite action collapses the individual store calls inside it into one
        entry. The inner groups still run — they just don't snapshot.
        """
        if self._suspended or self._open:
            self._open += 1
            try:
                yield
            finally:
                self._open -= 1
            return

        before = self._read()
        self._open = 1
        try:
            yield
        finally:
            self._open = 0
            # In a finally rather than after a clean return: an action that
            # raised halfway may still have committed some of its work, and
            # that is precisely the state a user wants a way back out of.
            try:
                changed = self._read() != before
            except OSError:
                changed = False
            if changed:
                self._push(Step(label, zlib.compress(before, 1)))

    @contextmanager
    def suspended(self):
        """Record nothing inside. For work the user did not ask for by hand —
        building a demo database, or migrating one — where the steps are noise
        and the file can be large."""
        self._suspended += 1
        try:
            yield
        finally:
            self._suspended -= 1

    def _push(self, step: Step) -> None:
        self._undo.append(step)
        self._redo.clear()          # a new action forks the timeline
        self._trim()

    def _trim(self) -> None:
        while len(self._undo) > self._depth_limit:
            self._undo.pop(0)
        while len(self._undo) > 1 and self._nbytes() > self._budget:
            self._undo.pop(0)

    def _nbytes(self) -> int:
        return sum(s.nbytes for s in self._undo) + sum(s.nbytes for s in self._redo)

    # ---------------- state ----------------
    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    @property
    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    # ---------------- moving ----------------
    def undo(self) -> str | None:
        """Step back one action. Returns its label, or None if there is none."""
        return self._move(self._undo, self._redo)

    def redo(self) -> str | None:
        """Step forward again. Returns the label, or None if there is none."""
        return self._move(self._redo, self._undo)

    def _move(self, source: list[Step], sink: list[Step]) -> str | None:
        if not source:
            return None
        step = source.pop()
        # The state being left becomes the way back, under the same label: the
        # label names the action, and the action is what separates the two
        # states regardless of which direction it is being crossed in.
        sink.append(Step(step.label, zlib.compress(self._read(), 1)))
        self._restore(zlib.decompress(step.blob))
        self._trim()
        return step.label

    # ---------------- the file itself ----------------
    def _read(self) -> bytes:
        """The database as bytes, at a point where those bytes are complete.

        The commit matters. SQLite keeps an open transaction's changes out of
        the main file (they live in the rollback journal until commit), so
        reading mid-transaction captures a state the caller never sees. Every
        Store method commits before returning, so this is a no-op in practice —
        it is here so that a future one that forgets fails loudly at its own
        snapshot rather than quietly recording the wrong bytes.
        """
        self._store.con.commit()
        return self._store.path.read_bytes()

    def _restore(self, blob: bytes) -> None:
        self._store._reopen_with(blob)
