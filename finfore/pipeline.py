"""Shared ingestion pipeline used by both the GUI and the demo loader."""
from __future__ import annotations

from .categorize import pair_transfers
from .forecast import auto_link_loans
from .learning import apply_chain, feedback_key, full_chain
from .ingest import (ImportConfig, guess_mapping, parse_transactions, read_raw,
                     sniff_delimiter)
from .import_matching import unseen_transactions
from .recurrence import detect
from .store import Store


def import_csv(store: Store, path: str, account_id: int,
               sign_mode: str = "auto", dayfirst: bool = False) -> tuple[int, int]:
    """Programmatic import with auto-detected mapping. Returns (added, skipped)."""
    delim = sniff_delimiter(path)
    sample = read_raw(path, delim, nrows=50)
    g = guess_mapping(list(sample.columns), sample)
    if sign_mode == "auto":
        if g["indicator"] and g["amount"]:
            sign_mode = "indicator"
        elif (g["debit"] or g["credit"]) and not g["amount"]:
            sign_mode = "debit_credit"
        else:
            sign_mode = "standard"
    cfg = ImportConfig(
        path=path, account_id=account_id, delimiter=delim, dayfirst=dayfirst,
        sign_mode=sign_mode, col_date=g["date"], col_desc=g["desc"],
        col_amount=g["amount"], col_debit=g["debit"], col_credit=g["credit"],
        col_indicator=g["indicator"],
    )
    return import_with_config(store, cfg)


def import_with_config(store: Store, cfg: ImportConfig) -> tuple[int, int]:
    """Import using an explicit column mapping (the GUI dialog path). Remembers
    the mapping for the account so a later export can be re-imported without
    redoing setup."""
    df = parse_transactions(cfg)
    # Categorise through the model chain, so anything the user has already
    # corrected is labelled correctly on arrival instead of being re-guessed
    # by the keyword rules and needing the same fix again.
    total = len(df)
    df = unseen_transactions(store, df)
    df = apply_chain(df, full_chain(store))
    added, _skipped = store.add_transactions(df)
    result = (added, total - added)
    store.save_import_mapping(cfg.account_id, _mapping_of(cfg))
    return result


def _mapping_of(cfg: ImportConfig) -> dict:
    """The persistable part of an ImportConfig (everything but the file path)."""
    return {
        "delimiter": cfg.delimiter, "dayfirst": cfg.dayfirst,
        "sign_mode": cfg.sign_mode, "col_date": cfg.col_date,
        "col_desc": cfg.col_desc, "col_amount": cfg.col_amount,
        "col_debit": cfg.col_debit, "col_credit": cfg.col_credit,
        "col_indicator": cfg.col_indicator,
    }


def reimport_with_saved_mapping(store: Store, account_id: int,
                                path: str) -> tuple[int, int]:
    """Re-import a newer CSV for an account using its remembered column
    mapping. Idempotent, so only genuinely new rows are added. Raises
    LookupError if no mapping has been saved for the account yet."""
    m = store.get_import_mapping(account_id)
    if not m:
        raise LookupError("no saved mapping")
    cfg = ImportConfig(path=path, account_id=account_id, **m)
    return import_with_config(store, cfg)


def refresh_derived(store: Store, checkpoint: bool = True,
                    label: str = "") -> int:
    """Re-run transfer pairing + recurring detection over the full ledger.
    Preserves user overrides on recurring items. Returns pattern count.

    Also records a checkpoint, unless told not to. This has to happen here
    rather than on the Progress page, because the two things worth comparing
    later — the score and the projection — are both recency-weighted and cannot
    be reconstructed for a past date once newer months exist. A projection is
    only a claim if it is written down before the outcome is known.
    """
    tx = store.transactions()
    if tx.empty:
        return 0
    learned = store.learned_categories()
    # An explicit correction is stronger evidence than amount/date matching.
    # Exclude protected rows before pairing so neither leg of a rejected pair
    # is silently reclassified during the next import.
    protected = [
        bool(row.category_reviewed) or
        learned.get(feedback_key(row.description, row.amount)) not in (None, "Transfer")
        for row in tx.itertuples(index=False)]
    eligible = tx.loc[[not value for value in protected]]
    is_transfer = pair_transfers(eligible).reindex(tx.index, fill_value=False)
    updates = {}
    for i, row in tx.iterrows():
        if is_transfer.loc[i] and row["category"] != "Transfer":
            updates[int(row["id"])] = "Transfer"
    if updates:
        store.set_categories_bulk(updates, source='transfer_pair', reasons={
            key: 'Opposite amounts in different accounts within four days, with transfer/payment wording. Verify both legs.'
            for key in updates})
        tx = store.transactions()
    items = detect(tx)
    store.merge_recurring(items)
    auto_link_loans(store)
    if checkpoint:
        try:
            from .progress import capture, should_capture
            if should_capture(store):
                capture(store, label=label)
        except Exception:
            # A checkpoint is a nicety; failing to take one must never cost the
            # user the import they actually asked for.
            pass
    return len(store.recurring())
