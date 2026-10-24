"""Auditable goal earmarks of existing holdings, without transfers or sales."""
from __future__ import annotations

from datetime import date
from dataclasses import dataclass, field

from .models import AccountType
from .categorize import BUSINESS_CATEGORY
from .spending_plan import money

ACTIONS = {'contribute': 'Set aside money', 'assign': 'Locate existing savings',
           'purchase': 'Spend toward goal', 'release': 'Release savings'}
FUNDING_TYPES = (AccountType.CHECKING, AccountType.SAVINGS, AccountType.INVESTMENT)


def goal_buckets(store, goal_id: int) -> dict[int | None, float]:
    goal = next((g for g in store.goals() if g.id == goal_id), None)
    if goal is None:
        raise ValueError('This goal no longer exists.')
    buckets = {}
    for entry in store.goal_activity(goal_id):
        aid = entry['account_id']
        if aid is None:
            continue
        sign = -1 if entry['action'] in ('purchase', 'release') else 1
        buckets[aid] = buckets.get(aid, 0.0) + sign * entry['amount']
    buckets = {aid: round(value, 2) for aid, value in buckets.items()}
    buckets[None] = round(goal.saved_amount - sum(buckets.values()), 2)
    return buckets


def cash_capacity(store) -> tuple[dict[int, float], float]:
    """Cash capacity only; located investment earmarks do not consume cash."""
    return _capacity(store, (AccountType.CHECKING, AccountType.SAVINGS))


def funding_capacity(store) -> tuple[dict[int, float], float]:
    """Unclaimed tracked assets, after card balances; not spendable cash.

    An investment earmark classifies an existing holding without selling it.
    Unlocated savings remain a household claim, without inventing an account.
    """
    return _capacity(store, FUNDING_TYPES)


def _capacity(store, eligible):
    from .history import account_balance_history
    history = account_balance_history(store)
    balances = {a.id: float(history[a.id].iloc[-1]) for a in store.accounts()
                if a.type in eligible}
    net_assets = sum(balances.values()) + sum(float(history[a.id].iloc[-1])
        for a in store.accounts() if a.type == AccountType.CREDIT)
    claims = {}
    goals = [g for g in store.goals() if g.kind == 'savings']
    unlocated = 0.0
    for goal in goals:
        for aid, amount in goal_buckets(store, goal.id).items():
            if aid is None:
                unlocated += amount
            elif aid in balances:
                claims[aid] = claims.get(aid, 0.0) + amount
    return ({aid: round(balance - claims.get(aid, 0.0), 2) for aid, balance in balances.items()},
            round(net_assets - sum(claims.values()) - unlocated, 2))


@dataclass
class GoalHolding:
    id: int
    name: str
    target: float
    saved: float
    spent: float
    unlocated: float
    accounts: dict[int, float] = field(default_factory=dict)

    @property
    def remaining(self):
        return round(max(0.0, self.target - self.saved - self.spent), 2)

    @property
    def status(self):
        if self.remaining <= .005:
            return 'Completed' if self.spent >= self.target - .005 else 'Funded'
        return 'Partially funded' if self.saved + self.spent > .005 else 'Not funded'


@dataclass
class AccountHolding:
    id: int
    name: str
    kind: AccountType
    balance: float
    goals: dict[int, float] = field(default_factory=dict)

    @property
    def unassigned(self):
        return round(self.balance - sum(self.goals.values()), 2)


@dataclass
class FundingOverview:
    accounts: list[AccountHolding]
    goals: list[GoalHolding]
    cash: float
    investments: float
    card_balance: float
    unassigned: float


