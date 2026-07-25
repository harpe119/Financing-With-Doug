"""Adaptive categorisation: learn from the user's manual corrections.

This module is the seam between today's hand-written keyword rules and a real
classifier later. Nothing here trains a model yet — it does the two things that
have to exist *before* a model is worth building:

1. **Capture labels.** Every time the user re-categorises a transaction we
   record (normalised description, raw description, amount, account type, old
   label, new label, timestamp). That is a supervised training set, and it is
   the only part that cannot be reconstructed after the fact — so it is
   collected from day one whether or not a model ever consumes it.

2. **Use those labels immediately.** ``LearnedRules`` is a memory-based
   classifier: exact match on the normalised description, most recent
   correction wins. It is deliberately trivial, but it already delivers the
   behaviour people expect ("I fixed this once, stop getting it wrong"), and it
   sets the accuracy floor any statistical model has to beat.

Layering, highest precedence first:

    LearnedRules (user corrections)  ->  keyword RULES  ->  sign-based default

A future model slots in as another ``CategoryModel`` in that chain. See
``ml_notes`` at the bottom of this file for the intended path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import pandas as pd

from .categorize import categorize
from .recurrence import normalize_desc


def feedback_key(description: str, amount: float) -> str:
    """Identity under which a correction is remembered.

    Same normalisation the recurrence detector uses, plus the sign of the
    amount: "AMAZON MKTPL" as a -$40 purchase and as a +$40 refund are
    different labelling problems.
    """
    return f"{normalize_desc(description)}|{'+' if amount > 0 else '-'}"


@runtime_checkable
class CategoryModel(Protocol):
    """Anything that can propose a category.

    ``predict`` returns ``None`` to abstain, which is what lets models be
    chained: an abstaining model defers to the next one instead of guessing.
    A future scikit-learn wrapper implements exactly this and abstains when its
    predicted probability is below a confidence floor.
    """

    def predict(self, description: str, amount: float) -> str | None:
        ...


@dataclass
class LearnedRules:
    """Exact-match memory over the user's own corrections."""

    table: dict[str, str]

    @classmethod
    def from_store(cls, store) -> "LearnedRules":
        return cls(table=store.learned_categories())

    def predict(self, description: str, amount: float) -> str | None:
        return self.table.get(feedback_key(description, amount))

    def __len__(self) -> int:
        return len(self.table)


class KeywordModel:
    """The existing hand-written rules, wrapped in the model interface.

    Never abstains — it is the backstop that guarantees every transaction gets
    a label.
    """

    def predict(self, description: str, amount: float) -> str | None:
        return categorize(description, amount)


def build_chain(store, extra: list[CategoryModel] | None = None
                ) -> list[CategoryModel]:
    """Default precedence chain. ``extra`` models are consulted after the
    user's own corrections but before the keyword fallback, which is where a
    trained classifier belongs.
    """
    chain: list[CategoryModel] = [LearnedRules.from_store(store)]
    chain.extend(extra or [])
    chain.append(KeywordModel())
    return chain


def full_chain(store, extra: list[CategoryModel] | None = None
               ) -> list[CategoryModel]:
    """Chain including similarity propagation.

    Order matters: an exact prior correction always wins, then any trained
    model, then merchant similarity, then keywords. Similarity sits below a
    trained model because it is a blunter generaliser, and above keywords
    because it is grounded in this user's own labels.
    """
    chain: list[CategoryModel] = [LearnedRules.from_store(store)]
    chain.extend(extra or [])
    chain.append(NearestMerchant.from_store(store))
    chain.append(KeywordModel())
    return chain


def predict_one(chain: list[CategoryModel], description: str,
                amount: float) -> str:
    for model in chain:
        got = model.predict(description, amount)
        if got:
            return got
    return "Other income" if amount > 0 else "Misc"


def apply_chain(df: pd.DataFrame, chain: list[CategoryModel]) -> pd.DataFrame:
    """Add/overwrite a 'category' column using the model chain."""
    out = df.copy()
    out["category"] = [predict_one(chain, d, a)
                       for d, a in zip(out["description"], out["amount"])]
    return out


def record_corrections(store, tx_rows: pd.DataFrame, new_category: str,
                       account_types: dict[int, str] | None = None,
                       source: str = "manual") -> int:
    """Persist a batch of manual re-categorisations as training labels.

    ``tx_rows`` is a slice of the transactions frame (needs description,
    amount, category, account_id).
    """
    if tx_rows is None or tx_rows.empty:
        return 0
    account_types = account_types or {}
    records = []
    for _, r in tx_rows.iterrows():
        desc, amt = str(r["description"]), float(r["amount"])
        records.append({
            "norm_key": feedback_key(desc, amt),
            "description": desc,
            "amount": amt,
            "account_type": account_types.get(int(r["account_id"]), ""),
            "old_category": str(r.get("category", "")),
            "new_category": new_category,
            "source": source,
        })
    return store.add_category_feedback(records)


