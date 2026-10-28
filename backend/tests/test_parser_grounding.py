"""Reject recorded model mistakes and exercise new wording beyond the measured corpus."""

import json
from datetime import date

import pytest

from app.core.config import ROOT
from app.evaluation.scoring import grade
from app.nlp.grounding import ground_dates, ground_interests, name_present, unsupported_conditions
from app.nlp.parser import parse_request
from app.schemas.assistant import ExtractedTrip, ParseInput

CORPUS = json.loads((ROOT / "data/evaluation/llm-parser.json").read_text("utf-8"))
BASELINE = json.loads((ROOT / "docs/llm-quality-evaluation.json").read_text("utf-8"))
V2 = json.loads((ROOT / "docs/llm-quality-parsing-v2.json").read_text("utf-8"))
ADDITIONAL = json.loads((ROOT / "data/evaluation/llm-parser-additional.json").read_text("utf-8"))
V2_ADDITIONAL = json.loads((ROOT / "docs/llm-quality-parsing-additional.json").read_text("utf-8"))


class RecordedModel:
    def __init__(self, raw):
        self.raw = raw

    def structured(self, system, data, schema):
        return schema.model_validate_json(json.dumps(self.raw)), "recorded-baseline"


@pytest.mark.parametrize(
    "case,row",
    list(zip(CORPUS["cases"], BASELINE["parser"], strict=True)),
    ids=[c["id"] for c in CORPUS["cases"]],
)
def test_recorded_predictions_pass_server_guards(case, row):
    assert case["id"] == row["id"]
    result = parse_request(
        ParseInput(text=case["text"], member_names=case["members"]),
        RecordedModel(row["attempts"][0]["output"]),
        date.fromisoformat(CORPUS["reference_date"]),
    )
    assert all(c["passed"] for c in grade(case, result)), grade(case, result)


SCOPE_REGRESSIONS = list(zip(CORPUS["cases"], V2["parser"], strict=True)) + [
    (case, row)
    for case, row in zip(ADDITIONAL["cases"], V2_ADDITIONAL["parser"], strict=True)
    if case["id"] in {"personal-contrast", "supported-car-mode"}
]


@pytest.mark.parametrize("case,row", SCOPE_REGRESSIONS, ids=[c["id"] for c, _ in SCOPE_REGRESSIONS])
def test_new_generations_keep_only_grounded_scopes(case, row):
    result = parse_request(
        ParseInput(text=case["text"], member_names=case["members"]),
        RecordedModel(row["attempts"][0]["output"]),
        date(2026, 10, 24),
    )
    assert all(c["passed"] for c in grade(case, result)), grade(case, result)


@pytest.mark.parametrize(
    "quote,text",
    [
        ("Рим", "Едем в Рим"),
        ("Лиссабон", "Не Лиссабон, а Париж"),
        ("Лиссабон", "Едем не в Лиссабон, а в Париж"),
        ("Париж", "Едем в Париж"),
    ],
)
def test_unknown_or_negated_city_cannot_become_supported_destination(quote, text):
    raw = {"destination": "Lisbon", "evidence": {"destination": quote}}
    result = parse_request(
        ParseInput(text=text, member_names=["Аня"]), RecordedModel(raw), date(2026, 10, 24)
    )
    assert result.draft.destination is None
    assert "destination" in {q.field for q in result.questions}


def test_budget_year_does_not_supply_missing_date_year():
    draft = ExtractedTrip(
        travel_date=date(2026, 11, 4), end_date=date(2026, 11, 4), duration_days=1
    )
    ground_dates(draft, "4 ноября, общий бюджет 2026 евро", "4 ноября", True, date(2026, 10, 24))
    assert draft.travel_date is None and draft.end_date is None


def test_one_date_does_not_acquire_a_hallucinated_duration():
    draft = ExtractedTrip(travel_date=date(2026, 11, 4), duration_days=3)
    ground_dates(draft, "4 ноября 2026", "4 ноября 2026", True, date(2026, 10, 24))
    assert (
        draft.travel_date == date(2026, 11, 4)
        and draft.duration_days is None
        and draft.end_date is None
    )