def funding_overview(store) -> FundingOverview:
    """Current balances and recorded goal sources, with no inferred allocation."""
    from .history import account_balance_history
    history = account_balance_history(store)
    accounts = [AccountHolding(a.id, a.name, a.type, round(float(history[a.id].iloc[-1]), 2))
                for a in store.accounts() if a.type in FUNDING_TYPES]
    by_id = {a.id: a for a in accounts}
    rank = {key: i for i, key in enumerate(store.plan_order())}
    goals = []
    for g in sorted(store.goals(), key=lambda g: (rank.get(f'goal:{g.id}', len(rank)), g.name)):
        if g.kind != 'savings':
            continue
        buckets = goal_buckets(store, g.id)
        located = {aid: amount for aid, amount in buckets.items() if aid in by_id and abs(amount) > .005}
        goals.append(GoalHolding(g.id, g.name, g.amount, g.saved_amount, g.spent_amount,
                                 round(g.saved_amount - sum(located.values()), 2), located))
        for aid, value in located.items():
            by_id[aid].goals[g.id] = value
    cash = round(sum(a.balance for a in accounts if a.kind != AccountType.INVESTMENT), 2)
    investments = round(sum(a.balance for a in accounts if a.kind == AccountType.INVESTMENT), 2)
    cards = round(sum(float(history[a.id].iloc[-1]) for a in store.accounts()
                      if a.type == AccountType.CREDIT), 2)
    return FundingOverview(accounts, goals, cash, investments, cards,
                           round(cash + investments + cards - sum(g.saved for g in goals), 2))


def validate_goal_edit(store, goal_id, amount, kind, saved):
    goal = next((g for g in store.goals() if g.id == goal_id), None)
    if goal is None:
        raise ValueError('This goal no longer exists.')
    activity = store.goal_activity(goal_id)
    if activity and ((kind is not None and kind != goal.kind) or
                     (saved is not None and money(saved) != round(goal.saved_amount, 2))):
        raise ValueError('Use Goal activity to change savings once funding has been recorded.')
    target = money(amount) if amount is not None else goal.amount
    savings = money(saved) if saved is not None else goal.saved_amount
    if amount is not None and target <= 0:
        raise ValueError('The goal target must be greater than $0.')
    if (kind or goal.kind) == 'savings' and savings + goal.spent_amount > target + .005:
        raise ValueError('The target cannot be below money saved plus purchases already recorded.')


ACTION_HELP = {
    'contribute': 'New money you are putting toward this goal. Adds to Still saved and '
                  'claims that amount in the account you choose.',
    'assign': 'Says which account holds money already counted in Still saved. Moves it '
              'from "no account" to that account; Still saved does not change.',
    'purchase': 'You bought what you were saving for. Moves the amount from Still saved '
                'to Purchases, so the goal keeps counting it as progress.',
    'release': 'Takes money back out of the goal without buying anything, for example to '
               'cover something else. Lowers Still saved and progress; set aside again to rebuild.',
}


def delete_activity(store, activity_id):
    """Remove one activity entry and undo its effect on the goal's totals.

    Refused when later activity rests on it: deleting a contribution whose money
    was since spent or released, or a location the money has since left, would
    leave the goal holding less than nothing somewhere.
    """
    row = store.con.execute('SELECT * FROM goal_activity WHERE id=?', (int(activity_id),)).fetchone()
    if row is None:
        raise ValueError('This activity no longer exists.')
    entry = dict(row)
    goal = next((g for g in store.goals() if g.id == entry['goal_id']), None)
    if goal is None:
        raise ValueError('This goal no longer exists.')
    value, action = float(entry['amount']), entry['action']
    saved = goal.saved_amount + (-value if action == 'contribute' else
                                 value if action in ('purchase', 'release') else 0)
    spent = goal.spent_amount - (value if action == 'purchase' else 0)
    buckets = goal_buckets(store, goal.id)
    aid = entry['account_id']
    if aid is not None:
        buckets[aid] = buckets.get(aid, 0.0) + (value if action in ('purchase', 'release') else -value)
    buckets[None] = saved - sum(v for k, v in buckets.items() if k is not None)
    if saved < -.005 or spent < -.005 or any(v < -.005 for v in buckets.values()):
        raise ValueError('Later activity depends on this entry: some of this money was since '
                         'spent, released or moved. Delete that later activity first.')
    if action == 'release':
        # Undoing a release claims the money again, so it must still be
        # unclaimed, exactly as a new contribution must. A purchase is different:
        # its money has usually left the account already.
        capacities, free = funding_capacity(store)
        if value > max(0.0, free) + .005 or (
                aid is not None and value > max(0.0, capacities.get(aid, 0.0)) + .005):
            raise ValueError('This money is no longer free: other goals or spending now use '
                             'it. Release it from another goal or check the account balance first.')
    with store.con:
        store.con.execute('DELETE FROM goal_activity WHERE id=?', (int(activity_id),))
        store.con.execute('UPDATE goals SET saved_amount=?,spent_amount=? WHERE id=?',
                          (round(max(0.0, saved), 2), round(max(0.0, spent), 2), goal.id))