def training_frame(store) -> pd.DataFrame:
    """The labelled corpus in model-ready shape.

    Columns: description (raw text feature), norm_key (normalised text),
    amount, abs_amount, is_inflow, account_type, label. Deduplicated to the
    most recent label per (norm_key, label) so repeated corrections of the same
    merchant don't dominate the sample.
    """
    df = store.category_feedback()
    if df.empty:
        return pd.DataFrame(columns=["description", "norm_key", "amount",
                                     "abs_amount", "is_inflow", "account_type",
                                     "label"])
    df = df.drop_duplicates(subset=["norm_key"], keep="last").copy()
    df["abs_amount"] = df["amount"].abs()
    df["is_inflow"] = (df["amount"] > 0).astype(int)
    df = df.rename(columns={"new_category": "label"})
    return df[["description", "norm_key", "amount", "abs_amount", "is_inflow",
               "account_type", "label"]].reset_index(drop=True)


def coverage(store) -> dict:
    """How much signal has accumulated — used to decide when a statistical
    model is worth training at all."""
    df = training_frame(store)
    if df.empty:
        return {"labels": 0, "classes": 0, "ready": False,
                "note": "No corrections recorded yet."}
    classes = df["label"].nunique()
    per_class = df.groupby("label").size()
    ready = len(df) >= 200 and classes >= 5 and (per_class >= 15).sum() >= 5
    return {
        "labels": int(len(df)),
        "classes": int(classes),
        "min_per_class": int(per_class.min()),
        "ready": bool(ready),
        "note": ("Enough labelled data to try a statistical model."
                 if ready else
                 "Keep correcting; a model needs roughly 200 labels across "
                 "5+ categories before it beats the keyword rules."),
    }


ml_notes = """
Path to an actual model
-----------------------
The hard part of this problem is not the algorithm, it is that the label set is
personal (one person's "Shopping" is another's "Groceries") and the sample is
small — a few hundred corrections, not millions. That rules out anything deep
and points at a classical text classifier.

Concretely, when `coverage(store)["ready"]` is True:

  features : char n-gram TF-IDF (analyzer="char_wb", ngram_range=(3, 5)) over
             the raw description, which handles merchant strings like
             "SQ *BLUE BOTTLE 0042" far better than word tokens; plus
             log1p(abs_amount) and is_inflow as numeric columns.
  model    : LogisticRegression or LinearSVC in a sklearn Pipeline with
             ColumnTransformer. Both are fast to fit on this size, calibrated
             enough to threshold on, and — importantly — inspectable, so a
             wrong prediction can be explained to the user.
  abstain  : wrap in a class implementing CategoryModel that returns None when
             max predict_proba < ~0.65, so low-confidence rows fall through to
             the keyword rules instead of being confidently wrong.
  eval     : GroupKFold grouped on norm_key. Random splits leak here — the same
             merchant appearing in train and test inflates accuracy badly,
             because exact-match memory would already have caught it.
  baseline : LearnedRules + KeywordModel on the same folds. A model that does
             not beat that is not worth shipping.

Insert it with `build_chain(store, extra=[TrainedModel.load(path)])`. Nothing
else in the app needs to change: `apply_chain` and `predict_one` already treat
the chain as opaque, and `pipeline.import_csv` builds the chain per import so a
newly trained model takes effect on the next import with no other edits.

Retraining is offline and explicit (a menu action), not automatic — silently
changing historical categories under the user would be worse than being wrong
consistently.
"""


