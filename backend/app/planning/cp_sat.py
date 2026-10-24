"""Optional POI circuits per day, exact hard constraints and a bounded scaled objective."""

from math import isfinite
from time import perf_counter

import ortools
from ortools.sat.python import cp_model

from app.planning.models import Result, SolverReport, Visit
from app.planning.objective import Objective
from app.planning.period import plan_period, validate_period

SCALE = 1_000_000


def cp_sat_plan(request, inputs, objective=None, time_limit=5.0, seed=42, scale=SCALE):
    if not isfinite(time_limit) or not 0 <= time_limit <= 60:
        raise ValueError("CP-SAT time limit must be between 0 and 60 seconds")
    if not isinstance(scale, int) or not 10 <= scale <= 1_000_000_000:
        raise ValueError("Invalid objective integer scale")
    started = perf_counter()
    objective = objective or Objective(request, inputs)
    # Also performs the same reference-input checks as the heuristic implementations.
    baseline = plan_period(request, inputs, "greedy", objective=objective)
    model = cp_model.CpModel()
    selections, all_cost, reward_terms, drive_terms = {}, [], [], []
    coverage_terms = {pid: [] for pid, bound in objective.bounds.items() if bound > 0}
    day_models = []

    def integer(value):
        scaled = round(scale * value)
        if abs(scaled) > 10**12:
            raise ValueError("Objective coefficient exceeds the supported integer range")
        return scaled

    for d, data in enumerate(inputs):
        places = [
            p
            for p in sorted(data.places, key=lambda p: p.id)
            if p.id not in request.excluded
            and p.cost_per_person * len(request.participants) <= request.budget
            and max(request.start, p.opens) + p.duration <= min(request.end, p.closes)
        ]
        chosen, starts = {}, {}
        used = model.new_bool_var(f"day_{d}")
        if request.pace:
            reward_terms.append(integer(objective.weights.spread / len(inputs)) * used)
        day_drive = []
        arcs, edge_vars = [(0, 0, used.Not())], {}
        for i, place in enumerate(places, 1):
            x = model.new_bool_var(f"visit_{d}_{i}")
            chosen[i] = x
            selections.setdefault(place.id, []).append(x)
            model.add(x <= used)
            arcs.append((i, i, x.Not()))
            starts[i] = model.new_int_var(request.start, request.end, f"start_{d}_{i}")
            model.add(starts[i] >= place.opens).only_enforce_if(x)
            model.add(starts[i] + place.duration <= min(request.end, place.closes)).only_enforce_if(
                x
            )
            model.add(starts[i] == request.start).only_enforce_if(x.Not())
            cost = place.cost_per_person * len(request.participants)
            all_cost.append(cost * x)
            reward = sum(p.interests.get(place.category, 0) for p in request.participants)
            reward = (
                reward / len(request.participants) / objective.reward_bound
                if objective.reward_bound
                else 0
            )
            reward_terms.append(integer(objective.weights.reward * reward) * x)
            if objective.request.budget:
                reward_terms.append(
                    -integer(objective.weights.cost * cost / objective.request.budget) * x
                )
            for person in request.participants:
                if person.id in coverage_terms:
                    coefficient = integer(
                        objective.weights.fairness
                        * person.interests.get(place.category, 0)
                        / objective.bounds[person.id]
                    )
                    coverage_terms[person.id].append((coefficient, x))

        def edge(a, b, minutes=0):
            literal = model.new_bool_var(f"arc_{d}_{a}_{b}")
            arcs.append((a, b, literal))
            edge_vars[a, b] = literal
            if b:
                earliest = request.start if a == 0 else starts[a] + places[a - 1].duration
                rest = request.pace.break_minutes if request.pace and a else 0
                model.add(starts[b] >= earliest + minutes + rest).only_enforce_if(literal)
                day_drive.append(minutes * literal)
                drive_terms.append(
                    integer(objective.weights.travel * minutes / objective.available_minutes)
                    * literal
                )

        for i, place in enumerate(places, 1):
            travel = data.travel.get(("start", place.id))
            if travel is not None and max(
                request.start + travel, place.opens
            ) + place.duration <= min(request.end, place.closes):
                edge(0, i, travel)
            edge(i, 0)  # Virtual sink: returning to the origin is outside the current model.
            for j, target in enumerate(places, 1):
                if i == j:
                    continue
                travel = data.travel.get((place.id, target.id))
                if travel is not None and max(
                    max(request.start, place.opens) + place.duration + travel, target.opens
                ) + target.duration <= min(request.end, target.closes):
                    edge(i, j, travel)
        model.add(sum(chosen.values()) >= used)
        if request.pace:
            model.add(sum(chosen.values()) <= request.pace.max_visits)
            model.add(
                sum(places[i - 1].duration * x for i, x in chosen.items())
                + sum(day_drive)
                + request.pace.break_minutes * (sum(chosen.values()) - used)
                <= request.pace.max_active_minutes
            )
        model.add_circuit(arcs)
        day_models.append((places, chosen, starts, used, edge_vars))

    for pid, variables in selections.items():
        model.add(sum(variables) <= 1)
    for pid in request.mandatory:
        model.add(sum(selections.get(pid, [])) == 1)
    model.add(sum(x for variables in selections.values() for x in variables) >= 1)
    model.add(sum(all_cost) <= request.budget)
    coverages, max_domain = [], 0
    for pid, terms in coverage_terms.items():
        bound = sum(c for c, _ in terms)
        max_domain = max(max_domain, bound)
        coverage = model.new_int_var(0, bound, f"coverage_{pid}")
        model.add(coverage == sum(c * x for c, x in terms))
        coverages.append(coverage)
    high = model.new_int_var(0, max_domain, "max_coverage")
    low = model.new_int_var(0, max_domain, "min_coverage")
    if coverages:
        # Exact selectors avoid the objective-only lin_max presolve issue OR-Tools #5453.
        # Inequalities alone would allow slack in a time-limited incumbent's score.
        maxima, minima = [], []
        for i, coverage in enumerate(coverages):
            is_max = model.new_bool_var(f"max_is_{i}")
            is_min = model.new_bool_var(f"min_is_{i}")
            maxima.append(is_max)
            minima.append(is_min)
            model.add(high >= coverage)
            model.add(low <= coverage)
            model.add(high == coverage).only_enforce_if(is_max)
            model.add(low == coverage).only_enforce_if(is_min)
        model.add(sum(maxima) == 1)
        model.add(sum(minima) == 1)
    else:
        model.add(high == 0)
        model.add(low == 0)
    model.maximize(sum(reward_terms) - sum(drive_terms) - high + low)

    if baseline.is_feasible:
        for (places, chosen, starts, used, edges), day in zip(
            day_models, baseline.days, strict=True
        ):
            by_id = {p.id: i for i, p in enumerate(places, 1)}
            order = [by_id[v.place_id] for v in day.visits]
            desired = set(zip([0] + order, order + [0])) if order else set()
            visits = {v.place_id: v for v in day.visits}
            model.add_hint(used, bool(order))
            for i, x in chosen.items():
                visit = visits.get(places[i - 1].id)
                model.add_hint(x, visit is not None)
                model.add_hint(starts[i], visit.start if visit else request.start)
            for arc, literal in edges.items():
                model.add_hint(literal, arc in desired)
    error = 2.5 * objective.candidate_count / scale + 1e-9
    if request.pace:
        error += 0.5 * len(inputs) / scale
    validation_error = model.validate()
    if validation_error:
        raise ValueError(f"Invalid CP-SAT model: {validation_error}")
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = seed
    solver.parameters.absolute_gap_limit = 0.0
    solver.parameters.relative_gap_limit = 0.0
    remaining = max(0.0, time_limit - (perf_counter() - started))
    solver.parameters.max_time_in_seconds = remaining
    status = solver.solve(model) if remaining > 0 else cp_model.UNKNOWN
    if status == cp_model.MODEL_INVALID:
        raise RuntimeError("CP-SAT rejected the model")
    found = status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    schedule = [[] for _ in inputs]
    if found:
        for d, (places, chosen, starts, used, edges) in enumerate(day_models):
            successors = {
                a: b for (a, b), literal in edges.items() if solver.boolean_value(literal)
            }
            node, previous, now = successors.get(0, 0), "start", request.start
            while node:
                place = places[node - 1]
                arrival = now + inputs[d].travel[previous, place.id]
                rest = request.pace.break_minutes if request.pace and schedule[d] else 0
                begins = max(arrival + rest, place.opens)
                now = begins + place.duration
                schedule[d].append(Visit(place.id, arrival, begins, now))
                previous, node = place.id, successors[node]
    cost = sum(
        objective.places[d][v.place_id].cost_per_person * len(request.participants)
        for d, visits in enumerate(schedule)
        for v in visits
    )
    report, days = validate_period(request, schedule, inputs, cost)
    if found and not report.is_valid:
        raise RuntimeError("CP-SAT solution failed independent validation")
    score = objective.evaluate(schedule) if found else None
    model_value = solver.objective_value / scale if found else None
    model_bound = solver.best_objective_bound / scale if found else None
    max_score = objective.weights.reward + (objective.weights.spread if request.pace else 0)
    upper = min(max_score + 1e-9, model_bound + error) if found else None
    if found and (abs(score.value - model_value) > error or upper + 1e-8 < score.value):
        raise RuntimeError(
            f"CP-SAT objective mismatch: evaluated={score.value}, scaled={model_value}, "
            f"bound={model_bound}, rounding_limit={error}"
        )
    metadata = SolverReport(
        backend="ortools-cp-sat",
        version=ortools.__version__,
        status=solver.status_name(status),
        time_limit_seconds=time_limit,
        search_time_limit_seconds=remaining,
        elapsed_seconds=perf_counter() - started,
        solver_seconds=solver.wall_time if remaining > 0 else 0.0,
        seed=seed,
        workers=1,
        scale=scale,
        objective_error_bound=error,
        integer_model_optimal=status == cp_model.OPTIMAL,
        model_objective=model_value,
        model_best_bound=model_bound,
        original_upper_bound=upper,
        absolute_gap_bound=max(0.0, upper - score.value) if found else None,
        warm_start=baseline.is_feasible,
    )
    visits = [v for day in schedule for v in day]
    return Result(
        found,
        visits,
        cost,
        {
            p.id: score.participant_utility[p.id] / len(visits) if found else 0
            for p in request.participants
        },
        []
        if found
        else [f"cp_sat_{metadata.status.lower()}"] + [v.code for v in report.violations],
        report,
        inputs[0].data_context if len(inputs) == 1 else None,
        days if len(inputs) > 1 else None,
        score,
        metadata,
    )
