from dataclasses import asdict, replace
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Group, Membership, RouteOption, TripRequest, User
from app.db.session import get_session
from app.integrations.city import CityProvider, IncompleteData, SourceUnavailable
from app.integrations.demo import load_demo
from app.planning.models import DayInput, Participant, Request
from app.planning.period import plan_period
from app.planning.service import checked_plan
from app.schemas.groups import (
    GroupOutput,
    MemberInput,
    MemberOutput,
    NamedInput,
    NamedOutput,
    PreferencesInput,
    RouteOutput,
    TripInput,
    TripOutput,
)

router = APIRouter(prefix="/api/v1", tags=["Groups and trips"])
DB = Annotated[Session, Depends(get_session)]


def require(session, model, identifier):
    obj = session.get(model, identifier)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


def members(session, group_id):
    rows = session.execute(
        select(Membership, User)
        .join(User, Membership.user_id == User.id)
        .where(Membership.group_id == group_id)
        .order_by(User.id)
    )
    return [MemberOutput(user_id=u.id, name=u.name, interests=m.interests) for m, u in rows]


@router.post("/users", response_model=NamedOutput, status_code=201)
def create_user(payload: NamedInput, session: DB):
    user = User(name=payload.name.strip())
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.get("/users/{user_id}", response_model=NamedOutput)
def get_user(user_id: int, session: DB):
    return require(session, User, user_id)


@router.post("/groups", response_model=GroupOutput, status_code=201)
def create_group(payload: NamedInput, session: DB):
    group = Group(name=payload.name.strip())
    session.add(group)
    session.commit()
    session.refresh(group)
    return GroupOutput(id=group.id, name=group.name, members=[])


@router.get("/groups/{group_id}", response_model=GroupOutput)
def get_group(group_id: int, session: DB):
    group = require(session, Group, group_id)
    return GroupOutput(id=group.id, name=group.name, members=members(session, group_id))


def lock_group(session, group_id):
    group = session.scalar(select(Group).where(Group.id == group_id).with_for_update())
    if group is None:
        raise HTTPException(404, "Group not found")
    return group


@router.post("/groups/{group_id}/members", response_model=MemberOutput, status_code=201)
def add_member(group_id: int, payload: MemberInput, session: DB):
    lock_group(session, group_id)  # Serializes membership and preference changes.
    user = require(session, User, payload.user_id)
    current = members(session, group_id)
    if any(m.user_id == user.id for m in current):
        raise HTTPException(409, "User is already a member")
    if len(current) >= 6:
        raise HTTPException(409, "Group is limited to six participants")
    session.add(Membership(group_id=group_id, user_id=user.id, interests={}))
    session.commit()
    return MemberOutput(user_id=user.id, name=user.name, interests={})


@router.put("/groups/{group_id}/members/{user_id}/preferences", response_model=MemberOutput)
def update_preferences(group_id: int, user_id: int, payload: PreferencesInput, session: DB):
    lock_group(session, group_id)
    member = session.scalar(
        select(Membership).where(
            Membership.group_id == group_id,
            Membership.user_id == user_id,
        )
    )
    if member is None:
        raise HTTPException(404, "Membership not found")
    member.interests = payload.interests
    user = require(session, User, user_id)
    session.commit()
    return MemberOutput(user_id=user.id, name=user.name, interests=member.interests)


def trip_output(trip):
    return TripOutput(
        id=trip.id,
        group_id=trip.group_id,
        destination=trip.destination,
        currency=trip.currency,
        travel_date=trip.travel_date,
        end_date=trip.end_date,
        budget=trip.budget,
        start=trip.start,
        end=trip.end_time,
        mandatory=set(trip.mandatory),
        excluded=set(trip.excluded),
        participants_snapshot=trip.participants_snapshot,
        created_at=trip.created_at,
    )


@router.post("/groups/{group_id}/trips", response_model=TripOutput, status_code=201)
def create_trip(group_id: int, payload: TripInput, session: DB):
    lock_group(session, group_id)
    participants = members(session, group_id)
    if not participants:
        raise HTTPException(409, "Add participants before creating a trip")
    trip = TripRequest(
        group_id=group_id,
        destination=payload.destination,
        currency=payload.currency,
        travel_date=payload.travel_date,
        end_date=payload.end_date,
        budget=payload.budget,
        start=payload.start,
        end_time=payload.end,
        mandatory=sorted(payload.mandatory),
        excluded=sorted(payload.excluded),
        participants_snapshot=[p.model_dump() for p in participants],
    )
    session.add(trip)
    session.commit()
    session.refresh(trip)
    return trip_output(trip)


