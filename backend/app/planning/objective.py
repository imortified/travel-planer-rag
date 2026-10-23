"""One fixed objective context per request, shared by all search attempts and days."""

from dataclasses import dataclass
from datetime import date
from math import isfinite

from app.planning.models import DayInput, ObjectiveReport, Request

VERSION = "normalized-group-objective-v1"


@dataclass(frozen=True)
class Weights:
    reward: float = 1.0
    cost: float = 0.15
    travel: float = 0.15
    fairness: float = 0.35

    def __post_init__(self):
        if any(not isfinite(v) or v < 0 for v in self.as_dict().values()):
            raise ValueError("Objective weights must be finite and nonnegative")
        if self.reward == 0:
            raise ValueError("Reward weight must be positive")

    def as_dict(self):
        return {
            "reward": self.reward,
            "cost": self.cost,
            "travel": self.travel,
            "fairness": self.fairness,
        }


class Objective:
    def __init__(self, request: Request, inputs: list[DayInput], weights=Weights()):
        if not inputs or not request.participants or request.end <= request.start:
            raise ValueError("Objective requires participants, days and a positive day window")
        if request.budget < 0:
            raise ValueError("Budget cannot be negative")
        self.request, self.weights = request, weights
        self.places = [{p.id: p for p in day.places} for day in inputs]
        ids = set().union(*(set(day) for day in self.places)) - request.excluded
        self.candidate_count = len(ids)
        # A POI can be visited once; its best date-specific utility defines an upper bound.
        # This bound ignores reachability/budget, and is NOT an achievable optimum.
        self.bounds = {
            person.id: sum(
                max(person.interests.get(day[pid].category, 0) for day in self.places if pid in day)
                for pid in sorted(ids)
            )
            for person in request.participants
        }
        self.reward_bound = sum(self.bounds.values()) / len(request.participants)
        self.available_minutes = len(inputs) * (request.end - request.start)

    @classmethod
    def single(cls, request, places, weights=Weights()):
        return cls(request, [DayInput(date.min, places, {})], weights)

    def evaluate(self, visits_by_day):
        if len(visits_by_day) != len(self.places):
            raise ValueError("Objective day count differs from reference context")
        totals = {p.id: 0.0 for p in self.request.participants}
        cost, travel, seen = 0, 0, set()
        for visits, places in zip(visits_by_day, self.places, strict=True):
            previous_end = self.request.start
            for visit in visits:
                if visit.place_id in seen:
                    raise ValueError("Objective cannot score repeated POIs")
                seen.add(visit.place_id)
                place = places[visit.place_id]
                leg = visit.arrival - previous_end
                if leg < 0:
                    raise ValueError("Negative travel duration")
                travel += leg  # Waiting (start-arrival) is not driving time.
                previous_end = visit.end
                cost += place.cost_per_person * len(totals)
                for person in self.request.participants:
                    totals[person.id] += person.interests.get(place.category, 0)
        mean = sum(totals.values()) / len(totals)
        coverage = {
            pid: totals[pid] / bound if bound else 0.0 for pid, bound in self.bounds.items()
        }
        active = [coverage[pid] for pid, bound in self.bounds.items() if bound > 0]
        gap = max(active) - min(active) if active else 0.0
        reward = mean / self.reward_bound if self.reward_bound else 0.0
        # Only zero-cost visits are feasible at B=0. No artificial denominator of one euro.
        if self.request.budget == 0 and cost:
            raise ValueError("Paid visits cannot be scored with a zero budget")
        cost_ratio = cost / self.request.budget if self.request.budget else 0.0
        travel_ratio = travel / self.available_minutes
        contributions = {
            "reward": self.weights.reward * reward,
            "cost": -self.weights.cost * cost_ratio,
            "travel": -self.weights.travel * travel_ratio,
            "fairness": -self.weights.fairness * gap,
        }
        return ObjectiveReport(
            version=VERSION,
            weights=self.weights.as_dict(),
            value=sum(contributions.values()),
            contributions=contributions,
            participant_utility=totals,
            participant_coverage=coverage,
            utility_bounds=dict(self.bounds),
            excluded_from_fairness=[pid for pid, bound in self.bounds.items() if bound == 0],
            mean_utility=mean,
            reward_bound=self.reward_bound,
            reward=reward,
            cost_ratio=cost_ratio,
            travel_minutes=travel,
            travel_ratio=travel_ratio,
            fairness_gap=gap,
            candidate_count=self.candidate_count,
            budget=self.request.budget,
            available_minutes=self.available_minutes,
        )

    def key(self, visits_by_day):
        ids = {v.place_id for day in visits_by_day for v in day}
        report = self.evaluate(visits_by_day)
        return (
            not ids or bool(self.request.mandatory - ids),
            -len(self.request.mandatory & ids),
            -round(report.value, 12),
            -sum(bool(day) for day in visits_by_day),
            max(len(day) for day in visits_by_day),
            report.cost_ratio,
            report.travel_minutes,
            tuple((i, v.end, v.place_id) for i, day in enumerate(visits_by_day) for v in day),
        )