def test_date_of_planning_does_not_override_explicit_travel_date():
    draft = ExtractedTrip(travel_date=date(2026, 11, 4))
    ground_dates(
        draft,
        "Сегодня планируем поездку на 4 ноября 2026",
        "4 ноября 2026",
        True,
        date(2026, 10, 24),
    )
    assert draft.travel_date == date(2026, 11, 4)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("ближайшую среду", date(2026, 10, 28)),
        ("следующий понедельник", date(2026, 10, 26)),
        ("послезавтра", date(2026, 10, 26)),
        ("завтра или послезавтра", None),
    ],
)
def test_only_unambiguous_relative_dates_are_resolved(text, expected):
    draft = ExtractedTrip()
    ground_dates(draft, text, None, False, date(2026, 10, 24))
    assert draft.travel_date == expected


def test_personal_negation_and_contrast_do_not_apply_to_another_member():
    draft = ExtractedTrip(
        participants=[{"name": "Варя", "interests": {}}, {"name": "Пётр", "interests": {}}],
        common_interests={"history": 1, "art": 1, "nature": 1},
    )
    uncertain = ground_interests(
        draft, "Варя любит историю, но не искусство. Пётр любит природу.", []
    )
    assert not uncertain and draft.common_interests == {}
    assert draft.participants[0].interests == {"history": 1, "art": 0}
    assert draft.participants[1].interests == {"nature": 1}


def test_inflected_names_and_personal_dislikes_are_preserved():
    draft = ExtractedTrip(
        participants=[{"name": "Ольга", "interests": {}}, {"name": "Иван", "interests": {}}],
        common_interests={"history": 1, "nature": 1},
    )
    assert not ground_interests(draft, "Ольге история не интересна, Ивану нравится природа.", [])
    assert draft.common_interests == {}
    assert draft.participants[0].interests == {"history": 0}
    assert draft.participants[1].interests == {"nature": 1}


def test_ambiguous_pronouns_and_mixed_subjects_require_clarification():
    for text in ("Ей не нравится история", "Аня любит историю и Вера любит природу"):
        draft = ExtractedTrip(
            participants=[{"name": "Аня", "interests": {}}, {"name": "Вера", "interests": {}}]
        )
        assert ground_interests(draft, text, [])


def test_names_use_word_boundaries_and_keep_canonical_spelling():
    assert name_present("Ольга", "Ольге нравится история")
    assert name_present("Пётр", "Петру нравится природа")
    assert not name_present("Анна", "Жанна любит природу")


@pytest.mark.parametrize(
    "text",
    [
        "Только пешком",
        "Нужен общественный транспорт",
        "На велосипеде",
        "Исключительно веганские блюда",
        "Без глютена",
    ],
)
def test_unsupported_conditions_cannot_be_silently_dropped(text):
    assert unsupported_conditions(text)


def test_supported_transport_and_ordinary_nature_interests_do_not_warn():
    assert unsupported_conditions("На машине. Любим природу и парки.") == []


def test_adverbial_negation_and_restrictions_do_not_create_positive_preferences():
    draft = ExtractedTrip()
    assert not ground_interests(draft, "Не очень любим историю. Хочу исключить парки.", [])
    assert draft.common_interests == {"history": 0}


def test_first_person_in_a_group_requires_identity():
    draft = ExtractedTrip(
        participants=[{"name": "Аня", "interests": {}}, {"name": "Вера", "interests": {}}]
    )
    assert ground_interests(draft, "Мне не нравится история.", [])
    assert draft.common_interests == {}


@pytest.mark.parametrize("quote", ["27 октября 2026", "27.10.2026", "2026-10-27"])
def test_explicit_single_date_is_recovered_when_model_puts_it_in_end_only(quote):
    draft = ExtractedTrip(end_date=date(2026, 10, 27))
    ground_dates(draft, quote, quote, True, date(2026, 10, 24))
    assert draft.travel_date == date(2026, 10, 27) and draft.end_date is None


def test_impossible_calendar_date_is_not_silently_repaired():
    draft = ExtractedTrip(travel_date=date(2026, 2, 28))
    ground_dates(draft, "31 февраля 2026", "31 февраля 2026", True, date(2026, 1, 1))
    assert draft.travel_date is None


@pytest.mark.parametrize("field", ["mandatory_names", "excluded_names"])
def test_participant_names_cannot_silently_become_place_constraints(field):
    result = parse_request(
        ParseInput(
            text="Аня и Борис едут в Лиссабон. Посетим Oceanário.", member_names=["Аня", "Борис"]
        ),
        RecordedModel({field: ["Аня", "Борис", "Oceanário"]}),
        date(2026, 10, 24),
    )
    assert getattr(result.draft, field) == ["Oceanário"]
    assert field in {question.field for question in result.questions}


