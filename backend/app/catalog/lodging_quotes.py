"""Bind observed prices to a group; freeze an explicit base estimate without claiming booking."""

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.db.models import LodgingProviderQuote
from app.integrations.http_cache import SourceUnavailable
from app.integrations.xotelo import Xotelo
from app.planning.costs import age_at
from app.planning.models import Participant


def digest(data):
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def guests_for(members, checkin, checkout):
    guests = []
    for member in members:
        person = Participant(
            str(member.user_id),
            member.interests,
            member.age_profile.age_on_start,
            member.age_profile.birth_date,
        )
        guests.append({"participant_id": person.id, "age_at_checkin": age_at(person, checkin)})
        age_at(person, checkout)
    return sorted(guests, key=lambda row: row["participant_id"])


def fetch_observation(stay, members, provider=None):
    if stay.checkin < datetime.now(ZoneInfo("Europe/Lisbon")).date():
        raise ValueError("Для загрузки цен выберите текущую или будущую дату заезда")
    guests = guests_for(members, stay.checkin, stay.checkout)
    if not guests or any(row["age_at_checkin"] is None for row in guests):
        raise ValueError(
            "Для цен Xotelo укажите возраст каждого участника; ручная оценка доступна без возраста"
        )
    adults = sum(row["age_at_checkin"] >= 18 for row in guests)
    children = [row["age_at_checkin"] for row in guests if row["age_at_checkin"] < 18]
    if adults < 1:
        raise ValueError(
            "Для запроса Xotelo нужен хотя бы один взрослый; условия размещения уточните вручную"
        )
    provider = provider or Xotelo()
    listing = provider.hotels(stay.destination, stay.list_offset)
    hotel = next((row for row in listing["hotels"] if row["hotel_key"] == stay.hotel_key), None)
    if hotel is None:
        raise ValueError("Повторно выберите отель на указанной странице Xotelo")
    rates = provider.rates(stay, adults, children)
    if datetime.fromisoformat(rates["expires_at"]) <= datetime.now(timezone.utc):
        raise SourceUnavailable("Observation expired")
    return {
        "version": "xotelo-observation-v1",
        "destination": stay.destination,
        "stay": stay.model_dump(mode="json"),
        "hotel": hotel,
        "guests": guests,
        "adults": adults,
        "children_ages": children,
        **rates,
    }


def save_observation(session, group_id, data):
    identifier = str(uuid4())
    row = LodgingProviderQuote(
        id=identifier,
        group_id=group_id,
        data=data,
        content_sha256=digest(data),
        expires_at=datetime.fromisoformat(data["expires_at"]),
    )
    session.add(row)
    session.flush()
    return {
        "quote_id": identifier,
        **data,
        "status": "quotes" if data["rates"] else "empty",
        "booking_status": "not_booked",
        "occupancy_status": "unverified",
        "taxes_and_fees_status": "unknown",
    }


def freeze_quote(session, payload, members, group_id):
    choice = payload.lodging_quote
    row = session.get(LodgingProviderQuote, str(choice.quote_id))
    if row is None or row.group_id != group_id:
        raise ValueError("Предложение Xotelo отсутствует или принадлежит другой группе")
    data = row.data
    if digest(data) != row.content_sha256:
        raise ValueError("Снимок предложения повреждён")
    if row.expires_at <= datetime.now(timezone.utc) or row.expires_at != datetime.fromisoformat(
        data["expires_at"]
    ):
        raise ValueError(
            "Срок предложения Xotelo истёк. Загрузите цены снова или выберите ручную оценку"
        )
    stay = data["stay"]
    if data["destination"] != payload.destination:
        raise ValueError("Предложение относится к другому городу")
    from datetime import date

    guests = guests_for(
        members, date.fromisoformat(stay["checkin"]), date.fromisoformat(stay["checkout"])
    )
    if guests != data["guests"]:
        raise ValueError("Состав или возраст группы изменились. Загрузите цены Xotelo снова")
    if stay["checkin"] > str(payload.travel_date) or stay["checkout"] < str(
        payload.end_date or payload.travel_date
    ):
        raise ValueError("Проживание Xotelo должно покрывать даты поездки")
    rate = next((rate for rate in data["rates"] if rate["code"] == choice.rate_code), None)
    if rate is None:
        raise ValueError("Выбранного тарифа нет в предложении")
    estimate = payload.budget_settings.lodging
    if choice.apply_base_estimate:
        if stay["rooms"] != 1 or rate["estimated_base_total"] is None:
            raise ValueError("Для нескольких номеров нужна ручная общая оценка жилья")
        if estimate != rate["estimated_base_total"] or estimate > 1_000_000_000:
            raise ValueError("Оценка жилья не совпадает с базовым расчётом Xotelo")
    hotel = data["hotel"]
    return {
        "version": "xotelo-lodging-selection-v1",
        "mode": "xotelo",
        "quote_choice": choice.model_dump(mode="json"),
        "selection": {
            "property_id": "xotelo:" + stay["hotel_key"],
            "catalog_version_id": None,
            "checkin": stay["checkin"],
            "checkout": stay["checkout"],
            "rooms": stay["rooms"],
        },
        "property": {
            "id": "xotelo:" + stay["hotel_key"],
            "name": hotel["name"],
            "destination": data["destination"],
            "kind": "lodging",
            "latitude": hotel["latitude"],
            "longitude": hotel["longitude"],
            "location_kind": "xotelo_geo",
            "address": None,
            "website_url": None,
            "source_url": hotel["source_url"],
        },
        "catalog_sha256": None,
        "source_upstream_asof": data["source_timestamp"],
        "attribution": "Xotelo; property identity and OTA comparison based on Tripadvisor",
        "nights": (date.fromisoformat(stay["checkout"]) - date.fromisoformat(stay["checkin"])).days,
        "guests": guests,
        "adults": data["adults"],
        "children_ages": data["children_ages"],
        "unknown_ages": 0,
        "group_total_estimate": estimate,
        "price_status": "provider_base_estimate"
        if choice.apply_base_estimate
        else "user_estimate"
        if estimate is not None
        else "unknown",
        "availability_status": "observed_quote_not_guaranteed",
        "occupancy_status": "unverified",
        "booking_status": "not_booked",
        "taxes_and_fees_status": "unknown",
        "provider_quote": {
            "provider": "xotelo",
            "rate": rate,
            "query": data["query"],
            "fetched_at": data["fetched_at"],
            "source_timestamp": data["source_timestamp"],
            "expires_at": data["expires_at"],
            "cache": data["cache"],
            "unit": "documented_price_per_room_per_night",
            "complete_total_confirmed": False,
        },
    }
