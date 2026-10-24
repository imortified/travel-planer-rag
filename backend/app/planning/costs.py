"""Pure reference-cost arithmetic, shared by every optimizer and the independent validator."""


def group_cost(place, participants):
    if type(place.cost_per_person) is not int or place.cost_per_person < 0:
        raise ValueError("Reference price must be a nonnegative integer amount")
    if place.participant_costs is None:
        return place.cost_per_person * len(participants)
    identifiers = {p if isinstance(p, str) else p.id for p in participants}
    if (
        len(identifiers) != len(participants)
        or set(place.participant_costs) != identifiers
        or any(type(value) is not int or value < 0 for value in place.participant_costs.values())
    ):
        raise ValueError("Participant price vector does not match reference group")
    return sum(place.participant_costs.values())


def age_at(person, day):
    if person.birth_date is None:
        if person.age_on_start is not None and (
            type(person.age_on_start) is not int or not 0 <= person.age_on_start <= 120
        ):
            raise ValueError("Возраст должен быть целым числом от 0 до 120")
        return person.age_on_start
    if person.age_on_start is not None:
        raise ValueError("Укажите только один источник возраста")
    birth = person.birth_date
    age = day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))
    if not 0 <= age <= 120 or birth > day:
        raise ValueError("Возраст на дату поездки должен быть от 0 до 120 лет")
    return age
