"""Pure Python greedy baseline; times are local minutes, costs are integer cents."""

from app.planning.costs import group_cost
from app.planning.models import Place, Request, Result, Visit
from app.planning.objective import Objective
from app.planning.pace import fits_pace


def plan(
    request: Request,
    places: list[Place],
    travel: dict[tuple[str, str], int],
    objective=None,
    day_index=0,
    prefix=None,
) -> Result:
    if not request.participants or len({p.id for p in request.participants}) != len(
        request.participants
    ):
        raise ValueError("Participants must be nonempty and have unique IDs")
    if request.budget < 0 or not 0 <= request.start < request.end <= 1440:
        raise ValueError("Invalid budget or day interval")
    ids = {p.id for p in places}
    if len(ids) != len(places):
        raise ValueError("Duplicate place IDs")
    if any(
        p.cost_per_person < 0 or p.duration <= 0 or not 0 <= p.opens < p.closes <= 1440
        for p in places
    ) or any(t < 0 for t in travel.values()):
        raise ValueError("Invalid place or travel duration")
    if any(not 0 <= w <= 1 for p in request.participants for w in p.interests.values()):
        raise ValueError("Interest weights must be between 0 and 1")
    if request.mandatory & request.excluded:
        return Result(False, [], 0, {}, ["mandatory_excluded_conflict"])
    if request.mandatory - ids:
        return Result(False, [], 0, {}, ["unknown_mandatory_place"])

    objective = objective or Objective.single(request, places)
    for place in places:
        group_cost(place, request.participants)
    base = [list(day) for day in prefix] if prefix is not None else [[] for _ in objective.places]

    def schedule(visits):
        days = list(base)
        days[day_index] = visits
        return days

    remaining = [p for p in places if p.id not in request.excluded]
    visits: list[Visit] = []
    cost, now, previous = 0, request.start, "start"
    totals = {p.id: 0.0 for p in request.participants}
    while remaining:
        candidates = []
        for place in remaining:
            minutes = travel.get((previous, place.id))
            if minutes is None:
                continue  # Missing routing data must never imply zero travel time.
            arrival = now + minutes
            rest = request.pace.break_minutes if request.pace and visits else 0
            begins = max(arrival + rest, place.opens)
            ends = begins + place.duration
            price = group_cost(place, request.participants)
            extension = visits + [Visit(place.id, arrival, begins, ends)]
            if (
                ends <= min(place.closes, request.end)
                and cost + price <= request.budget
                and fits_pace(request, extension, travel)
            ):
                candidates.append((place, arrival, begins, ends, price))
        if not candidates:
            break
        # A required lunch must not force waiting all morning if an earlier stop
        # fits and its onward road leg still leaves room for the meal service.
        waiting = [
            c
            for c in candidates
            if c[0].id in request.meal_ids
            and c[2] > c[1] + (request.pace.break_minutes if request.pace and visits else 0)
        ]
        fill = set()
        if waiting:
            meal = min(waiting, key=lambda c: (c[0].closes, c[0].id))[0]
            rest = request.pace.break_minutes if request.pace else 0
            fill = {
                c[0].id
                for c in candidates
                if c[0].id not in request.mandatory
                and (c[0].id, meal.id) in travel
                and max(c[3] + travel[c[0].id, meal.id] + rest, meal.opens) + meal.duration
                <= min(meal.closes, request.end)
                and cost + c[4] + group_cost(meal, request.participants) <= request.budget
                and fits_pace(
                    request,
                    visits
                    + [
                        Visit(c[0].id, c[1], c[2], c[3]),
                        Visit(
                            meal.id,
                            c[3] + travel[c[0].id, meal.id],
                            max(c[3] + travel[c[0].id, meal.id] + rest, meal.opens),
                            max(c[3] + travel[c[0].id, meal.id] + rest, meal.opens) + meal.duration,
                        ),
                    ],
                    travel,
                )
            }
        deferred = {c[0].id for c in waiting} if fill else set()
        place, arrival, begins, ends, price = min(
            candidates,
            key=lambda c: (
                0
                if c[0].id in request.mandatory - deferred
                else 1
                if c[0].id in fill
                else 2
                if c[0].id in deferred
                else 3,
                objective.key(schedule(visits + [Visit(c[0].id, c[1], c[2], c[3])])),
            ),
        )
        extension = visits + [Visit(place.id, arrival, begins, ends)]
        if (
            any(schedule(visits))
            and request.mandatory <= {v.place_id for v in visits}
            and objective.key(schedule(extension)) >= objective.key(schedule(visits))
        ):
            break
        visits.append(Visit(place.id, arrival, begins, ends))
        cost += price
        now, previous = ends, place.id
        for participant in request.participants:
            totals[participant.id] += participant.interests.get(place.category, 0)
        remaining.remove(place)
    missing = request.mandatory - {v.place_id for v in visits}
    reasons = [f"mandatory_not_scheduled:{pid}" for pid in sorted(missing)]
    if not visits:
        reasons.append("no_feasible_visits")
    satisfaction = {pid: value / len(visits) if visits else 0 for pid, value in totals.items()}
    feasible = bool(visits) and not missing
    return Result(
        feasible,
        visits,
        cost,
        satisfaction,
        reasons,
        objective=objective.evaluate([visits]) if feasible and len(objective.places) == 1 else None,
    )
