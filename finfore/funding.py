"""Goal earmarks: an auditable allocation of existing cash, never bank transfers."""
from __future__ import annotations

from datetime import date

from .models import AccountType
from .categorize import BUSINESS_CATEGORY
from .spending_plan import money

ACTIONS = {'contribute': 'Set aside money', 'assign': 'Locate existing savings',
           'purchase': 'Spend toward goal', 'release': 'Release savings'}


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
    from .history import account_balance_history
    history = account_balance_history(store)
    balances = {a.id: float(history[a.id].iloc[-1]) for a in store.accounts()
                if a.type in (AccountType.CHECKING, AccountType.SAVINGS)}
    net_cash = sum(balances.values()) + sum(float(history[a.id].iloc[-1])
        for a in store.accounts() if a.type == AccountType.CREDIT)
    claims = {}
    goals = [g for g in store.goals() if g.kind == 'savings']
    for goal in goals:
        for aid, amount in goal_buckets(store, goal.id).items():
            if aid is not None:
                claims[aid] = claims.get(aid, 0.0) + amount
    return ({aid: round(balance - claims.get(aid, 0.0), 2) for aid, balance in balances.items()},
            round(net_cash - sum(g.saved_amount for g in goals), 2))


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
        if account is None or account.type not in (AccountType.CHECKING, AccountType.SAVINGS):
            raise ValueError('Choose a checking or savings account that holds this money.')
    buckets = goal_buckets(store, goal_id)
    if action in ('purchase', 'release') and value > buckets.get(account_id, 0.0) + .005:
        raise ValueError('This goal does not have that much saved in the selected account.')
    if action == 'contribute' and value + goal.saved_amount + goal.spent_amount > goal.amount + .005:
        raise ValueError('This exceeds the goal target. Raise the target before adding more.')
    if action in ('contribute', 'assign'):
        capacities, free = cash_capacity(store)
        if action == 'assign':
            if account_id is None or value > buckets.get(None, 0.0) + .005:
                raise ValueError('Choose an account and an amount within existing unlocated savings.')
        elif value > max(0.0, free) + .005:
            raise ValueError('Not enough unassigned net cash. Review balances and other goal savings.')
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
