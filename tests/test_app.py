from copy import deepcopy
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import sessionmaker

from app import main
from app.db import Base, make_engine


class FakeKeitaro:
    def __init__(self):
        self.campaign_rows = []
        self.stream_rows = {}
        self.calls = []
        self.lose_create_response = False
        self.fail_push = False

    def campaigns(self):
        return deepcopy(self.campaign_rows)

    def streams(self, campaign_id):
        return deepcopy([row for row in self.stream_rows.values() if row["campaign_id"] == campaign_id])

    def request(self, method, path, **kwargs):
        data = kwargs.get("json")
        self.calls.append((method, path, data))
        if path == "campaigns" and method == "POST":
            row = {"id": len(self.campaign_rows) + 1, **data}
            self.campaign_rows.append(row)
            if self.lose_create_response:
                self.lose_create_response = False
                raise HTTPException(502, "Response lost")
            return deepcopy(row)
        if path.startswith("campaigns/") and method == "PUT":
            return data
        if path == "streams" and method == "POST":
            row = {"id": len(self.stream_rows) + 10, **data}
            self.stream_rows[row["id"]] = row
            return deepcopy(row)
        if path.startswith("streams/"):
            row = self.stream_rows[int(path.split("/")[1])]
            if method == "PUT":
                if self.fail_push:
                    raise HTTPException(502, "Unavailable")
                row.update(data)
            return deepcopy(row)
        if path.startswith("offers/"):
            return {"id": int(path.split("/")[1]), "state": "active"}
        raise AssertionError((method, path))


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = make_engine("sqlite:///" + str(tmp_path / "test.db"))
    Base.metadata.create_all(engine)
    monkeypatch.setattr(main, "Session", sessionmaker(engine, expire_on_commit=False))
    fake = FakeKeitaro()
    monkeypatch.setattr(main, "kt", fake)
    with TestClient(main.app) as client:
        yield client, fake
    engine.dispose()


def create(client, **changes):
    data = {
        "request_id": str(uuid4()), "name": "test_tyutikov_test",
        "geo": ["au", "AU", " mx "], "offer_id": 10,
        "domain_id": 1, "group_id": 2, "traffic_source_id": 3,
    }
    data.update(changes)
    return client.post("/api/campaigns", json=data), data


def load_draft(client, fake, offers=None):
    fake.stream_rows[10] = {
        "id": 10, "campaign_id": 1, "schema": "landings", "position": 2,
        "name": "Offers", "filters": [], "action_type": "http",
        "offers": offers or [{"offer_id": 10, "share": 100, "state": "active"}],
    }
    response = client.get("/api/campaigns/1/streams")
    assert response.status_code == 200
    return response.json()[0]


def edit(client, draft, action, offer_id, share=None):
    return client.patch(
        "/api/campaigns/1/streams/10/draft",
        json={"revision": draft["revision"], "action": action, "offer_id": offer_id, "share": share},
    )


def test_creation_geo_order_and_idempotency(client):
    client, fake = client
    response, data = create(client)
    assert response.status_code == 200
    assert client.post("/api/campaigns", json=data).json() == response.json()
    assert len(fake.campaign_rows) == 1
    assert fake.campaign_rows[0]["type"] == "position"
    assert fake.campaign_rows[0]["state"] == "disabled"
    first, second = fake.stream_rows.values()
    assert first["position"] == 1 and first["filters"][0]["payload"] == ["AU", "MX"]
    assert first["action_options"]["url"] == "https://google.com"
    assert second["type"] == "default" and second["offers"][0]["share"] == 100
    assert fake.calls[-1][2] == {"state": "active"}
    data["name"] += "changed"
    assert client.post("/api/campaigns", json=data).status_code == 409


def test_recovery_after_lost_create_response(client):
    client, fake = client
    fake.lose_create_response = True
    response, data = create(client)
    assert response.status_code == 502
    assert client.post("/api/campaigns", json=data).status_code == 200
    assert len(fake.campaign_rows) == 1
    assert len(fake.stream_rows) == 2


