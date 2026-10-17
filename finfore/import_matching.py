"""Conservative overlap matching for exported transaction ledgers.

File hashes remain a fast identity check, but older imports may have different
hashes. Match complete financial rows as a multiset as well: the same account,
calendar date, signed cents, and description (case/whitespace normalized).
References and punctuation remain significant. Never fuzzy-match away money.
"""
from collections import Counter
import unicodedata

import pandas as pd


def row_key(row):
    description = " ".join(unicodedata.normalize("NFC", str(row.description)).casefold().split())
    return (int(row.account_id), pd.Timestamp(row.date).date(),
            round(float(row.amount), 2), description)


class PostingDateReviewRequired(ValueError):
    def __init__(self, changes):
        self.changes = changes
        super().__init__(f"{len(changes)} older transaction(s) need their bank posting dates reviewed before importing.")


def legacy_date_repairs(store, incoming):
    """Attach source dates to exact legacy matches; review every changed date.

    Matching is per account and complete financial identity, never merchant
    similarity. Ambiguous multiplicities are blocked, not silently reassigned.
    """
    if incoming.empty:
        return []
    existing = store.import_identities(incoming.account_id.unique())
    legacy = existing[existing.date_basis.eq('legacy')]
    groups = {}
    posted_keys = {}
    for row in existing.itertuples(index=False):
        posted_keys.setdefault(row_key(row),set()).add(row.id)
    for row in legacy.itertuples(index=False):
        groups.setdefault(row_key(row), []).append(row)
    candidates = {}
    for row in incoming.itertuples(index=False):
        old_date = row.transaction_date if pd.notna(row.transaction_date) else row.date
        key = (int(row.account_id), pd.Timestamp(old_date).date(), round(float(row.amount), 2), row_key(row)[3])
        candidates.setdefault(key, []).append(row)
    repairs = []
    for key, rows in candidates.items():
        old = groups.get(key, [])
        if not old:
            continue
        dates_change = any(pd.Timestamp(row.date).date() != key[1] for row in rows)
        if dates_change and (len(old) != len(rows) or len({pd.Timestamp(row.date).date() for row in rows}) != 1):
            raise ValueError('Older entries have identical dates, descriptions and amounts but different posting dates. '
                             'Their identities are ambiguous. Review the account in Transactions before importing; no rows were changed.')
        if dates_change:
            posted_key = row_key(rows[0])
            if posted_keys.get(posted_key,set()) - {row.id for row in old}:
                raise ValueError('This account already has both purchase-date and posting-date copies of an entry. '
                                 'Open Accounts → Check my balances → Find duplicate rows, and choose a seven-day comparison. No rows were changed.')
        for before, after in zip(old, rows):
            def iso(value):
                return None if pd.isna(value) else pd.Timestamp(value).date().isoformat()
            repairs.append(dict(id=int(before.id), old_date=str(before.date)[:10], date=iso(after.date),
                transaction_date=iso(after.transaction_date), posting_date=iso(after.posting_date),
                source_reference=after.source_reference, date_basis=after.date_basis,
                description=after.description, amount=float(after.amount)))
    return repairs


def unseen_transactions(store, incoming: pd.DataFrame) -> pd.DataFrame:
    existing = store.import_identities(incoming.account_id.unique())
    counts = Counter(row_key(row) for row in existing.itertuples(index=False))
    removed = store.suppressed_transactions()
    exact_existing = incoming.import_hash.isin(set(existing.import_hash))
    exact_hashes = set(incoming.loc[exact_existing, "import_hash"])
    for row in existing.itertuples(index=False):
        if row.import_hash in exact_hashes:
            counts[row_key(row)] -= 1
    exact_blocked = incoming.import_hash.isin(store.suppressed_hashes())
    # A removed legacy purchase must stay removed after selecting Posting Date.
    alternate = incoming.copy()
    if 'transaction_date' in alternate:
        from .ingest import make_hashes
        alternate['date'] = alternate.transaction_date.fillna(alternate.date)
        alt_hashes = pd.Series(make_hashes(alternate), index=incoming.index)
    else:
        alt_hashes = incoming.import_hash
    exact_blocked |= alt_hashes.isin(store.suppressed_hashes())
    # Exact hash matches consume their own removal slot *before* counting
    # other occurrences, so a third genuine purchase is not suppressed twice.
    known = removed.dropna(subset=["date", "description", "amount"])
    matched_removals = set(incoming.loc[exact_blocked, 'import_hash']) | set(alt_hashes[exact_blocked])
    known = known[~known.import_hash.isin(matched_removals)]
    removed_counts = Counter(row_key(row) for row in known.itertuples(index=False))
    keep = []
    for blocked, row in zip(exact_blocked | exact_existing, incoming.itertuples(index=False)):
        key = row_key(row)
        if blocked:
            keep.append(False)
        elif counts[key]:
            counts[key] -= 1
            keep.append(False)
        elif removed_counts[key]:
            removed_counts[key] -= 1
            keep.append(False)
        elif pd.notna(getattr(row, 'transaction_date', pd.NaT)) and removed_counts[
                (key[0], pd.Timestamp(row.transaction_date).date(), key[2], key[3])]:
            removed_counts[(key[0], pd.Timestamp(row.transaction_date).date(), key[2], key[3])] -= 1
            keep.append(False)
        else:
            keep.append(True)
    return incoming.loc[keep].copy()
