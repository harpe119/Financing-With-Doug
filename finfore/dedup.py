"""Finding one transaction that got posted twice.

Banks do this. Around a core conversion the same event is exported in both the
old and the new format — an ACH row carrying a reference and a legacy row
without one, with wording that differs enough to look like two events:

    11/14/2023  843.92  Purdue University Deposit
    11/14/2023  843.92  Deposit - PURDUE UNIVERSIT PURDUE UNIVERSIT DEPOSIT

The import hash cannot collapse those, and shouldn't try: it is
(account, date, description, amount, occurrence), and making it looser would
start silently discarding two genuine identical purchases on one day, which is
a far worse failure than keeping one row too many.

So detection lives here instead, and it is *advisory*. Nothing is removed
without the user looking at it, because a false positive deletes real money
from someone's records.

Two signals do the work:

* **merchant similarity** on the description, the same 3-gram measure the
  categoriser uses. It scores the Purdue pair at 0.79 and correctly refuses
  "Transfer To Certificate" against "Transfer To Savings -2507" at 0.29 — same
  day, same $1,000, genuinely different destinations.
* **the reconciliation gap**, which is the honest oracle. An account's opening
  balance is forced by `entered − Σflows`; if removing a candidate moves that
  toward zero, the candidate is probably spurious, and if it moves it away, it
  is probably real. Similarity alone cannot tell two identical $250 transfers
  from one $250 transfer posted twice — the balance can.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .learning import similarity
from .store import Store

# Below this the two descriptions are treated as different merchants. Chosen
# from the observed spread: real duplicates score 0.63–1.00 here, genuinely
# different transfers on the same day score 0.26–0.29. Set deliberately nearer
# the top of that gap — under-detecting costs a manual look, over-detecting
# costs a row of someone's financial history.
MIN_SIMILARITY = 0.55
WINDOW_DAYS = 1          # posting vs transaction date can differ by a day


@dataclass
class DuplicatePair:
    """Two rows that may be the same event. ``drop`` is the later-seen copy."""
    account_id: int
    account: str
    keep_id: int
    drop_id: int
    when: date
    other_when: date
    amount: float
    keep_desc: str
    drop_desc: str
    score: float

    @property
    def helps(self) -> bool:
        """True when removing this row moves the opening balance toward zero.

        Only meaningful alongside a known gap; see `suggest`.
        """
        return self.amount > 0


@dataclass
class DedupReport:
    account: str
    account_id: int
    opening: float                  # implied opening balance as things stand
    pairs: list[DuplicatePair] = field(default_factory=list)
    suggested: list[int] = field(default_factory=list)   # tx ids to drop

    @property
    def suggested_pairs(self) -> list[DuplicatePair]:
        s = set(self.suggested)
        return [p for p in self.pairs if p.drop_id in s]

    def opening_after(self, drop_ids) -> float:
        """Opening balance if these rows were removed."""
        drop = set(drop_ids)
        delta = sum(p.amount for p in self.pairs if p.drop_id in drop)
        return self.opening + delta


def find_pairs(store: Store, account_id: int | None = None,
               window_days: int = WINDOW_DAYS,
               min_similarity: float = MIN_SIMILARITY) -> list[DuplicatePair]:
    """Candidate double-postings, newest first.

    Rows pair when they share an account and an exact signed amount, sit within
    ``window_days`` of each other, and their descriptions look like the same
    merchant. Each row is used at most once, so three identical rows yield two
    pairs rather than three.
    """
    tx = store.transactions()
    if tx.empty:
        return []
    names = {a.id: a.name for a in store.accounts()}
    if account_id is not None:
        tx = tx[tx["account_id"] == account_id]
    tx = tx.dropna(subset=["date"]).sort_values(["date", "id"])

    out: list[DuplicatePair] = []
    used: set[int] = set()
    for (acct, amt), grp in tx.groupby(["account_id", tx["amount"].round(2)]):
        if len(grp) < 2:
            continue
        rows = list(grp.itertuples())
        for i, a in enumerate(rows):
            if a.id in used:
                continue
            for b in rows[i + 1:]:
                if b.id in used:
                    continue
                if abs((b.date - a.date).days) > window_days:
                    continue
                s = similarity(str(a.description), str(b.description))
                if s < min_similarity:
                    continue
                used.add(a.id)
                used.add(b.id)
                out.append(DuplicatePair(
                    account_id=int(acct), account=names.get(int(acct), "?"),
                    keep_id=int(a.id), drop_id=int(b.id),
                    when=a.date.date(), other_when=b.date.date(),
                    amount=float(amt), keep_desc=str(a.description),
                    drop_desc=str(b.description), score=float(s)))
                break
    out.sort(key=lambda p: (-abs(p.amount), p.when))
    return out


def suggest(store: Store, account_id: int | None = None) -> list[DedupReport]:
    """Per-account candidates, with a suggested selection driven by the gap.

    Nothing is suggested for an account that already reconciles: a ledger whose
    opening balance is sensible has no evidence of a problem, and two identical
    same-day charges are usually two identical same-day charges. Where the
    opening *is* impossible, the rows proposed are the ones whose removal moves
    it back toward zero, largest first, stopping before overshooting.
    """
    from .history import reconcile_all

    pairs = find_pairs(store, account_id)
    by_acct: dict[int, list[DuplicatePair]] = {}
    for p in pairs:
        by_acct.setdefault(p.account_id, []).append(p)

    reports: list[DedupReport] = []
    ids = {a.name: a.id for a in store.accounts()}
    for info in reconcile_all(store):
        aid = ids.get(info.account)
        if aid is None or (account_id is not None and aid != account_id):
            continue
        mine = by_acct.get(aid, [])
        if not mine:
            continue
        rep = DedupReport(account=info.account, account_id=aid,
                          opening=info.opening, pairs=mine)
        if info.opening < -1.0:
            # Excess inflow to remove. Take the largest helpful candidates
            # while they keep the opening below zero, so a suggestion never
            # overshoots into inventing money.
            need = -info.opening
            for p in sorted([q for q in mine if q.amount > 0],
                            key=lambda q: -q.amount):
                if p.amount <= need + 1.0:
                    rep.suggested.append(p.drop_id)
                    need -= p.amount
                if need <= 0.5:
                    break
        reports.append(rep)
    return reports


def apply(store: Store, drop_ids: list[int]) -> int:
    """Remove the chosen rows and remember them, so re-import won't restore."""
    return store.delete_transactions(
        drop_ids, note="removed as a duplicate posting")