@pytest.mark.parametrize("changes", [
    {"name": "wrong_prefix"}, {"geo": ["ZZ"]}, {"geo": []}, {"offer_id": 0},
])
def test_invalid_creation_has_no_side_effects(client, changes):
    client, fake = client
    response, _ = create(client, **changes)
    assert response.status_code == 422
    assert not fake.calls


def test_add_remove_restore_pin_and_cancel(client):
    client, fake = client
    draft = load_draft(client, fake)
    draft = edit(client, draft, "add", 20).json()
    draft = edit(client, draft, "add", 30).json()
    assert [row["share"] for row in draft["offers"]] == [34, 33, 33]
    draft = edit(client, draft, "pin", 10, 25).json()
    assert [row["share"] for row in draft["offers"]] == [25, 38, 37]
    draft = edit(client, draft, "remove", 20).json()
    assert [row["share"] for row in draft["offers"]] == [25, 0, 75]
    draft = edit(client, draft, "restore", 20).json()
    assert [row["share"] for row in draft["offers"]] == [25, 38, 37]
    assert client.get("/api/campaigns/1/streams").json()[0]["offers"] == draft["offers"]
    assert not any(method == "PUT" for method, _, _ in fake.calls)
    reset = client.post("/api/campaigns/1/streams/10/reset", json={"revision": draft["revision"]})
    assert not reset.json()["dirty"]
    assert len(reset.json()["offers"]) == 1


def test_reject_last_offer_duplicate_stale_revision_and_impossible_pins(client):
    client, fake = client
    draft = load_draft(client, fake)
    assert edit(client, draft, "remove", 10).status_code == 422
    assert edit(client, draft, "add", 10).status_code == 422
    newer = edit(client, draft, "add", 20).json()
    assert edit(client, draft, "add", 30).status_code == 409
    pinned = edit(client, newer, "pin", 10, 60).json()
    assert edit(client, pinned, "pin", 20, 50).status_code == 422
    assert edit(client, pinned, "remove", 20).status_code == 422


def test_push_preserves_filters_and_handles_external_conflict(client):
    client, fake = client
    draft = load_draft(client, fake)
    draft = edit(client, draft, "add", 20).json()
    fake.stream_rows[10]["name"] = "External change"
    url = "/api/campaigns/1/streams/10/push"
    assert client.post(url, json={"revision": draft["revision"]}).status_code == 409
    assert not any(method == "PUT" for method, _, _ in fake.calls)
    fake.stream_rows[10]["name"] = "Offers"
    response = client.post(url, json={"revision": draft["revision"]})
    assert response.status_code == 200 and not response.json()["dirty"]
    put = next(data for method, _, data in fake.calls if method == "PUT")
    assert set(put) == {"offers"}


def test_failed_push_keeps_draft_for_retry(client):
    client, fake = client
    draft = load_draft(client, fake)
    draft = edit(client, draft, "add", 20).json()
    fake.fail_push = True
    url = "/api/campaigns/1/streams/10/push"
    assert client.post(url, json={"revision": draft["revision"]}).status_code == 502
    stored = client.get("/api/campaigns/1/streams").json()[0]
    assert stored["dirty"] and stored["revision"] == draft["revision"]
    fake.fail_push = False
    assert client.post(url, json={"revision": draft["revision"]}).status_code == 200


def test_retry_after_successful_put_but_lost_response(client):
    client, fake = client
    draft = load_draft(client, fake)
    draft = edit(client, draft, "add", 20).json()
    fake.stream_rows[10]["offers"] = main.offer_payload(draft["offers"])
    response = client.post("/api/campaigns/1/streams/10/push", json={"revision": draft["revision"]})
    assert response.status_code == 200 and not response.json()["dirty"]
    assert not any(method == "PUT" for method, _, _ in fake.calls)
