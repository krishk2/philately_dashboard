from decimal import Decimal

import pytest
from werkzeug.datastructures import FileStorage

from app.models import Issue, Notification, NpdaAccount, Prebooking
from app.services import marketplace as svc
from app.services import npda
from app.services.errors import Forbidden, ValidationError

from .conftest import login, make_user, png


def complete_profile(user, db):
    user.full_name = user.username.title()
    user.phone = "9876543210"
    user.address = "12 MG Road, Vijayawada 520001"
    db.session.commit()
    return user


def open_for(user, db, **overrides):
    complete_profile(user, db)
    data = {
        "home_circle": "Andhra Pradesh",
        "copies_per_issue": "1",
        "scope": "home",
        "initial_deposit": "200",
    }
    data.update(overrides)
    return npda.open_account(user, data)


def publish(circle, *, denomination="25", stock=10, seed=60):
    return svc.create_issue(
        circle,
        {
            "title": "Araku Coffee",
            "issue_date": "2026-01-26",
            "denomination": denomination,
            "prebook_available": stock,
        },
        FileStorage(stream=png(seed), filename="a.png"),
    )


# ---------- opening ----------


def test_open_requires_complete_profile(alice):
    with pytest.raises(ValidationError, match="Complete your profile"):
        npda.open_account(alice, {"home_circle": "Andhra Pradesh", "initial_deposit": "500"})


def test_open_account_number_balance_and_history(alice, db):
    account = open_for(alice, db, initial_deposit="500")
    assert account.account_number == f"NPDA-AP-{account.id:06d}"
    assert account.balance == Decimal("500.00")
    assert [(t.kind, t.amount) for t in account.transactions] == [("deposit", Decimal("500.00"))]
    assert db.session.query(Notification).filter_by(user_id=alice.id).count() == 1


def test_minimum_opening_deposit(alice, db):
    with pytest.raises(ValidationError, match="at least"):
        open_for(alice, db, initial_deposit="199.99")


def test_one_active_account_per_user(alice, db):
    open_for(alice, db)
    with pytest.raises(ValidationError, match="already have"):
        open_for(alice, db)


def test_circle_cannot_open_account(circle, db):
    with pytest.raises(Forbidden):
        open_for(circle, db)


def test_deposit_close_and_reopen_keeps_number(alice, db):
    account = open_for(alice, db)
    number = account.account_number
    npda.deposit(alice, "300")
    assert account.balance == Decimal("500.00")
    npda.close_account(alice)
    assert (account.status, account.balance) == ("closed", Decimal("0"))
    assert account.transactions[0].kind == "refund"
    reopened = open_for(alice, db)
    assert reopened.account_number == number
    assert reopened.is_active


# ---------- automatic pre-booking of new issues ----------


def test_new_issue_is_reserved_for_subscribers(circle, alice, db):
    account = open_for(alice, db, copies_per_issue="2", initial_deposit="200")
    issue = publish(circle, denomination="25", stock=10)
    assert issue.npda_result == {"booked": 1, "low_balance": 0, "sold_out": 0}
    booking = db.session.query(Prebooking).one()
    assert (booking.user_id, booking.quantity) == (alice.id, 2)
    assert db.session.get(NpdaAccount, account.id).balance == Decimal("150.00")
    assert db.session.get(Issue, issue.id).prebook_available == 8
    debit = account.transactions[0]
    assert (debit.kind, debit.amount, debit.issue_id) == ("debit", Decimal("50.00"), issue.id)


def test_low_balance_skips_and_puts_stock_back(circle, alice, db):
    open_for(alice, db, copies_per_issue="10", initial_deposit="200")
    issue = publish(circle, denomination="25", stock=50)  # needs ₹250
    assert issue.npda_result["low_balance"] == 1
    assert db.session.query(Prebooking).count() == 0
    assert db.session.get(Issue, issue.id).prebook_available == 50
    assert (
        "Top up" in db.session.query(Notification).order_by(Notification.id.desc()).first().message
    )


def test_oldest_account_first_when_stock_runs_out(circle, alice, bob, db):
    open_for(alice, db)
    open_for(bob, db)
    issue = publish(circle, stock=1)
    assert issue.npda_result == {"booked": 1, "low_balance": 0, "sold_out": 1}
    assert db.session.query(Prebooking).one().user_id == alice.id


def test_scope_home_vs_all(circle, alice, bob, db):
    open_for(alice, db, home_circle="Karnataka", scope="home")
    open_for(bob, db, home_circle="Karnataka", scope="all")
    publish(circle)  # an Andhra Pradesh issue
    assert [b.user_id for b in db.session.query(Prebooking)] == [bob.id]


def test_closed_accounts_get_nothing(circle, alice, db):
    open_for(alice, db)
    npda.close_account(alice)
    assert publish(circle).npda_result["booked"] == 0


# ---------- pages ----------


def test_npda_page_flow(client, alice, circle, db):
    complete_profile(alice, db)
    login(client, "alice")
    assert b"Create your NPDA Account" in client.get("/npda").data
    client.post(
        "/npda/open",
        data={
            "home_circle": "Andhra Pradesh",
            "copies_per_issue": "1",
            "scope": "home",
            "initial_deposit": "250",
        },
    )
    page = client.get("/npda").data
    assert b"NPDA-AP-" in page and "₹250.00".encode() in page
    client.post("/npda/deposit", data={"amount": "100"})
    client.post(
        "/npda/preferences",
        data={"copies_per_issue": "3", "scope": "all", "home_circle": "Andhra Pradesh"},
    )
    account = db.session.query(NpdaAccount).one()
    assert (account.balance, account.copies_per_issue, account.scope) == (
        Decimal("350.00"),
        3,
        "all",
    )
    client.post("/npda/close")
    assert db.session.get(NpdaAccount, account.id).status == "closed"


def test_npda_page_asks_to_complete_profile(client, alice):
    login(client, "alice")
    page = client.get("/npda").data
    assert b"Complete your profile" in page


def test_circle_sees_npda_result_when_adding_stamp(client, circle, alice, db):
    open_for(alice, db)
    login(client, "ap_circle", circle="Andhra Pradesh")
    response = client.post(
        "/manage_stamps/add",
        data={
            "stampName": "Lepakshi",
            "stampDate": "2026-08-15",
            "stampPrice": "5",
            "stampStock": "100",
            "stampImage": (png(3), "ref.png"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert b"1 subscriber(s) booked automatically" in response.data


def test_sale_notifies_seller(client, alice, bob, db):
    listing = svc.create_listing(
        alice,
        {"title": "Tiger", "year": 1983, "price": "15", "quantity": 1},
        FileStorage(stream=png(4), filename="t.png"),
    )
    svc.place_order(bob, listing.id, 1, "Pune")
    note = db.session.query(Notification).filter_by(user_id=alice.id).one()
    assert "Sold!" in note.message


def test_notifications_page_and_mark_read(client, alice, db):
    other = make_user("carol")
    db.session.add(Notification(user_id=alice.id, message="Hello alice"))
    db.session.add(Notification(user_id=other.id, message="Not for alice"))
    db.session.commit()
    login(client, "alice")
    page = client.get("/notifications").data
    assert b"Hello alice" in page and b"Not for alice" not in page
    client.post("/notifications/read")
    assert db.session.query(Notification).filter_by(user_id=alice.id, is_read=False).count() == 0
