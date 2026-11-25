"""Load test ("swarm") for the National Philatelic Community, using Locust.

Three kinds of simulated users, weighted like real traffic:
  * Browser (most traffic): home, Stamp Mela with sorting, search, stamp pages, forums, store.
  * Collector: logs in, checks the dashboard, toggles the wishlist, pre-books and pays.
  * ApiClient: gets a token and pages through the JSON API.

Run against a STAGING copy, never production (it creates orders and pre-bookings):

    # web UI (open http://localhost:8089, choose users and spawn rate, press Start)
    uv run --with locust locust -f loadtest/locustfile.py --host https://staging.example.com

    # headless, e.g. 100 users, 10 new per second, for 5 minutes, CSV results
    uv run --with locust locust -f loadtest/locustfile.py --host https://staging.example.com \\
        --headless -u 100 -r 10 -t 5m --csv loadtest/results

Accounts: the demo seed users (alice, bob; password demo-password), or set LOADTEST_USERS to
"user:password,user2:password2". Rate limits will block many logins from one IP. For a load test,
set RATELIMIT_ENABLED=0 on the staging server, or run Locust from several machines.
"""

import os
import random
import re

from locust import HttpUser, between, task

CSRF_RE = re.compile(r'name="csrf-token" content="([^"]+)"')
TEST_CARD = {"cardNumber": "4111111111111111", "expiryDate": "12/99", "cvv": "123"}
USERS = [
    tuple(pair.split(":", 1))
    for pair in os.environ.get("LOADTEST_USERS", "alice:demo-password,bob:demo-password").split(",")
]
SEARCH_TERMS = ["gandhi", "temple", "tiger", "1948", "dance", "coffee", "birds", "zebra"]


def csrf_from(html: str) -> str:
    match = CSRF_RE.search(html)
    return match.group(1) if match else ""


class Browser(HttpUser):
    """An anonymous visitor."""

    weight = 6
    wait_time = between(1, 4)

    def on_start(self):
        listing = self.client.get("/api/v1/listings?per_page=50", name="/api/v1/listings").json()
        self.listing_ids = [item["id"] for item in listing.get("items", [])] or [1]

    @task(5)
    def home(self):
        self.client.get("/home")

    @task(4)
    def marketplace(self):
        sort = random.choice(["", "price_asc", "price_desc", "year_asc", "year_desc"])
        self.client.get(f"/buyer_home?sort={sort}", name="/buyer_home?sort=[sort]")

    @task(3)
    def stamp(self):
        self.client.get(f"/stamp/{random.choice(self.listing_ids)}", name="/stamp/[id]")

    @task(2)
    def search(self):
        self.client.get(f"/search?search={random.choice(SEARCH_TERMS)}", name="/search?search=[q]")

    @task(2)
    def forums(self):
        self.client.get("/forums")

    @task(1)
    def collectors(self):
        self.client.get("/collectors")

    @task(1)
    def store(self):
        self.client.get("/stamp_store")


class Collector(HttpUser):
    """A logged-in philatelist."""

    weight = 3
    wait_time = between(2, 6)

    def on_start(self):
        self.username, password = random.choice(USERS)
        page = self.client.get("/login").text
        response = self.client.post(
            "/login_philatelists",
            data={"username": self.username, "password": password, "csrf_token": csrf_from(page)},
            name="/login_philatelists",
            allow_redirects=False,
        )
        self.logged_in = response.status_code == 302
        listing = self.client.get("/api/v1/listings?per_page=50", name="/api/v1/listings").json()
        self.listing_ids = [item["id"] for item in listing.get("items", [])] or [1]
        issues = self.client.get("/api/v1/issues", name="/api/v1/issues").json()
        self.issue_ids = [item["id"] for item in issues.get("items", [])]

    @task(4)
    def dashboard(self):
        self.client.get("/dashboard")

    @task(2)
    def collection(self):
        self.client.get("/my-collection")

    @task(2)
    def wishlist_toggle(self):
        token = csrf_from(self.client.get("/buyer_home", name="/buyer_home").text)
        self.client.post(
            f"/wishlist/{random.choice(self.listing_ids)}",
            headers={"X-CSRFToken": token, "Accept": "application/json"},
            name="/wishlist/[id]",
        )

    @task(1)
    def prebook_and_pay(self):
        if not self.issue_ids:
            return
        issue_id = random.choice(self.issue_ids)
        page = self.client.get(f"/pre_book/{issue_id}", name="/pre_book/[id]").text
        response = self.client.post(
            "/submit_order",
            data={"stampType": issue_id, "quantity": 1, "csrf_token": csrf_from(page)},
            allow_redirects=False,
            name="/submit_order",
        )
        location = response.headers.get("Location", "")
        if "/payment/" not in location:
            return  # sold out / validation error: fine under load
        payment_page = self.client.get(location, name="/payment/[id]").text
        self.client.post(
            location,
            data={**TEST_CARD, "csrf_token": csrf_from(payment_page)},
            name="/payment/[id] (pay)",
        )


class ApiClient(HttpUser):
    """A third-party app using the REST API."""

    weight = 1
    wait_time = between(1, 3)

    def on_start(self):
        username, password = random.choice(USERS)
        token = (
            self.client.post(
                "/api/v1/auth/token", json={"username": username, "password": password}
            )
            .json()
            .get("token", "")
        )
        self.client.headers["Authorization"] = f"Bearer {token}"

    @task(3)
    def list_listings(self):
        page = random.randint(1, 3)
        self.client.get(f"/api/v1/listings?page={page}&per_page=20", name="/api/v1/listings?page")

    @task(2)
    def detail(self):
        self.client.get("/api/v1/listings/1", name="/api/v1/listings/[id]")

    @task(1)
    def me(self):
        self.client.get("/api/v1/me")
