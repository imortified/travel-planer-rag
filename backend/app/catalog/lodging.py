"""Atomic, immutable lodging directory publication. Never fetches or invents offers."""

import unicodedata
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.models import LodgingCatalogHead, LodgingCatalogVersion
from app.planning.costs import age_at
from app.planning.models import Participant
from app.schemas.lodging import LodgingCatalog


def publish_lodgings(session, data, now=None):
    data = LodgingCatalog.model_validate(data.model_dump(mode="json"))
    now = now or datetime.now(timezone.utc)
    if data.downloaded_at > now or data.source_upstream_asof > now:
        raise ValueError("Cannot publish future lodging data")
    session.execute(
        insert(LodgingCatalogHead).values(destination=data.destination).on_conflict_do_nothing()
    )
    head = session.scalar(
        select(LodgingCatalogHead)
        .where(LodgingCatalogHead.destination == data.destination)
        .with_for_update()
    )
    if head.version_id is not None:
        _, current = load_lodgings(session, data.destination, head.version_id)
        if data.source_upstream_asof < current.source_upstream_asof:
            raise ValueError("Cannot replace lodging directory with an older upstream source")
    digest = data.digest()
    version = session.scalar(
        select(LodgingCatalogVersion).where(
            LodgingCatalogVersion.destination == data.destination,
            LodgingCatalogVersion.content_sha256 == digest,
        )
    )
    reused = version is not None
    if version is None:
        version = LodgingCatalogVersion(
            destination=data.destination, content_sha256=digest, data=data.model_dump(mode="json")
        )
        session.add(version)
        session.flush()
    head.version_id = version.id
    session.flush()
    return version, reused


def load_lodgings(session, destination, version_id=None):
    if version_id is None:
        version_id = session.scalar(
            select(LodgingCatalogHead.version_id).where(
                LodgingCatalogHead.destination == destination
            )
        )
    version = session.get(LodgingCatalogVersion, version_id) if version_id is not None else None
    if version is None:
        raise LookupError("Каталог проживания ещё не опубликован")
    if version.destination != destination:
        raise ValueError("Каталог проживания относится к другому городу")
    data = LodgingCatalog.model_validate(version.data)
    if data.destination != destination or data.digest() != version.content_sha256:
        raise ValueError("Контрольная сумма каталога проживания не совпадает")
    return version, data


def normalized(value):
    return (
        "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))
        .casefold()
        .strip()
    )


def search_lodgings(session, destination, query="", kind=None, page=1, page_size=20):
    version, data = load_lodgings(session, destination)
    needle = normalized(query)
    matches = sorted(
        (
            p
            for p in data.properties
            if (not kind or p.kind == kind)
            and (not needle or needle in normalized(p.name + " " + (p.address or "")))
        ),
        key=lambda p: (normalized(p.name), p.id),
    )
    start = (page - 1) * page_size
    return {
        "mode": "directory",
        "live_offers": False,
        "destination": destination,
        "catalog_version_id": version.id,
        "content_sha256": version.content_sha256,
        "source_upstream_asof": data.source_upstream_asof,
        "downloaded_at": data.downloaded_at,
        "published_at": version.published_at,
        "attribution": data.attribution,
        "total": len(matches),
        "page": page,
        "page_size": page_size,
        "properties": [p.model_dump(mode="json") for p in matches[start : start + page_size]],
        "availability_status": "not_checked",
        "price_status": "unknown",
    }


def freeze_lodging(session, payload, members, group_id=None):
    if payload.lodging_quote:
        from app.catalog.lodging_quotes import freeze_quote

        return freeze_quote(session, payload, members, group_id)
    selection = payload.lodging_plan
    if selection is None:
        return None
    version, data = load_lodgings(session, payload.destination, selection.catalog_version_id)
    property_ = next((p for p in data.properties if p.id == selection.property_id), None)
    if property_ is None:
        raise LookupError("Объект отсутствует в выбранной версии каталога проживания")
    guests = []
    for member in members:
        person = Participant(
            str(member.user_id),
            member.interests,
            member.age_profile.age_on_start,
            member.age_profile.birth_date,
        )
        age = age_at(person, selection.checkin)
        age_at(person, selection.checkout)
        guests.append({"participant_id": person.id, "age_at_checkin": age})
    unknown = sum(g["age_at_checkin"] is None for g in guests)
    adults = sum(g["age_at_checkin"] is not None and g["age_at_checkin"] >= 18 for g in guests)
    children = [
        g["age_at_checkin"]
        for g in guests
        if g["age_at_checkin"] is not None and g["age_at_checkin"] < 18
    ]
    return {
        "version": "directory-lodging-selection-v1",
        "mode": "directory",
        "selection": selection.model_dump(mode="json"),
        "property": property_.model_dump(mode="json"),
        "catalog_sha256": version.content_sha256,
        "source_upstream_asof": data.source_upstream_asof.isoformat(),
        "attribution": data.attribution,
        "nights": (selection.checkout - selection.checkin).days,
        "guests": guests,
        "adults": adults,
        "children_ages": children,
        "unknown_ages": unknown,
        "group_total_estimate": payload.budget_settings.lodging,
        "price_status": "user_estimate"
        if payload.budget_settings.lodging is not None
        else "unknown",
        "availability_status": "not_checked",
        "occupancy_status": "not_checked",
        "booking_status": "not_booked",
    }
