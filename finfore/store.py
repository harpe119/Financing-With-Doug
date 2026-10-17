"""SQLite persistence.

Single-file database. Schema is created on first open. Recurring-item
re-detection merges by key and preserves user overrides (active flag, kind,
loan link) so refreshing detection never discards manual curation.

Every method that writes is marked ``@undoable`` with the name the user will
see on the Undo menu item. The marker only costs anything once
:meth:`Store.enable_undo` has been called — the GUI does, the test suite and
the demo builder do not — and it is what keeps undo coverage from drifting: a
new mutator either carries a label or is visibly the odd one out.
"""
from __future__ import annotations

import functools
import json
import math
import os
import sqlite3
import tempfile
from contextlib import closing, nullcontext
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .models import Account, AccountType, Goal, OneOffEvent, RecurringItem
from .cache import ReadCache, cached_read


def undoable(label: str):
    """Make one store method a single undo step named ``label``.

    Groups nest, so a method that calls another marked method still produces
    one entry, and a caller that wraps several in :meth:`Store.undo_group`
    collapses the lot into one — which is what makes "Undo Import CSV" one
    press rather than six.
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            with self.undo_group(label):
                return fn(self, *args, **kwargs)
        wrapper.undo_label = label
        return wrapper
    return deco


def _numeric(df: pd.DataFrame, **columns: str) -> pd.DataFrame:
    """Force the named columns to a real numeric dtype, in place.

    pandas infers a SQL result's dtypes from the rows it got back, so a query
    that returns **no rows** hands back `object` columns — `amount` included.
    Almost everything downstream then works by accident: `sum()` still adds up
    because Python floats add, and comparisons still compare, so the frame
    passes through the whole app looking fine.

    numpy is less forgiving. `np.isfinite` refuses an object array outright,
    which is how a brand-new ledger — one account added, nothing imported yet —
    crashed the dashboard: the empty ledger produced an object `amount`,
    `cash_history` carried the dtype into its output, `float + object` gave an
    object net-worth series, and pyqtgraph raised *ufunc 'isfinite' not
    supported* from inside a Qt callback, where an exception aborts the
    process rather than politely returning.

    Declaring the dtypes here rather than repairing them at each consumer is
    the fix: the schema knows what these columns are, and a frame leaving the
    store should already be what it claims to be.
    """
    for col, dtype in columns.items():
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        try:
            df[col] = s.astype(dtype)
        except (ValueError, TypeError):
            # A null in a column the schema declares NOT NULL means a
            # hand-edited or half-migrated file. Keeping the float version is
            # still an improvement on `object`, and is not worth refusing to
            # open somebody's ledger over.
            df[col] = s
    return df

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    balance REAL NOT NULL DEFAULT 0,
    balance_date TEXT NOT NULL,
    apr REAL NOT NULL DEFAULT 0,
    annual_return REAL NOT NULL DEFAULT 0.07,
    volatility REAL NOT NULL DEFAULT 0.15,
    monthly_contribution REAL NOT NULL DEFAULT 0,
    monthly_payment REAL NOT NULL DEFAULT 0,
    payment_day INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    balance REAL NOT NULL,
    UNIQUE(account_id, date)
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    category TEXT NOT NULL DEFAULT 'Misc',
    import_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE TABLE IF NOT EXISTS business_reimbursements (
    transaction_id INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
    amount REAL NOT NULL CHECK(amount >= 0)
);
CREATE TABLE IF NOT EXISTS recurring (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    cadence TEXT NOT NULL,
    amount REAL NOT NULL,
    last_date TEXT NOT NULL,
    days_of_month TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0,
    amount_cv REAL NOT NULL DEFAULT 0,
    n_seen INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    kind TEXT NOT NULL DEFAULT 'bill',
    linked_account_id INTEGER
);
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT 'cash',
    saved_amount REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goal_activity (
    id INTEGER PRIMARY KEY,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    when_date TEXT NOT NULL,
    action TEXT NOT NULL,
    amount REAL NOT NULL,
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    transaction_hash TEXT,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_goal_activity_goal ON goal_activity(goal_id);
CREATE INDEX IF NOT EXISTS idx_goal_activity_hash ON goal_activity(transaction_hash);
-- Rows the user has deliberately removed as double-postings. Without this a
-- later "Update from CSV" would import them straight back: the row hash is
-- what makes imports idempotent, so deleting the row alone just frees the hash
-- to be used again. Suppressing the hash makes the removal stick.
CREATE TABLE IF NOT EXISTS suppressed (
    import_hash TEXT PRIMARY KEY,
    account_id INTEGER,
    note TEXT NOT NULL DEFAULT '',
    removed_on TEXT NOT NULL,
    date TEXT,
    description TEXT,
    amount REAL
);
-- One row per import: the state of things, and the projection being made at
-- that moment. The forecast is stored rather than recomputed because a
-- projection refitted later with the outcome already in the data is not a
-- prediction, and the score is stored because it is recency-weighted and
-- therefore cannot be reconstructed for a past date once newer months exist.
CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY,
    when_date TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    n_transactions INTEGER NOT NULL DEFAULT 0,
    latest_tx TEXT,
    networth REAL NOT NULL DEFAULT 0,
    cash REAL NOT NULL DEFAULT 0,
    investments REAL NOT NULL DEFAULT 0,
    debt REAL NOT NULL DEFAULT 0,
    score REAL,
    grade TEXT NOT NULL DEFAULT '',
    fesr REAL,
    savings_rate REAL,
    income REAL NOT NULL DEFAULT 0,
    spending REAL NOT NULL DEFAULT 0,
    essential REAL NOT NULL DEFAULT 0,
    frivolous REAL NOT NULL DEFAULT 0,
    unknown REAL NOT NULL DEFAULT 0,
    forecast TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_when ON checkpoints(when_date);
-- Every manual re-categorisation the user makes. This is the labelled corpus
-- a classifier will train on later; it is deliberately append-only and keeps
-- the raw description (not just the normalised key) so richer features can be
-- derived without re-collecting data.
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL,
    when_date TEXT NOT NULL,
    amount REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    repeat TEXT NOT NULL DEFAULT 'once',
    until_date TEXT
);
CREATE TABLE IF NOT EXISTS category_feedback (
    id INTEGER PRIMARY KEY,
    norm_key TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    account_type TEXT NOT NULL DEFAULT '',
    old_category TEXT NOT NULL DEFAULT '',
    new_category TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_key ON category_feedback(norm_key);
"""


