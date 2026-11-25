"""A simulated card payment gateway.

No money moves and no card data is stored or logged: the card is validated (Luhn checksum,
future expiry, CVV format) and a reference is returned, mimicking a gateway such as Razorpay or
Stripe in test mode. The test card 4000 0000 0000 0002 is always declined, so the failure path
can be tried too. Swap `charge` for a real gateway SDK to go live.
"""

from __future__ import annotations

import re
import secrets
from datetime import date
from decimal import Decimal

from .errors import DomainError

DECLINED_TEST_CARD = "4000000000000002"


class PaymentDeclined(DomainError):
    status_code = 402
    code = "payment_declined"


def luhn_valid(number: str) -> bool:
    digits = [int(d) for d in number]
    checksum = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _expiry_ok(value: str, today: date | None = None) -> bool:
    match = re.fullmatch(r"\s*(\d{1,2})\s*/\s*(\d{2}|\d{4})\s*", value or "")
    if not match:
        return False
    month, year = int(match[1]), int(match[2])
    if year < 100:
        year += 2000
    if not 1 <= month <= 12:
        return False
    today = today or date.today()
    return (year, month) >= (today.year, today.month)


def charge(amount: Decimal, card: dict, today: date | None = None) -> str:
    number = re.sub(r"[\s-]", "", card.get("number") or "")
    if not number.isdigit() or not 12 <= len(number) <= 19 or not luhn_valid(number):
        raise PaymentDeclined("That card number isn't valid.")
    if not _expiry_ok(card.get("expiry", ""), today):
        raise PaymentDeclined("The card's expiry date is invalid or in the past.")
    if not re.fullmatch(r"\d{3,4}", (card.get("cvv") or "").strip()):
        raise PaymentDeclined("Enter the 3 or 4 digit CVV.")
    if number == DECLINED_TEST_CARD:
        raise PaymentDeclined("The bank declined this payment (test card).")
    if amount <= 0:
        raise PaymentDeclined("Nothing to pay.")
    return f"SIM-{secrets.token_hex(6).upper()}"
