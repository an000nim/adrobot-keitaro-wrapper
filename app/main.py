from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import pycountry
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from .config import settings
from .db import Session
from .keitaro import Keitaro
from .models import Creation, Draft
from .shares import distribute

app = FastAPI(title="Adrobot · Keitaro wrapper")
static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static), name="static")
kt = Keitaro(settings)


@contextmanager
def write_session():
    # SQLite serializes edits across requests and uvicorn workers.
    with Session() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


class CreateCampaign(BaseModel):
    request_id: UUID
    name: str = Field(min_length=1, max_length=200)
    geo: list[str] = Field(min_length=1, max_length=249)
    offer_id: int = Field(gt=0)
    domain_id: int = Field(gt=0)
    group_id: int = Field(gt=0)
    traffic_source_id: int = Field(gt=0)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value.startswith(settings.campaign_prefix) or value == settings.campaign_prefix:
            raise ValueError(f"Название должно начинаться с {settings.campaign_prefix}")
        return value

    @field_validator("geo")
    @classmethod
    def clean_geo(cls, values):
        result = sorted({value.strip().upper() for value in values})
        if any(pycountry.countries.get(alpha_2=value) is None for value in result):
            raise ValueError("Укажите ISO 3166-1 alpha-2 коды, например AU, MX, RO")
        return result


class EditDraft(BaseModel):
    revision: int = Field(ge=1)
    action: Literal["add", "remove", "restore", "pin", "unpin"]
    offer_id: int = Field(gt=0)
    share: int | None = Field(default=None, ge=0, le=100)


class Revision(BaseModel):
    revision: int = Field(ge=1)


def offer_payload(offers):
    return [
        {"offer_id": offer["offer_id"], "share": int(offer["share"]), "state": "active"}
        for offer in offers if not offer.get("removed")
    ]


def remote_offers(stream):
    return sorted([
        {"offer_id": int(offer["offer_id"]), "share": int(offer["share"]), "state": offer.get("state", "active")}
        for offer in stream.get("offers", []) if offer.get("state", "active") == "active"
    ], key=lambda offer: offer["offer_id"])


def fingerprint(stream):
    snapshot = deepcopy(stream)
    snapshot.pop("updated_at", None)
    snapshot.pop("created_at", None)
    for field in ("offers", "landings", "filters"):
        for item in snapshot.get(field, []):
            item.pop("updated_at", None)
            item.pop("created_at", None)
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def new_offers(stream):
    return [
        {
            "offer_id": int(offer["offer_id"]), "share": int(offer["share"]),
            "pinned": False, "removed": offer.get("state", "active") != "active",
        }
        for offer in stream.get("offers", [])
    ]


def draft_view(draft):
    desired = sorted(offer_payload(draft.offers), key=lambda offer: offer["offer_id"])
    return {
        "stream_id": draft.stream_id, "campaign_id": draft.campaign_id,
        "revision": draft.revision, "offers": draft.offers,
        "dirty": desired != remote_offers(draft.baseline),
        "stream": {
            key: draft.baseline.get(key)
            for key in ("name", "type", "position", "schema", "filters", "action_type", "action_payload", "action_options")
        },
    }


def get_draft(session, campaign_id, stream_id, revision):
    draft = session.get(Draft, stream_id)
    if draft is None or draft.campaign_id != campaign_id:
        raise HTTPException(404, "Сначала загрузите потоки кампании")
    if draft.revision != revision:
        raise HTTPException(409, "Черновик изменён в другой вкладке. Обновите данные.")
    return draft


@app.get("/")
def index():
    return FileResponse(static / "index.html")


@app.get("/api/catalog")
def catalog():
    domains = kt.request("GET", "domains")
    groups = kt.request("GET", "groups", params={"type": "campaigns"})
    sources = kt.request("GET", "traffic_sources")
    return {
        "domains": [
            {"id": row["id"], "name": row["name"]}
            for row in domains if row.get("state") == "active"
        ],
        "groups": [{"id": row["id"], "name": row["name"]} for row in groups],
        "sources": [
            {"id": row["id"], "name": row["name"]}
            for row in sources if row.get("state") == "active"
        ],
        "prefix": settings.campaign_prefix,
        "timezone": settings.report_timezone,
    }


