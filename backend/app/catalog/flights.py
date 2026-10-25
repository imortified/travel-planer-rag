"""Bind informational search results to a group and freeze the user's selection."""

from datetime import datetime, timezone
from uuid import uuid4

from app.db.models import FlightSearchObservation
from app.integrations.aviasales import Aviasales, digest
from app.planning.costs import age_at
from app.planning.models import Participant


def passengers_for(members, search):
    return sorted(
        [
            {
                "participant_id": str(m.user_id),
                "age_at_departure": age_at(
                    Participant(
                        str(m.user_id),
                        m.interests,
                        m.age_profile.age_on_start,
                        m.age_profile.birth_date,
                    ),
                    search.departure,
                ),
            }
            for m in members
        ],
        key=lambda row: row["participant_id"],
    )


def fetch_observation(search, members, provider=None):
    if search.departure < datetime.now(timezone.utc).date():
        raise ValueError("Для поиска перелётов выберите текущую или будущую дату")
    passengers = passengers_for(members, search)
    if not passengers:
        raise ValueError("Добавьте участников группы")
    data = (provider or Aviasales()).search(search)
    return {
        "version": "aviasales-observation-v1",
        "search": search.model_dump(mode="json"),
        "passengers": passengers,
        **data,
    }


def save_observation(session, group_id, data):
    row = FlightSearchObservation(
        id=str(uuid4()),
        group_id=group_id,
        data=data,
        content_sha256=digest(data),
        expires_at=datetime.fromisoformat(data["expires_at"]),
    )
    session.add(row)
    session.flush()
    return {
        "observation_id": row.id,
        **data,
        "status": "observations" if data["options"] else "empty",
    }


def freeze_flight(session, payload, members, group_id):
    choice = payload.flight_plan
    if choice is None:
        return None
    row = session.get(FlightSearchObservation, str(choice.observation_id))
    if row is None or row.group_id != group_id:
        raise ValueError("Результат поиска перелётов отсутствует или принадлежит другой группе")
    data = row.data
    if digest(data) != row.content_sha256:
        raise ValueError("Снимок поиска перелётов повреждён")
    if row.expires_at <= datetime.now(timezone.utc) or row.expires_at != datetime.fromisoformat(
        data["expires_at"]
    ):
        raise ValueError("Срок выбора перелёта истёк. Повторите поиск")
    from app.schemas.flights import FlightSearch

    search = FlightSearch.model_validate(data["search"])
    if search.destination != payload.destination:
        raise ValueError("Перелёт относится к другому городу поездки")
    if passengers_for(members, search) != data["passengers"]:
        raise ValueError("Состав или возраст группы изменился. Повторите поиск перелётов")
    if search.departure > payload.travel_date or (
        search.return_date and search.return_date < (payload.end_date or payload.travel_date)
    ):
        raise ValueError("Даты перелёта должны покрывать календарные даты поездки")
    option = next((o for o in data["options"] if o["option_id"] == choice.option_id), None)
    if option is None:
        raise ValueError("Выбранного перелёта нет в сохранённом поиске")
    return {
        "version": "flight-selection-v1",
        "choice": choice.model_dump(mode="json"),
        "search": data["search"],
        "option": option,
        "passengers": data["passengers"],
        "fetched_at": data["fetched_at"],
        "expires_at": data["expires_at"],
        "source_found_at": None,
        "cache": data["cache"],
        "group_total_estimate": payload.budget_settings.flights,
        "price_status": "user_estimate"
        if payload.budget_settings.flights is not None
        else "unknown",
        "group_total_confirmed": False,
        "booking_status": "not_booked",
        "schedule_adjusted": False,
        "segments_complete": False,
    }