@router.get("/groups/{group_id}/trips", response_model=list[TripOutput])
def list_trips(group_id: int, session: DB):
    require(session, Group, group_id)
    return [
        trip_output(t)
        for t in session.scalars(
            select(TripRequest).where(TripRequest.group_id == group_id).order_by(TripRequest.id)
        )
    ]


@router.get("/trips/{trip_id}", response_model=TripOutput)
def get_trip(trip_id: int, session: DB):
    return trip_output(require(session, TripRequest, trip_id))


@router.post("/trips/{trip_id}/routes", response_model=RouteOutput, status_code=201)
def calculate_route(
    trip_id: int,
    session: DB,
    algorithm: Literal["greedy", "beam"] = "greedy",
    beam_width: Annotated[int, Query(ge=1, le=128)] = 32,
):
    trip = require(session, TripRequest, trip_id)
    request = Request(
        participants=[
            Participant(str(p["user_id"]), p["interests"]) for p in trip.participants_snapshot
        ],
        budget=trip.budget,
        start=trip.start,
        end=trip.end_time,
        mandatory=frozenset(trip.mandatory),
        excluded=frozenset(trip.excluded),
    )
    context = None
    source = "synthetic-demo-v1"
    if trip.end_date and trip.travel_date and trip.end_date > trip.travel_date:
        inputs = []
        for offset in range((trip.end_date - trip.travel_date).days + 1):
            day = trip.travel_date + timedelta(days=offset)
            if trip.destination == "Lisbon":
                try:
                    places, travel, context = CityProvider().dataset(
                        day, request.mandatory, allow_empty=True
                    )
                except IncompleteData as exc:
                    raise HTTPException(422, str(exc)) from exc
                except SourceUnavailable as exc:
                    raise HTTPException(503, str(exc)) from exc
                source = "osm-osrm-lisbon-v2"
            else:
                places, travel = load_demo()
            inputs.append(DayInput(day, places, travel, context))
        if trip.destination == "Lisbon" and request.mandatory - {
            p.id for data in inputs for p in data.places
        }:
            raise HTTPException(422, "Mandatory POI lacks usable data throughout the period")
        result = plan_period(request, inputs, algorithm, beam_width)
        if trip.destination == "Lisbon":
            pois = {p["id"]: p for data in inputs for p in data.data_context["pois"]}
            result = replace(
                result, data_context={**inputs[0].data_context, "pois": list(pois.values())}
            )
        route = RouteOption(
            trip_request_id=trip.id,
            algorithm=f"{algorithm}-period-v1:w{beam_width}",
            data_source=source,
            result=asdict(result),
        )
        session.add(route)
        session.commit()
        session.refresh(route)
        return route
    if trip.destination == "Lisbon":
        try:
            places, travel, context = CityProvider().dataset(trip.travel_date, request.mandatory)
        except IncompleteData as exc:
            raise HTTPException(422, str(exc)) from exc
        except SourceUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        source = "osm-osrm-lisbon-v2"
    else:
        places, travel = load_demo()
    result = replace(
        checked_plan(request, places, travel, algorithm, beam_width), data_context=context
    )
    route = RouteOption(
        trip_request_id=trip.id,
        algorithm="greedy-v1" if algorithm == "greedy" else f"beam-v1:w{beam_width}",
        data_source=source,
        result=asdict(result),
    )
    session.add(route)
    session.commit()
    session.refresh(route)
    return route


@router.get("/trips/{trip_id}/routes", response_model=list[RouteOutput])
def list_routes(trip_id: int, session: DB):
    require(session, TripRequest, trip_id)
    return list(
        session.scalars(
            select(RouteOption)
            .where(RouteOption.trip_request_id == trip_id)
            .order_by(RouteOption.id)
        )
    )


@router.get("/routes/{route_id}", response_model=RouteOutput)
def get_route(route_id: int, session: DB):
    return require(session, RouteOption, route_id)
