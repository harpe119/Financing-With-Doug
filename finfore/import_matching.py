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
    # Exact hash matches consume their own removal slot *before* counting
    # other occurrences, so a third genuine purchase is not suppressed twice.
    known = removed.dropna(subset=["date", "description", "amount"])
    known = known[~known.import_hash.isin(incoming.loc[exact_blocked, "import_hash"])]
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
        else:
            keep.append(True)
    return incoming.loc[keep].copy()