@app.get("/api/offers")
def offers(q: str = Query(default="", max_length=200)):
    result = kt.request("GET", "offers")
    query = q.casefold().strip()
    return [
        {"id": row["id"], "name": row["name"]}
        for row in result
        if row.get("state") == "active"
        and (query in row["name"].casefold() or query in str(row["id"]))
    ]


@app.get("/api/campaigns")
def campaigns():
    return [
        {"id": row["id"], "name": row["name"]}
        for row in kt.campaigns() if row.get("state") != "deleted"
    ]


@app.post("/api/campaigns")
def create_campaign(body: CreateCampaign):
    payload = body.model_dump(mode="json", exclude={"request_id"})
    request_id = str(body.request_id)
    with write_session() as session:
        operation = session.get(Creation, request_id)
        if operation and operation.payload != payload:
            raise HTTPException(409, "Этот request_id уже использован с другими параметрами")
        if operation is None:
            operation = Creation(
                request_id=request_id, payload=payload,
                alias="adrobot-" + body.request_id.hex[:24], status="pending",
            )
            session.add(operation)
    error = None
    with write_session() as session:
        operation = session.get(Creation, request_id)
        if operation.status == "complete":
            return {"id": operation.campaign_id}
        try:
            # A deterministic alias allows recovery after a lost create response.
            if operation.campaign_id is None:
                matches = [row for row in kt.campaigns() if row["alias"] == operation.alias]
                if matches:
                    operation.campaign_id = matches[0]["id"]
                else:
                    campaign = kt.request("POST", "campaigns", json={
                        "name": body.name, "alias": operation.alias,
                        "type": "position", "state": "disabled",
                        "domain_id": body.domain_id, "group_id": str(body.group_id),
                        "traffic_source_id": body.traffic_source_id,
                    })
                    operation.campaign_id = campaign["id"]
            campaign_id = operation.campaign_id
            streams = kt.streams(campaign_id)
            names = {stream["name"] for stream in streams if stream.get("state") != "deleted"}
            if "Flow 1 · Google" not in names:
                kt.request("POST", "streams", json={
                    "campaign_id": campaign_id, "name": "Flow 1 · Google",
                    "position": 1, "type": "regular", "schema": "redirect",
                    "action_type": "http", "action_options": {"url": "https://google.com"},
                    "collect_clicks": True,
                    "filters": [{"name": "country", "mode": "accept", "payload": body.geo}],
                })
            if "Flow 2 · Offers" not in names:
                kt.request("POST", "streams", json={
                    "campaign_id": campaign_id, "name": "Flow 2 · Offers",
                    "position": 2, "type": "default", "schema": "landings",
                    "action_type": "http", "collect_clicks": True,
                    "offers": [{"offer_id": body.offer_id, "share": 100, "state": "active"}],
                })
            kt.request("PUT", f"campaigns/{campaign_id}", json={"state": "active"})
            operation.status = "complete"
            operation.error = None
        except HTTPException as exc:
            operation.status = "pending"
            operation.error = str(exc.detail)
            error = exc
    if error:
        raise error
    return {"id": campaign_id}


@app.get("/api/campaigns/{campaign_id}")
def campaign(campaign_id: int):
    row = kt.campaign(campaign_id)
    return {
        "id": row["id"], "name": row["name"],
        "url": row.get("domain", "").rstrip("/") + "/" + row["alias"],
        "admin_url": settings.keitaro_url + f"/admin/#!/campaigns/{campaign_id}",
    }


@app.get("/api/campaigns/{campaign_id}/streams")
def streams(campaign_id: int):
    remote = kt.streams(campaign_id)
    views = []
    with write_session() as session:
        for stream in remote:
            if stream.get("state") == "deleted":
                continue
            draft = session.get(Draft, stream["id"])
            if draft is None:
                draft = Draft(
                    stream_id=stream["id"], campaign_id=campaign_id,
                    baseline=stream, offers=new_offers(stream), revision=1,
                )
                session.add(draft)
            view = draft_view(draft)
            view["conflict"] = fingerprint(stream) != fingerprint(draft.baseline)
            views.append(view)
    return sorted(views, key=lambda row: row["stream"].get("position") or 0)


