"""SQLite persistence.

Single-file database. Schema is created on first open. Recurring-item
re-detection merges by key and preserves user overrides (active flag, kind,
loan link) so refreshing detection never discards manual curation.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .models import Account, AccountType, Goal, OneOffEvent, RecurringItem

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
    kind TEXT NOT NULL DEFAULT 'cash'
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Rows the user has deliberately removed as double-postings. Without this a
-- later "Update from CSV" would import them straight back: the row hash is
-- what makes imports idempotent, so deleting the row alone just frees the hash
-- to be used again. Suppressing the hash makes the removal stick.
CREATE TABLE IF NOT EXISTS suppressed (
    import_hash TEXT PRIMARY KEY,
    account_id INTEGER,
    note TEXT NOT NULL DEFAULT '',
    removed_on TEXT NOT NULL
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
    active INTEGER NOT NULL DEFAULT 1
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
        self.con = sqlite3.connect(str(self.path))
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.executescript(_SCHEMA)
        self.con.commit()

    # ---------------- accounts ----------------
    def add_account(self, a: Account) -> int:
        cur = self.con.execute(
            """INSERT INTO accounts (name,type,balance,balance_date,apr,annual_return,
               volatility,monthly_contribution,monthly_payment,payment_day)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (a.name, a.type.value, a.balance, a.balance_date.isoformat(), a.apr,
             a.annual_return, a.volatility, a.monthly_contribution,
             a.monthly_payment, a.payment_day),
        )
        self.con.commit()
        a.id = cur.lastrowid
        return a.id

    def update_account(self, a: Account) -> None:
        self.con.execute(
            """UPDATE accounts SET name=?,type=?,balance=?,balance_date=?,apr=?,
               annual_return=?,volatility=?,monthly_contribution=?,
               monthly_payment=?,payment_day=? WHERE id=?""",
            (a.name, a.type.value, a.balance, a.balance_date.isoformat(), a.apr,
             a.annual_return, a.volatility, a.monthly_contribution,
             a.monthly_payment, a.payment_day, a.id),
        )
        self.con.commit()

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
        )

    # ---------------- snapshots ----------------
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
        return df

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

    def delete_snapshot(self, snapshot_id: int) -> None:
        self.con.execute("DELETE FROM snapshots WHERE id=?", (snapshot_id,))
        self.con.commit()

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

    # ---------------- import mappings ----------------
    def save_import_mapping(self, account_id: int, mapping: dict) -> None:
        """Remember the column mapping used for an account's CSV so a later
        export can be re-imported without redoing setup."""
        self.set_setting(f"import_map:{account_id}", mapping)

    def get_import_mapping(self, account_id: int) -> dict | None:
        return self.get_setting(f"import_map:{account_id}", None)

    # ---------------- transactions ----------------
    def add_transactions(self, df: pd.DataFrame) -> tuple[int, int]:
        """Insert rows (idempotent on import_hash). Returns (added, skipped).

        Dates are normalised to ISO ``YYYY-MM-DD`` on the way in. A row whose
        date will not parse is skipped rather than written, because a
        non-parsing date string later reads back as NaT and breaks any consumer
        that formats it.
        """
        rows = []
        for _, r in df.iterrows():
            d = pd.to_datetime(r["date"], errors="coerce")
            if pd.isna(d):
                continue
            rows.append((int(r["account_id"]), d.date().isoformat(),
                         str(r["description"]), float(r["amount"]),
                         str(r.get("category", "Misc")), str(r["import_hash"])))
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
        self.con.commit()
        return added, len(df) - added

    def transactions(self, account_id: int | None = None) -> pd.DataFrame:
        q = ("SELECT id, account_id, date, description, amount, category "
             "FROM transactions")
        params: tuple = ()
        if account_id is not None:
            q += " WHERE account_id=?"
            params = (account_id,)
        q += " ORDER BY date"
        df = pd.read_sql_query(q, self.con, params=params)
        # Parse explicitly with errors="coerce" instead of relying on
        # read_sql_query's parse_dates=. A column holding more than one date
        # format makes pandas give up on inference and hand back NaT (or plain
        # strings), which then blows up every consumer that calls .strftime().
        df["date"] = pd.to_datetime(df["date"], format="ISO8601", errors="coerce")
        return df

    # ---------------- removing rows ----------------
    def suppressed_hashes(self) -> set[str]:
        return {r["import_hash"] for r in
                self.con.execute("SELECT import_hash FROM suppressed")}

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
            f"SELECT id, account_id, import_hash FROM transactions "
            f"WHERE id IN ({marks})", tx_ids).fetchall()
        if suppress:
            stamp = date.today().isoformat()
            self.con.executemany(
                "INSERT OR REPLACE INTO suppressed "
                "(import_hash, account_id, note, removed_on) VALUES (?,?,?,?)",
                [(r["import_hash"], r["account_id"], note, stamp) for r in rows])
        self.con.execute(
            f"DELETE FROM transactions WHERE id IN ({marks})", tx_ids)
        self.con.commit()
        return len(rows)

    def unsuppress_all(self) -> int:
        """Forget every suppression, so a re-import restores those rows."""
        n = self.con.execute("SELECT COUNT(*) c FROM suppressed").fetchone()["c"]
        self.con.execute("DELETE FROM suppressed")
        self.con.commit()
        return int(n)

    def set_category(self, tx_ids: list[int], category: str) -> None:
        self.con.executemany(
            "UPDATE transactions SET category=? WHERE id=?",
            [(category, i) for i in tx_ids],
        )
        self.con.commit()

    def set_categories_bulk(self, ids_to_cat: dict[int, str]) -> None:
        self.con.executemany(
            "UPDATE transactions SET category=? WHERE id=?",
            [(c, i) for i, c in ids_to_cat.items()],
        )
        self.con.commit()

    # ---------------- recurring ----------------
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

    def delete_recurring(self, item_id: int) -> None:
        """Permanently remove a pattern. Note: if it's still present in the
        ledger, the next re-detect will simply re-add it — use active=False
        or kind='ignore' instead for patterns that still occur but should be
        excluded from the forecast. Deletion is for patterns that no longer
        occur at all (e.g. income from a job you've left)."""
        self.con.execute("DELETE FROM recurring WHERE id=?", (item_id,))
        self.con.commit()

    # ---------------- category feedback (ML training corpus) ----------------
    def add_category_feedback(self, records: list[dict]) -> int:
        """Append manual re-categorisations. Each record needs norm_key,
        description, amount, new_category; old_category/account_type/source are
        optional. Append-only: corrections of corrections are kept so the
        label history stays auditable."""
        now = datetime.now().isoformat(timespec="seconds")
        rows = [(r["norm_key"], r["description"], float(r["amount"]),
                 r.get("account_type", ""), r.get("old_category", ""),
                 r["new_category"], r.get("source", "manual"), now)
                for r in records]
        self.con.executemany(
            """INSERT INTO category_feedback
               (norm_key, description, amount, account_type, old_category,
                new_category, source, created_at)
               VALUES (?,?,?,?,?,?,?,?)""", rows)
        self.con.commit()
        return len(rows)

    def category_feedback(self) -> pd.DataFrame:
        """The full labelled corpus, oldest first."""
        return pd.read_sql_query(
            "SELECT * FROM category_feedback ORDER BY id", self.con)

    def learned_categories(self) -> dict[str, str]:
        """Current best label per normalised description: the most recent
        correction wins. This is the lookup the rule layer consults before
        falling back to keywords."""
        rows = self.con.execute(
            """SELECT norm_key, new_category FROM category_feedback
               WHERE id IN (SELECT MAX(id) FROM category_feedback
                            GROUP BY norm_key)""").fetchall()
        return {r["norm_key"]: r["new_category"] for r in rows}

    def clear_category_feedback(self) -> None:
        self.con.execute("DELETE FROM category_feedback")
        self.con.commit()

    # ---------------- goals ----------------
    # ---------------- one-off events ----------------
    def events(self, active_only: bool = False) -> list[OneOffEvent]:
        q = "SELECT * FROM events"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY when_date"
        return [OneOffEvent(label=r["label"],
                            when=date.fromisoformat(r["when_date"]),
                            amount=r["amount"], active=bool(r["active"]),
                            id=r["id"])
                for r in self.con.execute(q).fetchall()]

    def add_event(self, e: OneOffEvent) -> int:
        cur = self.con.execute(
            "INSERT INTO events (label, when_date, amount, active) VALUES (?,?,?,?)",
            (e.label, e.when.isoformat(), float(e.amount), int(e.active)))
        self.con.commit()
        e.id = cur.lastrowid
        return e.id

    def update_event(self, event_id: int, **fields) -> None:
        allowed = {"label", "amount", "active"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k}=?")
            vals.append(int(v) if k == "active" else v)
        if "when" in fields:
            sets.append("when_date=?")
            vals.append(fields["when"].isoformat())
        if not sets:
            return
        vals.append(event_id)
        self.con.execute(f"UPDATE events SET {', '.join(sets)} WHERE id=?", vals)
        self.con.commit()

    def delete_event(self, event_id: int) -> None:
        self.con.execute("DELETE FROM events WHERE id=?", (event_id,))
        self.con.commit()

    def add_goal(self, g: Goal) -> int:
        cur = self.con.execute(
            "INSERT INTO goals (name, amount, kind) VALUES (?,?,?)",
            (g.name, g.amount, g.kind),
        )
        self.con.commit()
        g.id = cur.lastrowid
        return g.id

    def delete_goal(self, goal_id: int) -> None:
        self.con.execute("DELETE FROM goals WHERE id=?", (goal_id,))
        self.con.commit()

    def goals(self) -> list[Goal]:
        rows = self.con.execute("SELECT * FROM goals ORDER BY amount").fetchall()
        return [Goal(id=r["id"], name=r["name"], amount=r["amount"], kind=r["kind"])
                for r in rows]

    def update_goal(self, goal_id: int, name: str | None = None,
                    amount: float | None = None, kind: str | None = None) -> None:
        sets, params = [], []
        for col, val in (("name", name), ("amount", amount), ("kind", kind)):
            if val is not None:
                sets.append(f"{col}=?")
                params.append(val)
        if not sets:
            return
        params.append(goal_id)
        self.con.execute(f"UPDATE goals SET {', '.join(sets)} WHERE id=?", params)
        self.con.commit()

    # ---------------- goal / debt plan ----------------
    # Priority, policy and custom splits live in `settings` rather than in
    # columns. They describe a *plan over* goals and loans, not a property of
    # either one, and keeping them here means one ordered list covers both
    # kinds without an accounts-table migration on every existing database.
    def plan_order(self) -> list[str]:
        """Target keys ('goal:3', 'loan:2') highest priority first."""
        v = self.get_setting("plan_order", [])
        return [str(k) for k in v] if isinstance(v, list) else []

    def set_plan_order(self, keys: list[str]) -> None:
        self.set_setting("plan_order", list(keys))

    def plan_policy(self) -> str:
        v = self.get_setting("plan_policy", "focus")
        return v if v in ("focus", "weighted", "custom") else "focus"

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

    def set_plan_allocations(self, alloc: dict[str, float]) -> None:
        self.set_setting("plan_allocations",
                         {str(k): float(v) for k, v in alloc.items()})

    # ---------------- checkpoints ----------------
    _CP_COLS = ("when_date", "label", "n_transactions", "latest_tx", "networth",
                "cash", "investments", "debt", "score", "grade", "fesr",
                "savings_rate", "income", "spending", "essential", "frivolous",
                "unknown", "forecast")

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

    def delete_checkpoint(self, checkpoint_id: int) -> None:
        self.con.execute("DELETE FROM checkpoints WHERE id=?", (checkpoint_id,))
        self.con.commit()

    # ---------------- misc ----------------
    def set_setting(self, key: str, value) -> None:
        self.con.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
        self.con.commit()

    def get_setting(self, key: str, default=None):
        r = self.con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def clear_all(self) -> None:
        for t in ("snapshots", "transactions", "recurring", "goals", "accounts",
                  "settings", "category_feedback", "events", "checkpoints",
                  "suppressed"):
            self.con.execute(f"DELETE FROM {t}")
        self.con.commit()

    def close(self) -> None:
        self.con.close()