def _d(s) -> date:
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.undo = None                  # set by enable_undo(); see undo.py
        self._connect()
        self.con.executescript(_SCHEMA)
        for table, fields in {
            'transactions': [('category_source', "TEXT NOT NULL DEFAULT 'legacy'"),
                ('category_reason', "TEXT NOT NULL DEFAULT ''"),
                ('category_reviewed', 'INTEGER NOT NULL DEFAULT 0'), ('category_reviewed_at', 'TEXT'),
                ('transaction_date', 'TEXT'), ('posting_date', 'TEXT'),
                ('source_reference', "TEXT NOT NULL DEFAULT ''"),
                ('date_basis', "TEXT NOT NULL DEFAULT 'legacy'")],
            'accounts': [('allow_overdraft', 'INTEGER NOT NULL DEFAULT 0')],
            'category_feedback': [('remember', 'INTEGER NOT NULL DEFAULT 1'),
                ('transaction_id', 'INTEGER REFERENCES transactions(id) ON DELETE SET NULL'),
                ('transaction_date', 'TEXT')],
        }.items():
            existing = {r['name'] for r in self.con.execute(f'PRAGMA table_info({table})')}
            for name, kind in fields:
                if name not in existing:
                    self.con.execute(f'ALTER TABLE {table} ADD COLUMN {name} {kind}')
        goal_columns = {r["name"] for r in self.con.execute("PRAGMA table_info(goals)")}
        if "saved_amount" not in goal_columns:
            self.con.execute("ALTER TABLE goals ADD COLUMN saved_amount REAL NOT NULL DEFAULT 0")
        if "spent_amount" not in goal_columns:
            self.con.execute("ALTER TABLE goals ADD COLUMN spent_amount REAL NOT NULL DEFAULT 0")
        event_columns = {r["name"] for r in self.con.execute("PRAGMA table_info(events)")}
        for name, kind in (("repeat", "TEXT NOT NULL DEFAULT 'once'"), ("until_date", "TEXT")):
            if name not in event_columns:
                self.con.execute(f"ALTER TABLE events ADD COLUMN {name} {kind}")
        # Retain removed-row identities even if a later export changes case or
        # spacing. Existing tombstones still work through their original hash.
        columns = {r["name"] for r in self.con.execute("PRAGMA table_info(suppressed)")}
        for name, kind in (("date", "TEXT"), ("description", "TEXT"), ("amount", "REAL")):
            if name not in columns:
                self.con.execute(f"ALTER TABLE suppressed ADD COLUMN {name} {kind}")
        self.con.commit()

    def _connect(self) -> None:
        self.con = sqlite3.connect(str(self.path))
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self._read_cache = ReadCache()

    def read_stamp(self):
        """Identify the current data, even after direct SQL or another writer.

        total_changes includes our writes; data_version includes commits from
        other connections. Transaction state distinguishes a cached uncommitted
        read from the state after rollback. Reopening resets the entire cache.
        Date-sensitive analyses expire at midnight as well.
        """
        return (self.con.total_changes, self.con.in_transaction,
                self.con.execute('PRAGMA data_version').fetchone()[0],
                self.con.execute('PRAGMA schema_version').fetchone()[0], date.today())

    def cached(self, key, build):
        # A SAVEPOINT rollback does not change total_changes or transaction
        # state. Never retain reads from an open transaction.
        if self.con.in_transaction:
            return build()
        return self._read_cache.get(self.read_stamp(), key, build, self.read_stamp)

    def backup_to(self, path: str | Path) -> Path:
        """Save a consistent SQLite snapshot, atomically replacing the target.

        SQLite's backup API includes committed WAL contents. Copying the open
        database file alone could silently omit those transactions.
        """
        target = Path(path).expanduser().resolve()
        if target == self.path.resolve() or (
                target.exists() and os.path.samefile(target, self.path)):
            raise ValueError("Choose a different file from the open database.")
        # Replacing a WAL database while sidecars exist could corrupt it when
        # another connection next opens or checkpoints it.
        if any(Path(str(target) + suffix).exists()
               for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("This destination may be open. Choose a new backup file.")
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                    dir=target.parent, prefix=".doug-backup-", suffix=".db",
                    delete=False) as temp:
                temp_path = Path(temp.name)
            with closing(sqlite3.connect(temp_path)) as backup:
                self.con.backup(backup)
                if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("The backup failed its integrity check.")
            os.replace(temp_path, target)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        return target

    # ---------------- undo ----------------
    def enable_undo(self, **kwargs):
        """Start recording undo steps, and return the stack.

        Off by default. The GUI turns it on; the demo builder, the test suite
        and any batch import do not want to be snapshotting a database file
        before each of several thousand writes.
        """
        from .undo import UndoStack
        if self.undo is None:
            self.undo = UndoStack(self, **kwargs)
        return self.undo

    def undo_group(self, label: str):
        """Context manager: everything inside is one undo step called ``label``.

        A no-op when undo is off, which is what lets the store methods carry
        their markers unconditionally.
        """
        return self.undo.group(label) if self.undo is not None else nullcontext()

    def undo_suspended(self):
        """Context manager: record nothing that happens inside."""
        return self.undo.suspended() if self.undo is not None else nullcontext()

    def _reopen_with(self, blob: bytes) -> None:
        """Replace the database file wholesale and reopen on it.

        The ``Store`` object survives — every page in the window holds a
        reference to this one — so undo swaps the contents underneath rather
        than handing anybody a new store to go and find.
        """
        self.con.close()
        self.path.write_bytes(blob)
        self._connect()

    # ---------------- accounts ----------------
    @undoable("Add account")
    def add_account(self, a: Account) -> int:
        cur = self.con.execute(
            """INSERT INTO accounts (name,type,balance,balance_date,apr,annual_return,
               volatility,monthly_contribution,monthly_payment,payment_day,allow_overdraft)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (a.name, a.type.value, a.balance, a.balance_date.isoformat(), a.apr,
             a.annual_return, a.volatility, a.monthly_contribution,
             a.monthly_payment, a.payment_day, int(a.allow_overdraft)),
        )
        self.con.commit()
        a.id = cur.lastrowid
        return a.id

    @undoable("Edit account")
    def update_account(self, a: Account) -> None:
        previous = self.account(a.id)
        changed_balance = previous is not None and (
            previous.balance != a.balance or previous.balance_date != a.balance_date)
        with self.con:
            self.con.execute(
                """UPDATE accounts SET name=?,type=?,balance=?,balance_date=?,apr=?,
                   annual_return=?,volatility=?,monthly_contribution=?,
                   monthly_payment=?,payment_day=?,allow_overdraft=? WHERE id=?""",
                (a.name, a.type.value, a.balance, a.balance_date.isoformat(), a.apr,
                 a.annual_return, a.volatility, a.monthly_contribution,
                 a.monthly_payment, a.payment_day, int(a.allow_overdraft), a.id),
            )
            if changed_balance:
                # A same-day snapshot otherwise overrides this explicit edit
                # in both cash reconstruction and investment/loan history.
                # Editing terms alone must not replace a snapshot's value.
                self.con.execute('UPDATE snapshots SET balance=? WHERE account_id=? AND date=?',
                                 (a.balance, a.id, a.balance_date.isoformat()))

    @undoable("Delete account")
    def delete_account(self, account_id: int) -> None:
        self.con.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        self.con.commit()

    def accounts(self) -> list[Account]:
        rows = self.con.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return [self._row_to_account(r) for r in rows]

    def account(self, account_id: int) -> Account | None:
        r = self.con.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        return self._row_to_account(r) if r else None

    @staticmethod
    def _row_to_account(r) -> Account:
        return Account(
            id=r["id"], name=r["name"], type=AccountType(r["type"]),
            balance=r["balance"], balance_date=_d(r["balance_date"]),
            apr=r["apr"], annual_return=r["annual_return"], volatility=r["volatility"],
            monthly_contribution=r["monthly_contribution"],
            monthly_payment=r["monthly_payment"], payment_day=r["payment_day"],
            allow_overdraft=bool(r['allow_overdraft']),
        )

    # ---------------- snapshots ----------------
    @undoable("Add balance snapshot")
    def add_snapshot(self, account_id: int, d: date, balance: float) -> None:
        self.con.execute(
            """INSERT INTO snapshots (account_id, date, balance) VALUES (?,?,?)
               ON CONFLICT(account_id, date) DO UPDATE SET balance=excluded.balance""",
            (account_id, d.isoformat(), balance),
        )
        self.con.commit()

    def snapshots(self, account_id: int) -> pd.DataFrame:
        df = pd.read_sql_query(
            "SELECT id, date, balance FROM snapshots WHERE account_id=? ORDER BY date",
            self.con, params=(account_id,), parse_dates=["date"],
        )
        return _numeric(df, id="int64", balance="float64")

    @undoable("Edit balance snapshot")
    def update_snapshot(self, snapshot_id: int, d: date, balance: float) -> None:
        """Change a snapshot's date and/or balance. Raises ValueError if
        another snapshot already exists on the target date for that account."""
        try:
            self.con.execute(
                "UPDATE snapshots SET date=?, balance=? WHERE id=?",
                (d.isoformat(), balance, snapshot_id),
            )
        except sqlite3.IntegrityError:
            self.con.rollback()
            raise ValueError(
                "This account already has a snapshot on that date. "
                "Delete or edit the other one first.")
        self.con.commit()

    @undoable("Delete balance snapshot")
    def delete_snapshot(self, snapshot_id: int) -> None:
        self.con.execute("DELETE FROM snapshots WHERE id=?", (snapshot_id,))
        self.con.commit()

    @undoable("Import balance history")
    def import_snapshots(self, account_id: int,
                         records: list[tuple[date, float]]) -> tuple[int, int]:
        """Bulk upsert (date, balance) snapshots for an account. Returns
        (added, updated). Advances the account's current balance/date to the
        newest imported snapshot if it is at least as recent as the current
        balance date."""
        snaps = self.snapshots(account_id)
        existing = set(snaps["date"].dt.date) if len(snaps) else set()
        added = updated = 0
        newest: tuple[date, float] | None = None
        for d, bal in records:
            if d in existing:
                updated += 1
            else:
                added += 1
                existing.add(d)
            self.con.execute(
                """INSERT INTO snapshots (account_id, date, balance) VALUES (?,?,?)
                   ON CONFLICT(account_id, date) DO UPDATE SET balance=excluded.balance""",
                (account_id, d.isoformat(), float(bal)))
            if newest is None or d >= newest[0]:
                newest = (d, float(bal))
        # advance the account's current balance to the newest snapshot
        if newest is not None:
            row = self.con.execute(
                "SELECT balance_date FROM accounts WHERE id=?", (account_id,)
            ).fetchone()
            if row is not None:
                cur = date.fromisoformat(row["balance_date"])
                if newest[0] >= cur:
                    self.con.execute(
                        "UPDATE accounts SET balance=?, balance_date=? WHERE id=?",
                        (newest[1], newest[0].isoformat(), account_id))
        self.con.commit()
        return added, updated

    # ---------------- acknowledged ledger warnings ----------------
    def ledger_acknowledgements(self) -> dict[str, float]:
        """Accounts whose reconciliation warning the user has dismissed, and
        the opening balance each was dismissed at."""
        v = self.get_setting("ledger_ack", {})
        if not isinstance(v, dict):
            return {}
        out: dict[str, float] = {}
        for k, x in v.items():
            try:
                out[str(k)] = float(x)
            except (TypeError, ValueError):
                continue
        return out

    @undoable("Dismiss the ledger warning")
    def set_ledger_acknowledgements(self, ack: dict[str, float]) -> None:
        self.set_setting("ledger_ack",
                         {str(k): float(v) for k, v in ack.items()})

    # ---------------- what came before the records ----------------
    # Kept in `settings` rather than as an accounts column: it describes how to
    # *draw* an account whose records do not reach back to the start of the
    # chart, not a property of the account itself, and putting it here means no
    # schema migration on every database that already exists.
    PREHISTORY_MODES = ("zero", "flat")

    def prehistory_mode(self, account_id: int) -> str:
        """What a loan/investment balance was before its first recorded day.

        ``"zero"`` (the default — the account did not exist yet) or ``"flat"``
        (hold the first known balance backwards, for an account that was
        transferred in already carrying one).
        """
        v = self.get_setting(f"prehistory:{account_id}", "zero")
        return v if v in self.PREHISTORY_MODES else "zero"

    @undoable("Change what came before the records")
    def set_prehistory_mode(self, account_id: int, mode: str) -> None:
        if mode not in self.PREHISTORY_MODES:
            raise ValueError(f"unknown prehistory mode: {mode}")
        self.set_setting(f"prehistory:{account_id}", mode)

    # ---------------- import mappings ----------------
    @undoable("Save import settings")
    def save_import_mapping(self, account_id: int, mapping: dict) -> None:
        """Remember the column mapping used for an account's CSV so a later
        export can be re-imported without redoing setup."""
        self.set_setting(f"import_map:{account_id}", mapping)

    def get_import_mapping(self, account_id: int) -> dict | None:
        return self.get_setting(f"import_map:{account_id}", None)

    # ---------------- transactions ----------------
    @undoable("Import transactions")
    def add_transactions(self, df: pd.DataFrame, *, _commit: bool = True) -> tuple[int, int]:
        """Insert rows (idempotent on import_hash). Returns (added, skipped).

        Dates are normalised to ISO ``YYYY-MM-DD`` on the way in. A row whose
        date will not parse is skipped rather than written, because a
        non-parsing date string later reads back as NaT and breaks any consumer
        that formats it.
        """
        rows = []
        provenance = {}
        metadata = {}
        for _, r in df.iterrows():
            d = pd.to_datetime(r["date"], errors="coerce")
            if pd.isna(d):
                continue
            rows.append((int(r["account_id"]), d.date().isoformat(),
                         str(r["description"]), float(r["amount"]),
                         str(r.get("category", "Misc")), str(r["import_hash"])))
            provenance[str(r['import_hash'])] = (
                str(r.get('category_source', 'legacy')), str(r.get('category_reason', '')))
            def iso(value):
                return None if value is None or pd.isna(value) else pd.Timestamp(value).date().isoformat()
            metadata[str(r['import_hash'])] = (iso(r.get('transaction_date')), iso(r.get('posting_date')),
                str(r.get('source_reference', '')), str(r.get('date_basis', 'legacy')))
        blocked = self.suppressed_hashes()
        added = 0
        for row in rows:
            if row[5] in blocked:
                continue          # deliberately removed as a double-posting
            cur = self.con.execute(
                """INSERT OR IGNORE INTO transactions
                   (account_id, date, description, amount, category, import_hash)
                   VALUES (?,?,?,?,?,?)""", row)
            added += cur.rowcount
            if cur.rowcount:
                self.con.execute('UPDATE transactions SET category_source=?,category_reason=? WHERE id=?',
                                 (*provenance[row[5]], cur.lastrowid))
                self.con.execute('UPDATE transactions SET transaction_date=?,posting_date=?,source_reference=?,date_basis=? WHERE id=?',
                                 (*metadata[row[5]], cur.lastrowid))
        if _commit:
            self.con.commit()
        return added, len(df) - added

    def import_identities(self, account_ids) -> pd.DataFrame:
        """Raw row identities for overlap matching, without analytical changes."""
        ids = [int(account_id) for account_id in account_ids]
        marks = ",".join("?" for _ in ids)
        return pd.read_sql_query(
            f"SELECT id, account_id, date, description, amount, import_hash,transaction_date,posting_date,source_reference,date_basis FROM transactions "
            f"WHERE account_id IN ({marks})", self.con, params=ids)

    @cached_read
    def transactions(self, account_id: int | None = None) -> pd.DataFrame:
        q = ("SELECT t.id, t.account_id, t.date, t.description, t.amount, t.category, "
             "COALESCE(b.amount, 0.0) AS business_reimbursement, "
             "t.category_source,t.category_reason,t.category_reviewed,t.category_reviewed_at, "
             "t.transaction_date,t.posting_date,t.source_reference,t.date_basis "
             "FROM transactions t LEFT JOIN business_reimbursements b "
             "ON b.transaction_id=t.id")
        params: tuple = ()
        if account_id is not None:
            q += " WHERE t.account_id=?"
            params = (account_id,)
        q += " ORDER BY date"
        df = pd.read_sql_query(q, self.con, params=params)
        # Parse explicitly with errors="coerce" instead of relying on
        # read_sql_query's parse_dates=. A column holding more than one date
        # format makes pandas give up on inference and hand back NaT (or plain
        # strings), which then blows up every consumer that calls .strftime().
        df["date"] = pd.to_datetime(df["date"], format="ISO8601", errors="coerce")
        for col in ('transaction_date', 'posting_date'):
            df[col] = pd.to_datetime(df[col], format='ISO8601', errors='coerce')
        return _numeric(df, id="int64", account_id="int64", amount="float64",
                        business_reimbursement="float64", category_reviewed='int64')

    @undoable('Import bank CSV')
    def apply_csv_import(self, account_id, df, repairs, mapping, report):
        """Commit rows, approved date repairs and provenance as one transaction.

        Repairs keep the original IDs/hashes and all human category/goal data.
        Metadata-only matches also leave those decisions intact.
        """
        from .import_matching import unseen_transactions
        from .learning import apply_chain, full_chain
        with self.con:
            for repair in repairs:
                self.con.execute('UPDATE transactions SET date=?,transaction_date=?,posting_date=?,source_reference=?,date_basis=? WHERE id=?',
                    (repair['date'], repair['transaction_date'], repair['posting_date'],
                     repair['source_reference'], repair['date_basis'], repair['id']))
            fresh = unseen_transactions(self, df)
            if not fresh.empty:
                fresh = apply_chain(fresh, full_chain(self))
            added, _ = self.add_transactions(fresh, _commit=False)
            report = dict(report, added=added, skipped=len(df)-added,
                          corrected=sum(r['old_date'] != r['date'] for r in repairs))
            for key, value in ((f'import_map:{account_id}', mapping), (f'import_report:{account_id}', report)):
                self.con.execute('INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)',
                                 (key, json.dumps(value)))
        return added, len(df)-added

    @undoable("Set paycheck reimbursement")
    def set_business_reimbursement(self, transaction_id: int, amount: float) -> None:
        """Mark a pass-through part of a deposit; never alter its bank amount."""
        from .categorize import PERSONAL_EXCLUSIONS
        row = self.con.execute("SELECT amount, category FROM transactions WHERE id=?",
                               (transaction_id,)).fetchone()
        try:
            value = float(amount)
        except (ValueError, TypeError) as e:
            raise ValueError("Enter a valid reimbursement amount.") from e
        if not math.isfinite(value) or value < 0:
            raise ValueError("Enter a finite, non-negative reimbursement amount.")
        value = round(value, 2)
        if row is None or row["amount"] <= 0 or row["category"] in PERSONAL_EXCLUSIONS:
            raise ValueError("Select a personal income deposit to separate its reimbursement.")
        if not 0 <= value <= round(float(row["amount"]), 2):
            raise ValueError("Reimbursement must be between zero and the deposit amount.")
        if value == 0:
            self.con.execute("DELETE FROM business_reimbursements WHERE transaction_id=?",
                             (transaction_id,))
        else:
            self.con.execute(
                "INSERT INTO business_reimbursements(transaction_id, amount) VALUES (?,?) "
                "ON CONFLICT(transaction_id) DO UPDATE SET amount=excluded.amount",
                (transaction_id, value))
        self.con.commit()

    # ---------------- removing rows ----------------
    def suppressed_hashes(self) -> set[str]:
        return {r["import_hash"] for r in
                self.con.execute("SELECT import_hash FROM suppressed")}

    @undoable("Delete transactions")
    def delete_transactions(self, tx_ids: list[int], note: str = "",
                            suppress: bool = True) -> int:
        """Delete rows, optionally remembering them so re-import won't restore.

        Suppression is on by default because the only reason to delete a row
        here is that it should never have existed — and the next update from
        the same bank would otherwise bring it straight back, since the row
        hash that makes imports idempotent is freed along with the row.
        """
        if not tx_ids:
            return 0
        marks = ",".join("?" * len(tx_ids))
        rows = self.con.execute(
            f"SELECT id, account_id, import_hash, date, description, amount FROM transactions "
            f"WHERE id IN ({marks})", tx_ids).fetchall()
        if suppress:
            stamp = date.today().isoformat()
            self.con.executemany(
                "INSERT OR REPLACE INTO suppressed "
                "(import_hash, account_id, note, removed_on, date, description, amount) "
                "VALUES (?,?,?,?,?,?,?)",
                [(r["import_hash"], r["account_id"], note, stamp, r["date"],
                  r["description"], r["amount"]) for r in rows])
        self.con.execute(
            f"DELETE FROM transactions WHERE id IN ({marks})", tx_ids)
        self.con.commit()
        return len(rows)

    def suppressed_transactions(self) -> pd.DataFrame:
        """Identities of deliberately removed rows, including legacy tombstones."""
        return pd.read_sql_query(
            "SELECT import_hash, account_id, date, description, amount FROM suppressed", self.con)

    @undoable("Restore removed rows")
    def unsuppress_all(self) -> int:
        """Forget every suppression, so a re-import restores those rows."""
        n = self.con.execute("SELECT COUNT(*) c FROM suppressed").fetchone()["c"]
        self.con.execute("DELETE FROM suppressed")
        self.con.commit()
        return int(n)

    @undoable("Recategorise transactions")
    def set_category(self, tx_ids: list[int], category: str,
                     source: str = 'manual', reason: str = '') -> None:
        self.con.executemany(
            "UPDATE transactions SET category=?,category_source=?,category_reason=?,"
            "category_reviewed=?,category_reviewed_at=? WHERE id=?",
            [(category, source, reason, int(source == 'manual'),
              datetime.now().isoformat(timespec='microseconds') if source == 'manual' else None,
              int(i)) for i in tx_ids],
        )
        self.con.commit()

    @undoable("Recategorise transactions")
    def set_categories_bulk(self, ids_to_cat: dict[int, str], source: str = 'manual',
                            reasons: dict[int, str] | None = None) -> None:
        self.con.executemany(
            "UPDATE transactions SET category=?,category_source=?,category_reason=?,"
            "category_reviewed=?,category_reviewed_at=? WHERE id=?",
            [(c, source, (reasons or {}).get(i, ''), int(source == 'manual'),
              datetime.now().isoformat(timespec='microseconds') if source == 'manual' else None,
              int(i)) for i, c in ids_to_cat.items()],
        )
        self.con.commit()

    # ---------------- recurring ----------------
    @undoable("Update recurring patterns")
    def merge_recurring(self, items: list[RecurringItem]) -> None:
        """Upsert detected items by key, preserving user-owned fields."""
        for it in items:
            self.con.execute(
                """INSERT INTO recurring (key,name,cadence,amount,last_date,
                     days_of_month,confidence,amount_cv,n_seen,active,kind,
                     linked_account_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET
                     name=excluded.name, cadence=excluded.cadence,
                     amount=excluded.amount, last_date=excluded.last_date,
                     days_of_month=excluded.days_of_month,
                     confidence=excluded.confidence,
                     amount_cv=excluded.amount_cv, n_seen=excluded.n_seen""",
                (it.key, it.name, it.cadence, it.amount, it.last_date.isoformat(),
                 json.dumps(it.days_of_month), it.confidence, it.amount_cv,
                 it.n_seen, int(it.active), it.kind, it.linked_account_id),
            )
        self.con.commit()

    def recurring(self, active_only: bool = False) -> list[RecurringItem]:
        q = "SELECT * FROM recurring"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY ABS(amount * (30.44 / CASE cadence " \
             "WHEN 'weekly' THEN 7.0 WHEN 'biweekly' THEN 14.0 " \
             "WHEN 'semimonthly' THEN 15.22 WHEN 'monthly' THEN 30.44 " \
             "WHEN 'quarterly' THEN 91.31 ELSE 365.25 END)) DESC"
        rows = self.con.execute(q).fetchall()
        out = []
        for r in rows:
            out.append(RecurringItem(
                id=r["id"], key=r["key"], name=r["name"], cadence=r["cadence"],
                amount=r["amount"], last_date=_d(r["last_date"]),
                days_of_month=json.loads(r["days_of_month"]),
                confidence=r["confidence"], amount_cv=r["amount_cv"],
                n_seen=r["n_seen"], active=bool(r["active"]), kind=r["kind"],
                linked_account_id=r["linked_account_id"],
            ))
        return out

    @undoable("Edit recurring pattern")
    def update_recurring(self, item_id: int, **fields) -> None:
        allowed = {"active", "kind", "linked_account_id", "amount", "name"}
        if not fields:                     # nothing to do; SET clause would be empty
            return
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(f"field not editable: {k}")
            if k == "active":
                v = int(bool(v))
            sets.append(f"{k}=?")
            vals.append(v)
        vals.append(item_id)
        self.con.execute(f"UPDATE recurring SET {', '.join(sets)} WHERE id=?", vals)
        self.con.commit()

    @undoable("Delete recurring pattern")
    def delete_recurring(self, item_id: int) -> None:
        """Permanently remove a pattern. Note: if it's still present in the
        ledger, the next re-detect will simply re-add it — use active=False
        or kind='ignore' instead for patterns that still occur but should be
        excluded from the forecast. Deletion is for patterns that no longer
        occur at all (e.g. income from a job you've left)."""
        self.con.execute("DELETE FROM recurring WHERE id=?", (item_id,))
        self.con.commit()

    # ---------------- category feedback (ML training corpus) ----------------
    @undoable("Teach Doug a category")
    def add_category_feedback(self, records: list[dict]) -> int:
        """Append manual re-categorisations. Each record needs norm_key,
        description, amount, new_category; old_category/account_type/source are
        optional. Append-only: corrections of corrections are kept so the
        label history stays auditable."""
        self._write_category_feedback(records)
        self.con.commit()
        return len(records)

    def _write_category_feedback(self, records: list[dict]) -> None:
        """Write inside the caller's transaction so review and feedback commit together."""
        now = datetime.now().isoformat(timespec="microseconds")
        rows = [(r["norm_key"], r["description"], float(r["amount"]),
                 r.get("account_type", ""), r.get("old_category", ""),
                 r["new_category"], r.get("source", "manual"), now,
                 int(r.get('remember', 1)), r.get('transaction_id'), r.get('transaction_date'))
                for r in records]
        self.con.executemany(
            """INSERT INTO category_feedback
               (norm_key, description, amount, account_type, old_category,
                new_category, source, created_at,remember,transaction_id,transaction_date)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)

    def category_feedback(self) -> pd.DataFrame:
        """The full labelled corpus, oldest first."""
        return _numeric(
            pd.read_sql_query("SELECT * FROM category_feedback ORDER BY id",
                              self.con),
            id="int64", amount="float64")

    def learned_categories(self) -> dict[str, str]:
        """Current best label per normalised description: the most recent
        correction wins. This is the lookup the rule layer consults before
        falling back to keywords."""
        rows = self.con.execute(
            """SELECT norm_key, new_category FROM category_feedback
               WHERE id IN (SELECT MAX(id) FROM category_feedback
                            WHERE remember=1 GROUP BY norm_key)""").fetchall()
        return {r["norm_key"]: r["new_category"] for r in rows}

    @undoable('Review transaction categories')
    def review_transactions(self, tx_ids: list[int], category: str | dict[int, str] | None = None,
                            remember: bool = True) -> dict:
        from .categorize import BUSINESS_CATEGORY
        from .learning import feedback_key
        ids = {int(i) for i in tx_ids}
        if not ids:
            return dict(reviewed=0, changed=0, remembered=0)
        rows = self.transactions()
        rows = rows[rows.id.isin(ids)]
        if set(rows.id) != ids:
            raise ValueError('Some selected transactions no longer exist. Reload and select them again.')
        choices = category.values() if isinstance(category, dict) else [category] if category is not None else []
        if any(not isinstance(c, str) or not c.strip() or len(c) > 120 for c in choices):
            raise ValueError('Choose a category between 1 and 120 characters.')
        if isinstance(category, dict) and set(category) != ids:
            raise ValueError('Category choices must match the selected transactions.')
        types = {a.id: a.type.value for a in self.accounts()}
        updates, feedback = [], []
        changed = 0
        stamp = datetime.now().isoformat(timespec='microseconds')
        for row in rows.itertuples(index=False):
            label = (category[int(row.id)].strip() if isinstance(category, dict) else
                     category.strip() if category is not None else row.category)
            changed += label != row.category
            can_remember = remember and BUSINESS_CATEGORY not in (label, row.category)
            reason = ('You confirmed this category.' if label == row.category else
                      f'You changed the category from {row.category}.')
            reason += ' Remembered for future imports.' if can_remember else ' Applies only to this transaction.'
            updates.append((label, reason, stamp, int(row.id)))
            if BUSINESS_CATEGORY not in (label, row.category):
                feedback.append(dict(norm_key=feedback_key(row.description, row.amount),
                    description=row.description, amount=row.amount, account_type=types.get(row.account_id, ''),
                    old_category=row.category, new_category=label, source='manual' if category else 'confirmed',
                    remember=int(can_remember), transaction_id=int(row.id),
                    transaction_date=row.date.strftime('%Y-%m-%d') if pd.notna(row.date) else None))
        labels = {}
        for entry in feedback:
            if entry['remember']:
                labels.setdefault(entry['norm_key'], set()).add(entry['new_category'])
        if any(len(values) > 1 for values in labels.values()):
            raise ValueError('These transactions use different categories for the same merchant. '
                             'Uncheck Remember to confirm one-off purchases, or review them separately.')
        with self.con:
            self.con.executemany('UPDATE transactions SET category=?,category_source=\'manual\','
                'category_reason=?,category_reviewed=1,category_reviewed_at=? WHERE id=?', updates)
            self._write_category_feedback(feedback)
        return dict(reviewed=len(updates), changed=int(changed),
                    remembered=sum(r['remember'] for r in feedback))

    @undoable("Clear what Doug learned")
    def clear_category_feedback(self) -> None:
        self.con.execute("DELETE FROM category_feedback")
        self.con.commit()

    # ---------------- goals ----------------
    # ---------------- what-if events ----------------
    def events(self, active_only: bool = False) -> list[OneOffEvent]:
        q = "SELECT * FROM events"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY when_date"
        return [OneOffEvent(label=r["label"],
                            when=date.fromisoformat(r["when_date"]),
                            amount=r["amount"], active=bool(r["active"]),
                            id=r["id"], repeat=r["repeat"],
                            until=date.fromisoformat(r["until_date"]) if r["until_date"] else None)
                for r in self.con.execute(q).fetchall()]

    @undoable("Add what-if event")
    def add_event(self, e: OneOffEvent) -> int:
        return self.add_events([e])[0]

    @undoable("Add what-if events")
    def add_events(self, events: list[OneOffEvent]) -> list[int]:
        """Save related events together; a failed insert leaves none behind."""
        from .events import validate_event
        for event in events:
            validate_event(event)
        ids = []
        with self.con:
            for e in events:
                cur = self.con.execute(
                    "INSERT INTO events (label, when_date, amount, active, repeat, until_date) "
                    "VALUES (?,?,?,?,?,?)",
                    (e.label, e.when.isoformat(), float(e.amount), int(e.active),
                     e.repeat, e.until.isoformat() if e.until is not None else None))
                ids.append(cur.lastrowid)
        for e, event_id in zip(events, ids):
            e.id = event_id
        return ids

    @undoable("Edit what-if event")
    def update_event(self, event_id: int, **fields) -> None:
        from dataclasses import replace
        from .events import validate_event
        current = next((e for e in self.events() if e.id == event_id), None)
        if current is None:
            return
        allowed = {"label", "amount", "active", "repeat"}
        valid_fields = {k:v for k,v in fields.items() if k in allowed | {"when", "until"}}
        validate_event(replace(current, **valid_fields))
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            vals.append(int(v) if k == "active" else v)
        if "when" in fields:
            sets.append("when_date=?")
            vals.append(fields["when"].isoformat())
        if "until" in fields:
            sets.append("until_date=?")
            vals.append(fields["until"].isoformat() if fields["until"] is not None else None)
        if not sets:
            return
        vals.append(event_id)
        with self.con:
            self.con.execute(f"UPDATE events SET {', '.join(sets)} WHERE id=?", vals)

    @undoable("Delete what-if event")
    def delete_event(self, event_id: int) -> None:
        with self.con:
            self.con.execute("DELETE FROM events WHERE id=?", (event_id,))

    @undoable("Add goal")
    def add_goal(self, g: Goal) -> int:
        cur = self.con.execute(
            "INSERT INTO goals (name, amount, kind, saved_amount, spent_amount) VALUES (?,?,?,?,?)",
            (g.name, g.amount, g.kind, g.saved_amount, g.spent_amount),
        )
        self.con.commit()
        g.id = cur.lastrowid
        return g.id

    @undoable("Delete goal")
    def delete_goal(self, goal_id: int) -> None:
        self.con.execute("DELETE FROM goals WHERE id=?", (goal_id,))
        self.con.commit()

    def goals(self) -> list[Goal]:
        rows = self.con.execute("SELECT * FROM goals ORDER BY amount").fetchall()
        return [Goal(id=r["id"], name=r["name"], amount=r["amount"], kind=r["kind"],
                     saved_amount=r["saved_amount"], spent_amount=r["spent_amount"])
                for r in rows]

    @undoable("Edit goal")
    def update_goal(self, goal_id: int, name: str | None = None,
                    amount: float | None = None, kind: str | None = None,
                    saved_amount: float | None = None) -> None:
        from .funding import validate_goal_edit
        validate_goal_edit(self, goal_id, amount, kind, saved_amount)
        sets, params = [], []
        for col, val in (("name", name), ("amount", amount), ("kind", kind),
                         ("saved_amount", saved_amount)):
            if val is not None:
                sets.append(f"{col}=?")
                params.append(val)
        if not sets:
            return
        params.append(goal_id)
        self.con.execute(f"UPDATE goals SET {', '.join(sets)} WHERE id=?", params)
        self.con.commit()

    def goal_activity(self, goal_id: int | None = None) -> list[dict]:
        sql = "SELECT * FROM goal_activity"
        params = ()
        if goal_id is not None:
            sql += " WHERE goal_id=?"
            params = (goal_id,)
        return [dict(r) for r in self.con.execute(sql + " ORDER BY when_date DESC, id DESC", params)]

    @undoable("Record goal activity")
    def record_goal_activity(self, goal_id: int, action: str, amount: float,
                             when: date, account_id: int | None = None,
                             transaction_id: int | None = None, note: str = "") -> int:
        from .funding import record_activity
        return record_activity(self, goal_id, action, amount, when,
                               account_id, transaction_id, note)

    @undoable("Save spending plan")
    def save_spending_plan(self, overrides: dict[str, float], goal_budget: float) -> None:
        from .spending_plan import validate_overrides, money
        values = validate_overrides(overrides)
        budget = money(goal_budget)
        with self.con:
            self.con.executemany(
                "INSERT INTO settings(key,value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [("spending_plan", json.dumps(values)), ("plan_surplus", json.dumps(budget))])

    # ---------------- goal / debt plan ----------------
    # Priority, policy and custom splits live in `settings` rather than in
    # columns. They describe a *plan over* goals and loans, not a property of
    # either one, and keeping them here means one ordered list covers both
    # kinds without an accounts-table migration on every existing database.
    def plan_order(self) -> list[str]:
        """Target keys ('goal:3', 'loan:2') highest priority first."""
        v = self.get_setting("plan_order", [])
        return [str(k) for k in v] if isinstance(v, list) else []

    @undoable("Reorder the plan")
    def set_plan_order(self, keys: list[str]) -> None:
        self.set_setting("plan_order", list(keys))

    def plan_policy(self) -> str:
        v = self.get_setting("plan_policy", "focus")
        return v if v in ("focus", "weighted", "custom") else "focus"

    @undoable("Change plan policy")
    def set_plan_policy(self, policy: str) -> None:
        self.set_setting("plan_policy", policy)

    def plan_allocations(self) -> dict[str, float]:
        """Custom $/month per target key, used only by the 'custom' policy."""
        v = self.get_setting("plan_allocations", {})
        if not isinstance(v, dict):
            return {}
        out = {}
        for k, x in v.items():
            try:
                out[str(k)] = float(x)
            except (TypeError, ValueError):
                continue
        return out

    @undoable("Change plan split")
    def set_plan_allocations(self, alloc: dict[str, float]) -> None:
        self.set_setting("plan_allocations",
                         {str(k): float(v) for k, v in alloc.items()})

    # ---------------- checkpoints ----------------
    _CP_COLS = ("when_date", "label", "n_transactions", "latest_tx", "networth",
                "cash", "investments", "debt", "score", "grade", "fesr",
                "savings_rate", "income", "spending", "essential", "frivolous",
                "unknown", "forecast")

    @undoable("Record a checkpoint")
    def add_checkpoint(self, cp) -> int:
        vals = (cp.when.isoformat(), cp.label, int(cp.n_transactions),
                cp.latest_tx.isoformat() if cp.latest_tx else None,
                cp.networth, cp.cash, cp.investments, cp.debt, cp.score,
                cp.grade, cp.fesr, cp.savings_rate, cp.income, cp.spending,
                cp.essential, cp.frivolous, cp.unknown,
                json.dumps(cp.forecast or {}))
        cur = self.con.execute(
            f"INSERT INTO checkpoints ({','.join(self._CP_COLS)}) "
            f"VALUES ({','.join('?' * len(self._CP_COLS))})", vals)
        self.con.commit()
        cp.id = cur.lastrowid
        return cp.id

    def checkpoints(self) -> list:
        """Every checkpoint, oldest first."""
        from .progress import Checkpoint
        rows = self.con.execute(
            "SELECT * FROM checkpoints ORDER BY when_date, id").fetchall()
        out = []
        for r in rows:
            try:
                fc = json.loads(r["forecast"] or "{}")
            except (TypeError, ValueError):
                fc = {}
            out.append(Checkpoint(
                id=r["id"], when=_d(r["when_date"]), label=r["label"],
                n_transactions=r["n_transactions"],
                latest_tx=_d(r["latest_tx"]) if r["latest_tx"] else None,
                networth=r["networth"], cash=r["cash"],
                investments=r["investments"], debt=r["debt"], score=r["score"],
                grade=r["grade"], fesr=r["fesr"],
                savings_rate=r["savings_rate"], income=r["income"],
                spending=r["spending"], essential=r["essential"],
                frivolous=r["frivolous"], unknown=r["unknown"], forecast=fc))
        return out

    @undoable("Delete checkpoint")
    def delete_checkpoint(self, checkpoint_id: int) -> None:
        self.con.execute("DELETE FROM checkpoints WHERE id=?", (checkpoint_id,))
        self.con.commit()

    # ---------------- misc ----------------
    @undoable("Change a setting")
    def set_setting(self, key: str, value) -> None:
        with self.con:
            self.con.execute(
                "INSERT INTO settings (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def get_setting(self, key: str, default=None):
        r = self.con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    @undoable("Reset all data")
    def clear_all(self) -> None:
        for t in ("goal_activity", "snapshots", "business_reimbursements", "transactions", "recurring", "goals", "accounts",
                  "settings", "category_feedback", "events", "checkpoints",
                  "suppressed"):
            self.con.execute(f"DELETE FROM {t}")
        self.con.commit()

    def close(self) -> None:
        self.con.close()