def test_incomplete_request_keeps_city_duration_amount_and_count():
    text = (
        "Бюджет 1800. Хотим побывать в Лисабоне. Двое взрослых, муж и жена. "
        "36 и 34 соответственно. Хочется спокойно погулять недельку по Португалии."
    )
    # Actual local model error: a correct quote accompanied by the wrong city enum,
    # ages treated as date evidence, and an omitted duration.
    result = parse_request(
        ParseInput(text=text),
        RecordedModel({
            "destination": "Porto", "budget_eur": 1800, "participant_count": 2,
            "evidence": {
                "destination": "Лисабон", "dates": "36 и 34",
                "budget": "1800", "participants": "муж и жена",
            },
        }),
        date(2026, 10, 28),
    )
    assert result.draft.destination == "Lisbon"
    assert result.draft.duration_days == 7
    assert result.draft.budget_eur == 1800
    assert result.draft.participant_count == 2
    assert result.draft.travel_date is None and result.draft.end_date is None
    assert result.draft.currency is None and result.group_budget is None
    assert result.draft.participants == []
    assert {q.field for q in result.questions} == {
        "travel_date", "participants", "currency", "budget_scope",
    }


@pytest.mark.parametrize("city", ["Лисабоне", "Лиссабоне", "Lisboa"])
def test_city_is_recovered_from_literal_evidence_without_guessing_from_country(city):
    result = parse_request(
        ParseInput(text=f"Хотим побывать в {city}, Португалия"),
        RecordedModel({"evidence": {"destination": city}}),
    )
    assert result.draft.destination == "Lisbon"


@pytest.mark.parametrize(
    "text", ["Хотим в Португалию", "Не в Лисабон", "Лисабон и Порту", "Лисабон или Париж"],
)
def test_country_negation_and_multiple_cities_do_not_select_lisbon(text):
    result = parse_request(
        ParseInput(text=text), RecordedModel({"evidence": {"destination": text}}),
    )
    assert result.draft.destination is None


def test_literal_city_is_preserved_even_when_model_omits_its_evidence():
    result = parse_request(
        ParseInput(text="Хотим побывать в Лисабоне. По Португалии."), RecordedModel({}),
    )
    assert result.draft.destination == "Lisbon"
    assert result.draft.evidence.destination == "Лисабоне"


@pytest.mark.parametrize(
    "text,days",
    [
        ("Хотим на неделю", 7),
        ("Хочется погулять недельку по Португалии", 7),
        ("Едем на две недели", 14),
        ("Едем на 3 дня", 3),
        ("Погулять не неделю, а на две недели", 14),
        ("Через неделю хотим в отпуск", None),
        ("Неделю назад планировали отпуск", None),
        ("Не на неделю", None),
        ("На 3 дня или на неделю", None),
        ("Муж и жена. 25 и 23 соответственно", None),
    ],
)
def test_stay_duration_does_not_require_or_invent_departure_date(text, days):
    draft = ExtractedTrip(duration_days=5)
    ground_dates(draft, text, None, False, date(2026, 10, 28))
    assert draft.duration_days == days
    assert draft.travel_date is None and draft.end_date is None


def test_week_with_explicit_start_gets_inclusive_end_date():
    result = parse_request(
        ParseInput(text="Едем в Лисабон 10 ноября 2026 на неделю"),
        RecordedModel({
            "travel_date": "2026-11-10",
            "evidence": {"destination": "Лисабон", "dates": "10 ноября 2026"},
        }),
    )
    assert result.draft.travel_date == date(2026, 11, 10)
    assert result.draft.end_date == date(2026, 11, 16)


@pytest.mark.parametrize(
    "quote,scope,total",
    [
        ("Общий бюджет на двоих 1800 евро", "group", 180000),
        ("Бюджет 900 евро на человека", "per_person", 180000),
        ("Бюджет 1800 евро", None, None),
        ("Общий бюджет 900 евро на человека", None, None),
    ],
)
def test_budget_scope_and_currency_are_recovered_when_model_omits_them(quote, scope, total):
    result = parse_request(
        ParseInput(text=quote, member_names=["Аня", "Борис"]),
        RecordedModel({
            "budget_eur": 900 if "900" in quote else 1800,
            "evidence": {"budget": quote},
        }),
    )
    assert result.draft.currency == "EUR"
    assert result.draft.budget_scope == scope
    assert result.group_budget == total