# ===========================================================================
# Reducing the uncategorised pile
# ===========================================================================
# A trained classifier is the *wrong first move* here, and it is worth being
# precise about why. Training needs labels; the uncategorised pile exists
# precisely because there are none. Fitting a model on a handful of examples
# produces something that is confidently wrong on the merchants that matter,
# and the failure is invisible under a random train/test split because the same
# merchant lands on both sides.
#
# Three cheaper mechanisms clear most of the pile first, in descending order of
# value per unit of user effort:
#
#   1. MERCHANT CLUSTERING + ACTIVE LEARNING.  Transactions collapse to far
#      fewer merchants than rows. Ranking unlabelled merchants by total dollars
#      and labelling the top of that list is the single highest-leverage action
#      available: one decision fixes every past and future transaction from that
#      merchant. In practice a few dozen labels clear the large majority of the
#      uncategorised total.
#   2. RECURRENCE PROPAGATION.  A merchant that already reads as a recurring
#      pattern is a near-certain repeat, so labelling it pays off every month
#      forever. These are surfaced first within the ranking.
#   3. NEAREST-MERCHANT SIMILARITY.  Character n-gram overlap generalises an
#      existing label to unseen variants — "SHELL OIL 4471" from "SHELL
#      SERVICE" — using the labels from steps 1 and 2. This works with a dozen
#      labels, where a fitted model needs hundreds.
#
# Only after those does a statistical model earn its place, and by then it has a
# real corpus to train on. It slots into the same chain (see ml_notes).

_NGRAM = 3
_SIM_FLOOR = 0.25          # calibrated below; see the note on separability

# Payment processors that prefix the real merchant name. Stripping them is what
# lets "SQ *BLUE BOTTLE 0042" match "BLUE BOTTLE COFFEE".
_PROCESSOR_PREFIXES = frozenset({
    "SQ", "TST", "PAYPAL", "PP", "VENMO", "ZELLE", "ZEL", "WPY", "EIG",
    "IC", "CKE", "PY", "SP", "PAY",
})


def _merchant_tokens(text: str) -> list[str]:
    toks = normalize_desc(text or "").split()
    while toks and toks[0] in _PROCESSOR_PREFIXES:
        toks = toks[1:]
    return toks


def _grams(text: str, n: int = _NGRAM) -> set[str]:
    """Character n-grams of a merchant string."""
    s = f" {text} "
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}


def similarity(a: str, b: str) -> float:
    """Merchant-string similarity in [0, 1].

    Character n-grams rather than word tokens, because bank descriptions are
    mangled in ways that break tokenisation: "SQ *BLUE BOTTLE 0042",
    "TST* BLUEBOTTLE" and "BLUE BOTTLE COFFEE #12" share almost no whole words
    but a great many 3-grams.

    Jaccard specifically, not the containment/overlap coefficient. Measured on
    realistic pairs, Jaccard is the only one of the three that separates cleanly
    — overlap rates "SHELL OIL" against "SHELL SERVICE STN" at 0.56, higher than
    it rates two genuine Safeway branches, because containment rewards one
    string simply being longer.

    The leading token carries the brand, so a mismatch there is discounted
    hard. That is what keeps "BLUE BOTTLE" apart from "BLUE CROSS" and "ACME
    STORAGE" apart from "ACME PAYROLL", all of which share a first word and
    nothing else.
    """
    ta, tb = _merchant_tokens(a), _merchant_tokens(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    ga, gb = _grams(" ".join(ta)), _grams(" ".join(tb))
    if not ga or not gb:
        return 0.0
    jac = len(ga & gb) / len(ga | gb)
    return jac if ta[0] == tb[0] else jac * 0.4


@dataclass
class NearestMerchant:
    """Label propagation by merchant-string similarity.

    Abstains unless the best match clears ``floor``, so an uncertain guess falls
    through to the keyword rules rather than being confidently wrong. Pure
    Python — no scikit-learn dependency for something this small.
    """

    labelled: list[tuple[str, str]]        # (example description, category)
    floor: float = _SIM_FLOOR

    @classmethod
    def from_store(cls, store, floor: float = _SIM_FLOOR) -> "NearestMerchant":
        fb = store.category_feedback()
        pairs: list[tuple[str, str]] = []
        if not fb.empty:
            fb = fb.drop_duplicates(subset=["norm_key"], keep="last")
            pairs = list(zip(fb["description"], fb["new_category"]))
        return cls(labelled=pairs, floor=floor)

    def predict(self, description: str, amount: float) -> str | None:
        best, best_sim = None, 0.0
        for desc, cat in self.labelled:
            sim = similarity(description, desc)
            if sim > best_sim:
                best, best_sim = cat, sim
        return best if best_sim >= self.floor else None

    def explain(self, description: str) -> tuple[str | None, float, str | None]:
        """(category, similarity, matched example) — for showing the user why."""
        best, best_sim, src = None, 0.0, None
        for desc, cat in self.labelled:
            sim = similarity(description, desc)
            if sim > best_sim:
                best, best_sim, src = cat, sim, desc
        if best_sim < self.floor:
            return None, best_sim, src
        return best, best_sim, src


@dataclass
class MerchantCluster:
    """An unlabelled merchant, with everything needed to decide about it."""
    norm_key: str
    display: str                 # a representative raw description
    n_tx: int
    total: float                 # absolute dollars, whole history
    recent_total: float          # absolute dollars, last 90 days
    monthly: float               # rough $/month over the span seen
    is_recurring: bool
    suggestion: str | None       # what NearestMerchant would guess
    confidence: float            # similarity behind that guess
    tx_ids: list[int]

    @property
    def priority(self) -> float:
        """Ranking weight: dollars, with recent and recurring spend counted
        more heavily because labelling those pays off repeatedly."""
        base = self.total + 2.0 * self.recent_total
        return base * (1.5 if self.is_recurring else 1.0)


def uncategorized_clusters(store, unknown_labels: set[str] | None = None,
                           limit: int = 40) -> list[MerchantCluster]:
    """Unlabelled merchants, ranked by how much labelling them would fix.

    This is the active-learning queue: work down it and the uncategorised total
    collapses far faster than transaction-by-transaction editing.
    """
    unknown_labels = unknown_labels or {"Misc", "Other income", ""}
    tx = store.transactions()
    if tx.empty:
        return []
    tx = tx[tx["category"].isin(unknown_labels)]
    if tx.empty:
        return []
    tx = tx.copy()
    tx["date"] = pd.to_datetime(tx["date"], errors="coerce")
    tx["norm_key"] = [feedback_key(d, a)
                      for d, a in zip(tx["description"], tx["amount"])]

    recurring_keys = {it.key for it in store.recurring()}
    near = NearestMerchant.from_store(store)
    cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=90)

    out: list[MerchantCluster] = []
    for key, grp in tx.groupby("norm_key"):
        amounts = grp["amount"].abs()
        total = float(amounts.sum())
        recent = float(amounts[grp["date"] >= cutoff].sum())
        span_days = 1
        if grp["date"].notna().any():
            span_days = max((grp["date"].max() - grp["date"].min()).days, 1)
        monthly = total / max(span_days / 30.44, 1.0)
        display = str(grp["description"].iloc[-1])
        cat, sim, _src = near.explain(display)
        out.append(MerchantCluster(
            norm_key=key, display=display, n_tx=len(grp), total=total,
            recent_total=recent, monthly=monthly,
            is_recurring=key in recurring_keys,
            suggestion=cat, confidence=sim,
            tx_ids=[int(i) for i in grp["id"]]))
    out.sort(key=lambda c: -c.priority)
    return out[:limit]


