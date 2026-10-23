from dataclasses import replace

from app.planning.beam import beam_plan
from app.planning.models import Place, Request, Result
from app.planning.objective import Objective
from app.planning.planner import plan
from app.planning.validator import validate_route


def checked_plan(
    request: Request,
    places: list[Place],
    travel: dict[tuple[str, str], int],
    algorithm: str = "greedy",
    beam_width: int = 32,
    objective=None,
    time_limit=5.0,
) -> Result:
    objective = objective or Objective.single(request, places)
    if algorithm == "cp-sat":
        from datetime import date

        from app.planning.cp_sat import cp_sat_plan
        from app.planning.models import DayInput

        return cp_sat_plan(request, [DayInput(date.min, places, travel)], objective, time_limit)
    if algorithm == "greedy":
        candidate = plan(request, places, travel, objective)
    elif algorithm == "beam":
        candidate = beam_plan(request, places, travel, beam_width, objective)
    else:
        raise ValueError("Unknown algorithm")
    report = validate_route(request, candidate.visits, places, travel, candidate.total_cost)
    reasons = list(dict.fromkeys(candidate.reasons + [v.code for v in report.violations]))
    return replace(
        candidate,
        is_feasible=candidate.is_feasible and report.is_valid,
        reasons=reasons,
        validation=report,
        objective=objective.evaluate([candidate.visits])
        if candidate.is_feasible and report.is_valid
        else None,
    )
