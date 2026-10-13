"""Per-ledger history selection for forecasts; the ledger itself stays intact."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

import pandas as pd

from .models import RecurringItem
from .recurrence import detect

SETTING = "forecast_history"


@dataclass(frozen=True)
class ForecastHistory:
    mode: str = "auto"  # auto (legacy 78 samples), weeks, all, since
    weeks: int = 78
    since: date | None = None
    refit_recurring: bool = False

    @classmethod
    def from_setting(cls, value) -> "ForecastHistory":
        if not isinstance(value, dict):
            return cls()
        try:
            mode = value.get("mode", "auto")
            weeks = int(value.get("weeks", 78))
            since = date.fromisoformat(value["since"]) if value.get("since") else None
            if mode not in {"auto", "weeks", "all", "since"} or not 4 <= weeks <= 520:
                return cls()
            if mode == "since" and since is None:
                return cls()
            return cls(mode, weeks, since, bool(value.get("refit_recurring", False)))
        except (TypeError, ValueError):
            return cls()

    def to_setting(self) -> dict:
        return {"mode": self.mode, "weeks": self.weeks,
                "since": self.since.isoformat() if self.since else None,
                "refit_recurring": self.refit_recurring}

    def start(self, as_of: date) -> date | None:
        if self.mode == "weeks":
            return as_of - timedelta(weeks=self.weeks)
        return self.since if self.mode == "since" else None

    def transactions(self, tx: pd.DataFrame, as_of: date) -> pd.DataFrame:
        past = tx[tx["date"] <= pd.Timestamp(as_of)]
        start = self.start(as_of)
        return past[past["date"] >= pd.Timestamp(start)] if start else past

    def patterns(self, tx: pd.DataFrame, saved: list[RecurringItem],
                 as_of: date) -> tuple[list[RecurringItem], list[str]]:
        """Refit schedules in memory, retaining user-owned switches and links.

        Known keys remain excluded from random weeks even if the selected
        period has no repetitions sufficient to detect their schedule.
        """
        if not self.refit_recurring:
            return saved, []
        recent = self.transactions(tx, as_of)
        if self.mode == "auto" and not recent.empty:
            recent = recent[recent["date"] >= pd.Timestamp(as_of - timedelta(weeks=78))]
        known = {it.key: it for it in saved}
        fitted = []
        for item in detect(recent):
            old = known.get(item.key)
            if old:
                item = replace(item, active=old.active, kind=old.kind,
                               linked_account_id=old.linked_account_id,
                               id=old.id, name=old.name)
            fitted.append(item)
        keys = {it.key for it in fitted}
        missing = [it for it in saved if it.active and it.kind != "ignore"
                   and it.key not in keys]
        warnings = []
        if missing:
            warnings.append(
                f"{len(missing)} saved payday/bill pattern(s) were not detected in "
                "the selected history and are omitted from this forecast. "
                "Review Bills & payday; a short window can miss infrequent bills."
            )
        return fitted, warnings
