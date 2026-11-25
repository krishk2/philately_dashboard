from .conftest import png, token_for


def post_listing(client, headers, seed=1, **fields):
    data = {"title": "Tiger 1983", "year": "1983", "price": "15", "quantity": "2", **fields}
    data["image"] = (png(seed), "tiger.png")
    return client.post(
        "/api/v1/listings", data=data, headers=headers, content_type="multipart/form-data"
    )


def test_token_with_bad_credentials(client, alice):
    response = client.post("/api/v1/auth/token", json={"username": "alice", "password": "x"})
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_credentials"


def test_write_endpoints_require_token(client):
    response = client.post("/api/v1/listings")
    assert response.status_code == 401
    assert response.get_json() == {
        "error": {"code": "unauthorized", "message": "A valid bearer token is required."}
    }


def test_tampered_token_is_rejected(client, alice):
    headers = token_for(client, "alice")
    headers["Authorization"] += "x"
    assert client.get("/api/v1/me", headers=headers).status_code == 401


def test_password_change_revokes_tokens(client, alice, db):
    headers = token_for(client, "alice")
    assert client.get("/api/v1/me", headers=headers).status_code == 200
    alice.set_password("another-password")
    db.session.commit()
    assert client.get("/api/v1/me", headers=headers).status_code == 401


def test_create_and_fetch_listing(client, alice):
    headers = token_for(client, "alice")
    response = post_listing(client, headers)
    assert response.status_code == 201
    body = response.get_json()
    assert body["seller"] == "alice"
    assert body["price"] == "15.00"
    assert body["verification"]["status"] == "verified"

    fetched = client.get(f"/api/v1/listings/{body['id']}").get_json()
    assert fetched["title"] == "Tiger 1983"
    image = client.get(fetched["image_url"])
    assert image.status_code == 200
    assert image.mimetype == "image/png"


def test_create_listing_validation_error_shape(client, alice):
    response = post_listing(client, token_for(client, "alice"), price="-5")
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "validation_error"


def test_patch_and_delete_listing(client, alice, bob):
    alice_headers = token_for(client, "alice")
    listing_id = post_listing(client, alice_headers).get_json()["id"]

    bob_headers = token_for(client, "bob")
    forbidden = client.patch(
        f"/api/v1/listings/{listing_id}", json={"price": "1"}, headers=bob_headers
    )
    assert forbidden.status_code == 403

    patched = client.patch(
        f"/api/v1/listings/{listing_id}", json={"price": "18.5"}, headers=alice_headers
    )
    assert patched.get_json()["price"] == "18.50"

    assert client.delete(f"/api/v1/listings/{listing_id}", headers=alice_headers).status_code == 204
    assert client.get(f"/api/v1/listings/{listing_id}").status_code == 404


def test_list_listings_pagination_and_filters(client, alice):
    headers = token_for(client, "alice")
    for i in range(5):
        post_listing(client, headers, seed=i + 1, title=f"Stamp {i}", year=str(1990 + i))

    page = client.get("/api/v1/listings?per_page=2&page=2").get_json()
    assert page["total"] == 5
    assert page["pages"] == 3
    assert len(page["items"]) == 2

    filtered = client.get("/api/v1/listings?year=1992").get_json()
    assert [item["title"] for item in filtered["items"]] == ["Stamp 2"]


def test_invalid_query_param(client):
    response = client.get("/api/v1/listings?year=abc")
    assert response.status_code == 400
    assert "year" in response.get_json()["error"]["message"]


def test_order_flow(client, alice, bob):
    listing_id = post_listing(client, token_for(client, "alice")).get_json()["id"]
    bob_headers = token_for(client, "bob")

    created = client.post(
        "/api/v1/orders",
        json={"listing_id": listing_id, "quantity": 2, "shipping_address": "Balewadi, Pune"},
        headers=bob_headers,
    )
    assert created.status_code == 201
    assert created.get_json()["total"] == "30.00"

    again = client.post(
        "/api/v1/orders",
        json={"listing_id": listing_id, "quantity": 1, "shipping_address": "Pune"},
        headers=bob_headers,
    )
    assert again.status_code == 409
    assert again.get_json()["error"]["code"] == "out_of_stock"

    mine = client.get("/api/v1/orders", headers=bob_headers).get_json()
    assert len(mine["items"]) == 1


def test_verification_endpoints(client, alice):
    headers = token_for(client, "alice")
    listing_id = post_listing(client, headers).get_json()["id"]
    report = client.get(f"/api/v1/listings/{listing_id}/verification").get_json()
    assert {c["name"] for c in report["checks"]} == {
        "duplicate_photo",
        "official_issue",
        "ai_vision",
    }
    rerun = client.post(f"/api/v1/listings/{listing_id}/verification", headers=headers)
    assert rerun.status_code == 200


def test_unknown_api_route_returns_json(client):
    response = client.get("/api/v1/nope")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_prebook_via_api(client, circle, alice):
    from werkzeug.datastructures import FileStorage

    from app.services import marketplace as svc

    issue = svc.create_issue(
        circle,
        {
            "title": "Kuchipudi",
            "issue_date": "2025-03-01",
            "denomination": "10",
            "prebook_available": 3,
        },
        FileStorage(stream=png(9), filename="k.png"),
    )
    headers = token_for(client, "alice")
    ok = client.post(f"/api/v1/issues/{issue.id}/prebook", json={"quantity": 2}, headers=headers)
    assert ok.status_code == 201
    assert ok.get_json()["remaining"] == 1
    too_many = client.post(
        f"/api/v1/issues/{issue.id}/prebook", json={"quantity": 2}, headers=headers
    )
    assert too_many.status_code == 409
    issues = client.get("/api/v1/issues").get_json()["items"]
    assert issues[0]["postal_circle"] == "Andhra Pradesh"