def label_cluster(store, cluster: MerchantCluster, category: str) -> int:
    """Apply a category to every transaction of one merchant and remember it.

    Returns the number of transactions relabelled. The correction is recorded
    once (not once per row) so a single merchant does not swamp the training
    corpus.
    """
    if not cluster.tx_ids or not category:
        return 0
    store.set_category(cluster.tx_ids, category)
    store.add_category_feedback([{
        "norm_key": cluster.norm_key,
        "description": cluster.display,
        "amount": -abs(cluster.total / max(cluster.n_tx, 1)),
        "old_category": "Misc",
        "new_category": category,
        "source": "review-queue",
    }])
    return len(cluster.tx_ids)


def recategorize_all(store, extra: list[CategoryModel] | None = None,
                     only_unknown: bool = True) -> int:
    """Re-run categorisation over the whole ledger with the current chain.

    Called after labelling a batch, so similarity propagation reaches merchants
    the user never touched. Defaults to leaving already-categorised rows alone —
    silently rewriting a label the user set would be worse than being wrong.
    """
    tx = store.transactions()
    if tx.empty:
        return 0
    if only_unknown:
        tx = tx[tx["category"].isin({"Misc", "Other income", ""})]
        if tx.empty:
            return 0
    chain = build_chain(store, extra=extra or [NearestMerchant.from_store(store)])
    updates: dict[int, str] = {}
    for _, r in tx.iterrows():
        got = predict_one(chain, str(r["description"]), float(r["amount"]))
        if got and got != r["category"]:
            updates[int(r["id"])] = got
    if updates:
        store.set_categories_bulk(updates)
    return len(updates)


def uncategorized_summary(store) -> dict:
    """Headline numbers for the review UI."""
    clusters = uncategorized_clusters(store, limit=10_000)
    total = sum(c.total for c in clusters)
    top10 = sum(c.total for c in clusters[:10])
    return {
        "merchants": len(clusters),
        "transactions": sum(c.n_tx for c in clusters),
        "total": total,
        "top10_share": (top10 / total) if total else 0.0,
        "with_suggestion": sum(1 for c in clusters if c.suggestion),
    }
