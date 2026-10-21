"""Category review state and a strictly human-reviewed, local training dataset."""
from __future__ import annotations

from datetime import date
import unicodedata

import pandas as pd
import numpy as np

from .categorize import BUSINESS_CATEGORY
from .recurrence import normalize_desc

SOURCES = {'legacy': 'Older category', 'keyword': 'Keyword rule',
    'fallback': 'No clear match', 'saved_rule': 'Remembered category',
    'similarity': 'Similar merchant', 'broad_merchant': 'General retailer',
    'transfer_pair': 'Possible transfer', 'manual': 'Reviewed by you',
    'model': 'Model suggestion'}
ATTENTION = {'legacy', 'fallback', 'similarity', 'broad_merchant', 'transfer_pair', 'model'}


def merchant_name(description: str) -> str:
    """Exact name matching, allowing only case and whitespace differences."""
    return ' '.join(unicodedata.normalize('NFC', str(description)).casefold().split())


def matching_merchant_transactions(store, transaction_id: int) -> pd.DataFrame:
    tx = store.transactions()
    source = tx.loc[tx.id.eq(transaction_id)]
    if source.empty:
        raise ValueError('The selected transaction no longer exists. Refresh the ledger and select it again.')
    key = merchant_name(source.iloc[0].description)
    if not key:
        raise ValueError('This transaction has no merchant name to match.')
    return tx.loc[tx.description.map(merchant_name).eq(key)].sort_values(
        ['date', 'id'], ascending=False).copy()


def merchant_rule_transactions(store, transaction_ids) -> pd.DataFrame:
    """The broader name and payment-direction matches a remembered rule uses."""
    from .learning import feedback_key
    tx = store.transactions()
    selected = tx.loc[tx.id.isin(transaction_ids)]
    keys = {feedback_key(row.description, row.amount) for row in selected.itertuples()}
    mask = [feedback_key(row.description, row.amount) in keys for row in tx.itertuples()]
    return tx.loc[mask].copy()


def needs_attention(tx: pd.DataFrame) -> pd.Series:
    return tx.category_reviewed.eq(0) & (
        tx.category_source.isin(ATTENTION) | tx.category.isin({'Misc', 'Other income', ''}))


def reviewed_dataset(store, as_of: date | None = None) -> pd.DataFrame:
    """Current verified labels, with dates and groups for leakage-aware evaluation.

    Never promote keyword/similarity labels, remembered-rule predictions, or
    legacy merchant feedback to transaction-level ground truth. A past cutoff
    omits labels reviewed later; this is not a reconstruction of old labels.
    """
    tx = store.transactions()
    end = pd.Timestamp(min(as_of or date.today(), date.today())).normalize() + pd.Timedelta(days=1)
    reviewed = pd.to_datetime(tx.category_reviewed_at, errors='coerce', format='ISO8601')
    keep = (tx.category_reviewed.eq(1) & tx.category_source.eq('manual')
            & tx.date.notna() & (tx.date < end) & reviewed.notna() & (reviewed < end)
            & ~tx.category.isin({BUSINESS_CATEGORY, 'Misc', ''})
            & tx.business_reimbursement.eq(0) & np.isfinite(tx.amount))
    result = tx.loc[keep, ['id','date','description','amount','category','category_reviewed_at']].copy()
    result.rename(columns={'id':'transaction_id','category':'label','category_reviewed_at':'label_recorded_at'}, inplace=True)
    # Keep purchases and refunds together when evaluating unseen merchants;
    # the rule lookup separates their signs for a different purpose.
    result['merchant_group'] = result.description.map(normalize_desc)
    result['account_type'] = tx.loc[result.index,'account_id'].map({a.id:a.type.value for a in store.accounts()})
    result['date'] = result.date.dt.strftime('%Y-%m-%d')
    result['label_source'] = 'human_review'
    return result.sort_values(['date','transaction_id']).reset_index(drop=True)
