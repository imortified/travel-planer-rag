from dataclasses import asdict, replace
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Group, Membership, RouteOption, RouteSet, TripRequest, User
from app.db.session import get_session
from app.integrations.city import CityProvider, IncompleteData, SourceUnavailable
from app.integrations.demo import load_demo
from app.planning.alternatives import alternatives
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
    RouteSetOutput,
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


def planning_request(trip):
    return Request(
        participants=[
            Participant(str(p["user_id"]), p["interests"]) for p in trip.participants_snapshot
        ],
        budget=trip.budget,
        start=trip.start,
        end=trip.end_time,
        mandatory=frozenset(trip.mandatory),
        excluded=frozenset(trip.excluded),
    )


def planning_inputs(trip, request):
    """Load one shared reference snapshot for every option in this calculation."""
    source = "synthetic-demo-v1"
    multi_day = bool(trip.end_date and trip.travel_date and trip.end_date > trip.travel_date)
    # Undated demo trips use the creation date internally; it is not displayed as a travel date.
    first = trip.travel_date or trip.created_at.date()
    count = (trip.end_date - first).days + 1 if multi_day else 1
    inputs = []
    for offset in range(count):
        day, context = first + timedelta(days=offset), None
        if trip.destination == "Lisbon":
            try:
                if multi_day:
                    places, travel, context = CityProvider().dataset(
                        day, request.mandatory, allow_empty=True
                    )
                else:
                    places, travel, context = CityProvider().dataset(day, request.mandatory)
            except IncompleteData as exc:
                raise HTTPException(422, str(exc)) from exc
            except SourceUnavailable as exc:
                raise HTTPException(503, str(exc)) from exc
            source = "osm-osrm-lisbon-v2"
        else:
            places, travel = load_demo()
        inputs.append(DayInput(day, places, travel, context))
    if (
        multi_day
        and trip.destination == "Lisbon"
        and request.mandatory - {p.id for data in inputs for p in data.places}
    ):
        raise HTTPException(422, "Mandatory POI lacks usable data throughout the period")
    return inputs, multi_day, source


def with_overview(result, inputs):
    if inputs[0].data_context is None:
        return result
    if len(inputs) == 1:
        return replace(result, data_context=inputs[0].data_context)
    pois = {p["id"]: p for data in inputs for p in data.data_context["pois"]}
    return replace(result, data_context={**inputs[0].data_context, "pois": list(pois.values())})


def algorithm_name(algorithm, beam_width, multi_day):
    if multi_day:
        return f"{algorithm}-period-v1:w{beam_width}"
    return "greedy-v1" if algorithm == "greedy" else f"beam-v1:w{beam_width}"


@router.post("/trips/{trip_id}/routes", response_model=RouteOutput, status_code=201)
def calculate_route(
    trip_id: int,
    session: DB,
    algorithm: Literal["greedy", "beam"] = "greedy",
    beam_width: Annotated[int, Query(ge=1, le=128)] = 32,
):
    trip = require(session, TripRequest, trip_id)
    request = planning_request(trip)
    inputs, multi_day, source = planning_inputs(trip, request)
    data = inputs[0]
    result = (
        plan_period(request, inputs, algorithm, beam_width)
        if multi_day
        else checked_plan(request, data.places, data.travel, algorithm, beam_width)
    )
    route = RouteOption(
        trip_request_id=trip.id,
        algorithm=algorithm_name(algorithm, beam_width, multi_day),
        data_source=source,
        result=asdict(with_overview(result, inputs)),
    )
    session.add(route)
    session.commit()
    session.refresh(route)
    return route


def route_set_output(session, route_set):
    options = session.scalars(
        select(RouteOption).where(RouteOption.route_set_id == route_set.id).order_by(RouteOption.id)
    )
    return RouteSetOutput(
        id=route_set.id,
        trip_request_id=route_set.trip_request_id,
        algorithm=route_set.algorithm,
        search_info=route_set.search_info,
        diagnostic=route_set.diagnostic,
        created_at=route_set.created_at,
        options=list(options),
    )


@router.post("/trips/{trip_id}/route-sets", response_model=RouteSetOutput, status_code=201)
def calculate_route_set(
    trip_id: int,
    session: DB,
    algorithm: Literal["greedy", "beam"] = "beam",
    beam_width: Annotated[int, Query(ge=1, le=128)] = 32,
):
    trip = require(session, TripRequest, trip_id)
    request = planning_request(trip)
    inputs, multi_day, source = planning_inputs(trip, request)
    outcome = alternatives(request, inputs, algorithm, beam_width, multi_day)
    route_set = RouteSet(
        trip_request_id=trip.id,
        algorithm=f"diverse-v1:{algorithm}:w{beam_width}",
        search_info=outcome.search_info,
        diagnostic=asdict(with_overview(outcome.diagnostic, inputs))
        if outcome.diagnostic
        else None,
    )
    session.add(route_set)
    session.flush()
    for candidate in outcome.options:
        session.add(
            RouteOption(
                trip_request_id=trip.id,
                route_set_id=route_set.id,
                comparison=asdict(candidate.comparison),
                algorithm=algorithm_name(algorithm, beam_width, multi_day),
                data_source=source,
                result=asdict(with_overview(candidate.result, inputs)),
            )
        )
    # One transaction: a comparison cannot be persisted only partially.
    session.commit()
    session.refresh(route_set)
    return route_set_output(session, route_set)


@router.get("/route-sets/{route_set_id}", response_model=RouteSetOutput)
def get_route_set(route_set_id: int, session: DB):
    return route_set_output(session, require(session, RouteSet, route_set_id))


@router.get("/trips/{trip_id}/route-sets", response_model=list[RouteSetOutput])
def list_route_sets(trip_id: int, session: DB):
    require(session, TripRequest, trip_id)
    return [
        route_set_output(session, route_set)
        for route_set in session.scalars(
            select(RouteSet).where(RouteSet.trip_request_id == trip_id).order_by(RouteSet.id)
        )
    ]


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
