"""NPDA: in-app deposit accounts with automatic pre-booking of new issues.

Modelled on India Post's Philatelic Deposit Account, where collectors keep money on deposit and
new issues are sent to them automatically. India Post has no public API for opening a real PDA,
so this is the in-app equivalent:

* Opening an account is one step: identity and dispatch details come from the user's profile.
* Deposits are simulated (no payment gateway is connected).
* When a postal circle publishes an issue, `fulfil_new_issue` reserves copies for every matching
  subscriber and debits their balance, oldest account first, until the issue's stock runs out.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import or_, select, update

from ..extensions import db
from ..models import (
    NPDA_ACTIVE,
    NPDA_CLOSED,
    NPDA_SCOPE_ALL,
    NPDA_SCOPE_HOME,
    POSTAL_CIRCLES,
    Issue,
    NpdaAccount,
    NpdaTransaction,
    Prebooking,
    User,
)
from .errors import Forbidden, ValidationError
from .marketplace import parse_int, parse_price
from .notifications import notify

MIN_OPENING_DEPOSIT = Decimal("200.00")
MAX_DEPOSIT = Decimal("100000.00")


def _circle_code(circle: str) -> str:
    words = circle.split()
    return (words[0][0] + words[1][0]) if len(words) > 1 else circle[:2]


def _record(account: NpdaAccount, kind: str, amount: Decimal, description: str, issue_id=None):
    db.session.add(
        NpdaTransaction(
            account=account,
            kind=kind,
            amount=amount,
            balance_after=account.balance,
            description=description,
            issue_id=issue_id,
        )
    )


def _parse_preferences(data) -> tuple[int, str]:
    copies = parse_int(data.get("copies_per_issue", 1), "Copies per issue", minimum=1, maximum=10)
    scope = data.get("scope") or NPDA_SCOPE_HOME
    if scope not in (NPDA_SCOPE_HOME, NPDA_SCOPE_ALL):
        raise ValidationError("Choose which circles' issues to receive.")
    return copies, scope


def open_account(user: User, data) -> NpdaAccount:
    """Open an account using the profile's details. One account per user."""
    if user.is_circle:
        raise Forbidden("Postal circle accounts can't open an NPDA account.")
    if user.npda is not None and user.npda.status == NPDA_ACTIVE:
        raise ValidationError("You already have an active NPDA account.")
    if not user.profile_complete:
        raise ValidationError(
            "Complete your profile first: full name, phone and dispatch address are needed."
        )
    circle = data.get("home_circle")
    if circle not in POSTAL_CIRCLES:
        raise ValidationError("Choose your home postal circle.")
    copies, scope = _parse_preferences(data)
    deposit = parse_price(data.get("initial_deposit"), "Opening deposit")
    if deposit < MIN_OPENING_DEPOSIT:
        raise ValidationError(f"The opening deposit must be at least ₹{MIN_OPENING_DEPOSIT}.")
    if deposit > MAX_DEPOSIT:
        raise ValidationError(f"A single deposit can be at most ₹{MAX_DEPOSIT}.")

    account = user.npda
    if account is None:  # a closed account is reopened, keeping its number and history
        account = NpdaAccount(user=user)
        db.session.add(account)
    account.home_circle = circle
    account.copies_per_issue = copies
    account.scope = scope
    account.status = NPDA_ACTIVE
    account.balance = deposit
    db.session.flush()
    if not account.account_number:
        account.account_number = f"NPDA-{_circle_code(circle).upper()}-{account.id:06d}"
    _record(account, "deposit", deposit, "Opening deposit")
    notify(
        user.id,
        f"Your NPDA account {account.account_number} is active. New issues will be "
        f"reserved for you automatically ({copies} per issue).",
        "/npda",
    )
    db.session.commit()
    return account


def _require_active(account: NpdaAccount | None, user: User) -> NpdaAccount:
    if account is None or account.user_id != user.id or account.status != NPDA_ACTIVE:
        raise ValidationError("You don't have an active NPDA account.")
    return account