@app.patch("/api/campaigns/{campaign_id}/streams/{stream_id}/draft")
def edit_draft(campaign_id: int, stream_id: int, body: EditDraft):
    with write_session() as session:
        draft = get_draft(session, campaign_id, stream_id, body.revision)
        if draft.baseline.get("schema") != "landings":
            raise HTTPException(422, "В этом потоке используется прямой редирект")
        rows = deepcopy(draft.offers)
        row = next((offer for offer in rows if offer["offer_id"] == body.offer_id), None)
        if body.action == "add":
            if row:
                raise HTTPException(422, "Оффер уже есть в потоке. Используйте «Вернуть».")
            offer = kt.request("GET", f"offers/{body.offer_id}")
            if offer.get("state") != "active":
                raise HTTPException(422, "Оффер недоступен")
            rows.append({"offer_id": body.offer_id, "share": 0, "removed": False, "pinned": False})
        else:
            if row is None:
                raise HTTPException(404, "Оффер отсутствует в черновике")
            if body.action == "remove":
                row.update(removed=True, pinned=False)
            elif body.action == "restore":
                offer = kt.request("GET", f"offers/{body.offer_id}")
                if offer.get("state") != "active":
                    raise HTTPException(422, "Оффер недоступен")
                row.update(removed=False, pinned=False)
            elif body.action == "pin":
                if row["removed"] or body.share is None:
                    raise HTTPException(422, "Укажите долю активного оффера")
                row.update(pinned=True, share=body.share)
            else:
                row["pinned"] = False
        draft.offers = distribute(rows)
        draft.revision += 1
        return draft_view(draft)


@app.post("/api/campaigns/{campaign_id}/streams/{stream_id}/reset")
def reset_draft(campaign_id: int, stream_id: int, body: Revision):
    with write_session() as session:
        draft = get_draft(session, campaign_id, stream_id, body.revision)
        remote = kt.request("GET", f"streams/{stream_id}")
        if int(remote["campaign_id"]) != campaign_id:
            raise HTTPException(409, "Поток больше не принадлежит этой кампании")
        draft.baseline = remote
        draft.offers = new_offers(remote)
        draft.revision += 1
        return draft_view(draft)


@app.post("/api/campaigns/{campaign_id}/streams/{stream_id}/push")
def push_draft(campaign_id: int, stream_id: int, body: Revision):
    with write_session() as session:
        draft = get_draft(session, campaign_id, stream_id, body.revision)
        if draft.baseline.get("schema") != "landings":
            raise HTTPException(422, "В этом потоке используется прямой редирект")
        rows = distribute(deepcopy(draft.offers))
        desired = offer_payload(rows)
        current = kt.request("GET", f"streams/{stream_id}")
        if remote_offers(current) != sorted(desired, key=lambda row: row["offer_id"]):
            if fingerprint(current) != fingerprint(draft.baseline):
                raise HTTPException(409, "Поток изменён в Keitaro. Загрузите его заново кнопкой «Отменить / обновить».")
            kt.request("PUT", f"streams/{stream_id}", json={"offers": desired})
            current = kt.request("GET", f"streams/{stream_id}")
            if remote_offers(current) != sorted(desired, key=lambda row: row["offer_id"]):
                raise HTTPException(409, "Keitaro вернул другие доли. Обновите поток и проверьте результат.")
        draft.baseline = current
        draft.offers = rows
        draft.revision += 1
        return draft_view(draft)


@app.get("/api/campaigns/{campaign_id}/stats")
def stats(campaign_id: int):
    today = datetime.now(ZoneInfo(settings.report_timezone)).date()
    common = {
        "filters": [{"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}],
        "metrics": ["clicks", "conversions", "revenue", "cost", "profit", "roi"],
        "limit": 10000,
    }
    report = kt.request("POST", "report/build", json={
        **common, "range": {"from": str(today), "to": str(today), "timezone": settings.report_timezone},
        "dimensions": ["stream_id", "offer_id"],
    })
    trends = kt.request("POST", "report/build", json={
        **common,
        "range": {"from": str(today - timedelta(days=6)), "to": str(today), "timezone": settings.report_timezone},
        "dimensions": ["stream_id", "offer_id", "day"],
    })
    return {
        "rows": report["rows"], "trends": trends["rows"],
        "date": str(today), "timezone": settings.report_timezone,
    }