def record_activity(store, goal_id, action, amount, when, account_id, transaction_id, note):
    value = money(amount)
    goal = next((g for g in store.goals() if g.id == goal_id), None)
    if goal is None or goal.kind != 'savings':
        raise ValueError('Select a savings goal first.')
    if action not in ACTIONS or value <= 0:
        raise ValueError('Select an activity and enter an amount greater than $0.')
    if not isinstance(when, date) or when > date.today():
        raise ValueError('Record money already set aside or spent, using today or an earlier date.')
    if account_id is not None:
        account = store.account(int(account_id))
        if account is None or account.type not in FUNDING_TYPES:
            raise ValueError('Choose a checking, savings or investment account that holds this money.')
    buckets = goal_buckets(store, goal_id)
    if action in ('purchase', 'release') and value > buckets.get(account_id, 0.0) + .005:
        raise ValueError('This goal does not have that much saved in the selected account.')
    if action == 'contribute' and value + goal.saved_amount + goal.spent_amount > goal.amount + .005:
        raise ValueError('This exceeds the goal target. Raise the target before adding more.')
    if action in ('contribute', 'assign'):
        capacities, free = funding_capacity(store)
        if action == 'assign':
            if account_id is None or value > buckets.get(None, 0.0) + .005:
                raise ValueError('Choose an account and an amount within existing unlocated savings.')
        elif value > max(0.0, free) + .005:
            raise ValueError('Not enough unassigned funding balances. Review accounts and other goal savings.')
        if account_id is not None and value > max(0.0, capacities[account_id]) + .005:
            raise ValueError('This account does not have enough money left after its other goal earmarks.')
    tx_hash = None
    if transaction_id is not None:
        if action not in ('contribute', 'purchase'):
            raise ValueError('Link a transaction only when setting aside money or recording a purchase.')
        tx = store.transactions()
        found = tx[tx.id == int(transaction_id)]
        if found.empty:
            raise ValueError('This transaction no longer exists.')
        row = found.iloc[0]
        if account_id is None or int(row.account_id) != account_id:
            raise ValueError('The transaction must belong to the selected account.')
        if row.date.date() != when:
            raise ValueError('Use the transaction date for linked activity.')
        signed = float(row.amount)
        if (action == 'contribute' and signed <= 0) or (action == 'purchase' and signed >= 0):
            raise ValueError('Link a deposit for savings or an expense for a purchase.')
        if row.category == BUSINESS_CATEGORY or (action == 'purchase' and row.category == 'Transfer'):
            raise ValueError('Choose a personal transaction for this goal.')
        usable = abs(signed)
        if signed > 0:
            usable -= float(row.business_reimbursement)
        tx_hash = store.con.execute('SELECT import_hash FROM transactions WHERE id=?',
                                   (int(transaction_id),)).fetchone()[0]
        used = store.con.execute('SELECT COALESCE(SUM(amount),0) FROM goal_activity '
                                 'WHERE transaction_hash=?', (tx_hash,)).fetchone()[0]
        if value + used > usable + .005:
            raise ValueError('This transaction is already allocated, or the amount exceeds its unallocated portion.')
        note = f'{note.strip()} | Transaction: {row.description} ({signed:+,.2f})'.strip(' |')
    saved = goal.saved_amount + (value if action == 'contribute' else
                                -value if action in ('purchase', 'release') else 0)
    spent = goal.spent_amount + (value if action == 'purchase' else 0)
    with store.con:
        cur = store.con.execute('INSERT INTO goal_activity '
            '(goal_id,when_date,action,amount,account_id,transaction_id,transaction_hash,note) '
            'VALUES (?,?,?,?,?,?,?,?)', (goal_id, when.isoformat(), action, value,
                                       account_id, transaction_id, tx_hash, note.strip()))
        store.con.execute('UPDATE goals SET saved_amount=?,spent_amount=? WHERE id=?',
                          (round(saved, 2), round(spent, 2), goal_id))
    return cur.lastrowid