def deposit(user: User, amount) -> NpdaAccount:
    account = _require_active(user.npda, user)
    amount = parse_price(amount, "Deposit amount")
    if amount > MAX_DEPOSIT:
        raise ValidationError(f"A single deposit can be at most ₹{MAX_DEPOSIT}.")
    db.session.execute(
        update(NpdaAccount)
        .where(NpdaAccount.id == account.id)
        .values(balance=NpdaAccount.balance + amount)
        .execution_options(synchronize_session=False)
    )
    db.session.refresh(account)
    _record(account, "deposit", amount, "Deposit")
    db.session.commit()
    return account


def update_preferences(user: User, data) -> NpdaAccount:
    account = _require_active(user.npda, user)
    account.copies_per_issue, account.scope = _parse_preferences(data)
    if data.get("home_circle") in POSTAL_CIRCLES:
        account.home_circle = data["home_circle"]
    db.session.commit()
    return account


def close_account(user: User) -> NpdaAccount:
    """Close the account and refund the remaining balance."""
    account = _require_active(user.npda, user)
    refund = account.balance
    account.balance = Decimal("0")
    account.status = NPDA_CLOSED
    _record(account, "refund", refund, "Account closed, balance refunded")
    notify(user.id, f"NPDA account {account.account_number} closed. ₹{refund} refunded.", "/npda")
    db.session.commit()
    return account


def fulfil_new_issue(issue: Issue) -> dict:
    """Reserve copies of a newly published issue for every matching NPDA subscriber.

    Each subscriber is handled with the same race-safe pattern as orders: stock and balance are
    each taken with a conditional UPDATE, and stock is put back if the balance turns out to be
    too low. Returns counts for the circle's confirmation message.
    """
    circle = issue.circle.postal_circle
    accounts = db.session.scalars(
        select(NpdaAccount)
        .where(
            NpdaAccount.status == NPDA_ACTIVE,
            or_(NpdaAccount.scope == NPDA_SCOPE_ALL, NpdaAccount.home_circle == circle),
        )
        .order_by(NpdaAccount.created_at, NpdaAccount.id)
    ).all()

    result = {"booked": 0, "low_balance": 0, "sold_out": 0}
    for account in accounts:
        copies = account.copies_per_issue
        cost = issue.denomination * copies

        took_stock = db.session.execute(
            update(Issue)
            .where(Issue.id == issue.id, Issue.prebook_available >= copies)
            .values(prebook_available=Issue.prebook_available - copies)
            .execution_options(synchronize_session=False)
        ).rowcount
        if not took_stock:
            result["sold_out"] += 1
            notify(
                account.user_id,
                f"'{issue.title}' sold out before your NPDA standing order could be filled.",
                "/stamp_store",
            )
            continue

        took_money = db.session.execute(
            update(NpdaAccount)
            .where(NpdaAccount.id == account.id, NpdaAccount.balance >= cost)
            .values(balance=NpdaAccount.balance - cost)
            .execution_options(synchronize_session=False)
        ).rowcount
        if not took_money:
            db.session.execute(  # give the copies back
                update(Issue)
                .where(Issue.id == issue.id)
                .values(prebook_available=Issue.prebook_available + copies)
                .execution_options(synchronize_session=False)
            )
            result["low_balance"] += 1
            notify(
                account.user_id,
                f"NPDA: couldn't reserve '{issue.title}' (₹{cost} needed). "
                "Top up your balance to keep receiving new issues.",
                "/npda",
            )
            continue

        db.session.refresh(account)
        db.session.add(Prebooking(issue_id=issue.id, user_id=account.user_id, quantity=copies))
        _record(account, "debit", cost, f"Auto pre-booking: {copies} × {issue.title}", issue.id)
        notify(
            account.user_id,
            f"NPDA: {copies} × '{issue.title}' reserved for you automatically. "
            f"₹{cost} debited, balance ₹{account.balance}.",
            "/my-collection",
        )
        result["booked"] += 1

    db.session.commit()
    db.session.refresh(issue)
    return result
