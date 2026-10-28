"""Conservative checks for explicit Russian phrases; uncertain meanings require user review."""

import re
from datetime import date, timedelta

DESTINATIONS = {
    "Lisbon": r"\b(?:лис{1,2}абон(?:а|е|у|ом)?|lisbon|lisboa)\b",
    "Porto": r"\b(?:порту|porto)\b",
    "Sintra": r"\b(?:синтр(?:а|е|у|ы|ой)|sintra)\b",
    "Demo City": r"\b(?:демо[-\s]*город(?:а|е|у|ом)?|demo\s+city)\b",
}
CATEGORIES = {
    "history": r"\b(?:истори\w*|археолог\w*|руин\w*|древност\w*)",
    "nature": r"\b(?:природ\w*|парк\w*|ботаническ\w*|растени\w*)",
    "art": r"\b(?:искусств\w*|живопис\w*|скульптур\w*|галере\w*)",
    "food": r"\b(?:ед(?:а|у|ы|ой)\b|кухн\w*|гастроном\w*|дегустац\w*|бранч\w*|выпечк\w*)",
}
POSITIVE = r"\b(?:люб\w*|нрав\w*|интерес\w*|предпочита\w*)"
NEGATIVE = (
    r"\b(?:не\s+(?:(?:очень|особо|совсем)\s+)?(?:люб\w*|нрав\w*|интерес\w*|хоч\w*)|"
    r"неинтерес\w*|ненави\w*|избега\w*)"
)
WEEKDAYS = {
    "понедельник": 0,
    "вторник": 1,
    "среда": 2,
    "среду": 2,
    "четверг": 3,
    "пятница": 4,
    "пятницу": 4,
    "суббота": 5,
    "субботу": 5,
    "воскресенье": 6,
}
MONTH = (
    r"(?:январ\w*|феврал\w*|март\w*|апрел\w*|мая|июн\w*|июл\w*|"
    r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
)
MONTH_NUMBERS = {
    name: index
    for index, name in enumerate(
        (
            "января",
            "февраля",
            "марта",
            "апреля",
            "мая",
            "июня",
            "июля",
            "августа",
            "сентября",
            "октября",
            "ноября",
            "декабря",
        ),
        start=1,
    )
}


def normalize(text):
    return re.sub(r"\s+", " ", text.casefold().replace("ё", "е")).strip()


def name_forms(name):
    name = normalize(name)
    forms = {name}
    if not re.fullmatch(r"[а-я]+", name):
        return forms
    if name.endswith("а"):
        vowel = "и" if name[-2:-1] in "гкхжчшщ" else "ы"
        forms |= {name[:-1] + suffix for suffix in ("е", vowel, "у", "ой", "ою")}
    elif name.endswith("я"):
        forms |= {name[:-1] + suffix for suffix in ("е", "и", "ю", "ей")}
    elif name.endswith(("й", "ь")):
        forms |= {name[:-1] + suffix for suffix in ("я", "ю", "е", "ем", "и")}
    elif name[-1] not in "аеёиоуыэюя":
        forms |= {name + suffix for suffix in ("а", "у", "е", "ом")}
    return forms


def name_present(name, text):
    return any(
        re.search(r"(?<!\w)" + re.escape(form) + r"(?!\w)", normalize(text))
        for form in name_forms(name)
    )


def destination_supported(destination, quote, text):
    if destination not in DESTINATIONS:
        return False
    pattern = DESTINATIONS[destination]
    if not re.search(pattern, quote, re.I):
        return False
    positive = {
        city
        for city, expression in DESTINATIONS.items()
        if any(
            not re.search(r"\b(?:не(?:\s+(?:в|во|на))?|вместо|кроме)\s*$", text[: m.start()], re.I)
            for m in re.finditer(expression, text, re.I)
        )
    }
    if len(positive) > 1:
        return False
    matches = list(re.finditer(pattern, text, re.I))
    return any(
        not re.search(r"\b(?:не(?:\s+(?:в|во|на))?|вместо|кроме)\s*$", text[: m.start()], re.I)
        for m in matches
    )


def ground_destination(draft, text):
    """Recover one literal city without relying on the model's enum or copied evidence."""
    cities = {}
    for city, pattern in DESTINATIONS.items():
        for match in re.finditer(pattern, text, re.I):
            if destination_supported(city, match.group(), text):
                # An alternative unknown city must not silently disappear from the request.
                before = re.split(r"[.!?;\n]", text[:match.start()])[-1]
                after = re.split(r"[.!?;\n]", text[match.end():])[0]
                if re.search(r"\b(?:или|либо)\b", before + " " + after, re.I):
                    return False
                cities[city] = match.group()
    if len(cities) != 1:
        return False
    draft.destination, draft.evidence.destination = next(iter(cities.items()))
    return True


def explicit_duration(text):
    """Recover a stated stay length independently of a missing departure date."""
    numbers = {
        "один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
        "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
        "десять": 10, "одиннадцать": 11, "двенадцать": 12,
        "тринадцать": 13, "четырнадцать": 14,
    }
    number = r"(?:\d{1,3}|" + "|".join(numbers) + r")"
    patterns = [
        (r"\b(?:на|длительностью|в\s+течение)\s+(" + number
         + r")\s+(?:день|дня|дней|сутки|суток)\b", 1),
        (r"\b(?:на|длительностью|в\s+течение)\s+(" + number
         + r")\s+недел(?:ю|и|ь|ьку)\b", 7),
        (r"\b(?:на|длительностью|в\s+течение)\s+(неделю|недельку)\b", 7),
        (r"\b(?:погулять|путешествовать|отдыхать|провести|побыть|пробыть)\s+"
         r"(неделю|недельку)\b", 7),
    ]
    durations = set()
    for pattern, multiplier in patterns:
        for match in re.finditer(pattern, text, re.I):
            if re.search(r"\b(?:не|вместо)\s*$", text[:match.start()], re.I):
                continue
            token = match[1].casefold()
            value = int(token) if token.isdecimal() else numbers.get(token, 1)
            days = value * multiplier
            if 1 <= days <= 365:
                durations.add(days)
    return next(iter(durations)) if len(durations) == 1 else None


def single_calendar_date(quote):
    """Recover an explicit single date, never fabricate a range or repair invalid calendar days."""
    candidates, invalid = set(), False
    values = []
    for match in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", quote):
        values.append(tuple(map(int, match.groups())))
    for match in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", quote):
        day, month, year = map(int, match.groups())
        values.append((year, month, day))
    pattern = r"\b(\d{1,2})\s+(" + "|".join(MONTH_NUMBERS) + r")\s+(\d{4})\b"
    for match in re.finditer(pattern, quote, re.I):
        values.append((int(match[3]), MONTH_NUMBERS[match[2].casefold()], int(match[1])))
    for value in values:
        try:
            candidates.add(date(*value))
        except ValueError:
            invalid = True
    range_stated = re.search(
        r"\b(?:по|до)\s+\d|\b\d{1,2}\s*[-–—]\s*\d{1,2}\s+" + MONTH, quote, re.I
    )
    if range_stated or len(candidates) != 1:
        return None, invalid
    return next(iter(candidates)), invalid


def relative_date(text, today):
    matches = []
    for match in re.finditer(r"\b(?:послезавтра|завтра|сегодня)\b", text, re.I):
        if re.search(r"\bне\s*$", text[: match.start()], re.I):
            continue
        days = {"сегодня": 0, "завтра": 1, "послезавтра": 2}[match.group().casefold()]
        matches.append(today + timedelta(days=days))
    weekday_pattern = r"\b(?:следующ\w*|ближайш\w*)\s+(" + "|".join(WEEKDAYS) + r")\b"
    for match in re.finditer(weekday_pattern, text, re.I):
        if re.search(r"\bне\s*$", text[: match.start()], re.I):
            continue
        delta = (WEEKDAYS[match[1].casefold()] - today.weekday()) % 7 or 7
        matches.append(today + timedelta(days=delta))
    return next(iter(set(matches))) if len(set(matches)) == 1 else None


def ground_dates(draft, text, quote, evidence_valid, today):
    """Keep explicit years; resolve one relative phrase. Never borrow a year from the budget."""
    quote = quote if evidence_valid else ""
    draft.duration_days = explicit_duration(text)
    duration_stated = draft.duration_days is not None
    context = quote or text
    explicit_calendar = bool(
        re.search(
            r"\b\d{1,2}\s+" + MONTH + r"|\b\d{4}-\d{2}-\d{2}|\b\d{1,2}\.\d{1,2}", context, re.I
        )
    )
    resolved = relative_date(context, today) if not explicit_calendar else None
    if resolved:
        draft.travel_date = resolved
        draft.end_date = None
        return
    years = {int(y) for y in re.findall(r"\b\d{4}\b", quote)}
    single, invalid = single_calendar_date(quote)
    if (
        not evidence_valid
        or not explicit_calendar
        or invalid
        or not years
        or (draft.travel_date and draft.travel_date.year not in years)
    ):
        draft.travel_date = draft.end_date = None
        return
    if single:
        draft.travel_date = single
        if not duration_stated:
            draft.end_date = None
    if (
        draft.end_date == draft.travel_date
        and not re.search(r"\b(?:по|до)\s+\d", quote, re.I)
        and not duration_stated
    ):
        draft.end_date = None


def unsupported_conditions(text):
    found = []
    if re.search(
        r"\b(?:пеш(?:ком|ий|ая|ие|его|их|ему)|пешеход\w*|общественн\w*\s+транспорт\w*|велосипед\w*|на\s+(?:метро|автобусе))\b",
        text,
        re.I,
    ):
        found.append("Пеший/общественный/велосипедный режим: поддерживаются автомобильные переезды")
    if re.search(
        r"\b(?:веган\w*|вегетариан\w*|безглютен\w*|аллерги\w*|без\s+(?:глютена|лактозы|орехов))\b",
        text,
        re.I,
    ):
        found.append("Диетические ограничения и аллергены не проверяются")
    return found


def discard_supported_conditions(conditions):
    supported = {
        "автомобильный маршрут",
        "автомобильные маршруты",
        "автомобильный транспорт",
        "автомобильные переезды",
        "на автомобиле",
        "на машине",
        "driving",
    }
    return [value for value in conditions if normalize(value).strip(" .:;") not in supported]


def polarity(clause, match):
    if re.search(r"\bне\s*$", clause[: match.start()], re.I):
        return 0
    negatives = list(re.finditer(NEGATIVE, clause, re.I))
    positives = [
        m
        for m in re.finditer(POSITIVE, clause, re.I)
        if not any(n.start() <= m.start() < n.end() for n in negatives)
    ]
    candidates = [
        (min(abs(m.end() - match.start()), abs(m.start() - match.end())), value)
        for value, spans in ((0, negatives), (1, positives))
        for m in spans
    ]
    if not candidates:
        return None
    distance = min(d for d, _ in candidates)
    values = {v for d, v in candidates if d == distance}
    return next(iter(values)) if len(values) == 1 else None


def ground_interests(draft, text, known_names):
    """Scope explicit category words/predicates. Unresolved names/polarity never spread to all."""
    people = {normalize(p.name): p for p in draft.participants}
    names = {normalize(name) for name in known_names} | set(people)
    common, individual, personal_categories = {}, {}, set()
    uncertain, complete = False, True
    # Decimal commas and time dots remain intact. Contrastive clauses have their own polarity.
    separators = list(re.finditer(r"(?<!\d)[,;.!?\n](?!\d)|\s+(?:но|а)\s+", text))
    clauses, start, leading = [], 0, ""
    for sep in separators:
        clauses.append((text[start : sep.start()], leading))
        start, leading = sep.end(), sep.group().strip()
    clauses.append((text[start:], leading))
    previous_owners = set()
    for clause, leading in clauses:
        has_category = any(re.search(pattern, clause, re.I) for pattern in CATEGORIES.values())
        if re.search(POSITIVE + "|" + NEGATIVE, clause, re.I) and not has_category:
            complete = False  # Preserve model interpretation of nonliteral interests for review.
        owners = {name for name in names if name_present(name, clause)}
        collective = bool(
            re.search(r"\b(?:всем|все|нам|мы|любим|хотим|предпочитаем)\b", clause, re.I)
        )
        if (
            not owners
            and not collective
            and previous_owners
            and (leading in {"но", "а"} or re.match(r"\s*не\b", clause, re.I))
        ):
            owners = previous_owners
        personal = owners and not collective
        if personal and len(owners) > 1:
            positions = sorted(
                (m.start(), m.end())
                for name in owners
                for form in name_forms(name)
                for m in re.finditer(r"(?<!\w)" + re.escape(form) + r"(?!\w)", normalize(clause))
            )
            between = normalize(clause)[positions[0][1] : positions[-1][0]] if positions else ""
            if re.search(POSITIVE + "|" + NEGATIVE, between, re.I) or any(
                re.search(p, between, re.I) for p in CATEGORIES.values()
            ):
                uncertain = True
                continue
        pronoun = bool(re.search(r"\b(?:он|она|ему|ей)\b", clause, re.I)) or (
            len(names) > 1 and bool(re.search(r"\b(?:я|мне|меня)\b", clause, re.I))
        )
        if pronoun and not owners and not collective:
            if any(re.search(p, clause, re.I) for p in CATEGORIES.values()):
                uncertain = True
                continue
        for category, pattern in CATEGORIES.items():
            for mention in re.finditer(pattern, clause, re.I):
                value = polarity(clause, mention)
                if personal:
                    personal_categories.add(category)
                if value is None:
                    complete = False
                    continue
                targets = (
                    [individual.setdefault(owner, {}) for owner in owners] if personal else [common]
                )
                for target in targets:
                    if category in target and target[category] != value:
                        uncertain = True
                    target[category] = value
                if personal and any(owner not in people for owner in owners):
                    uncertain = True  # Do not invent a missing member object to attach weights.
        if owners:
            previous_owners = owners
        elif collective:
            previous_owners = set()
    if complete and not uncertain and (common or individual):
        # Fully recognized preference clauses define the scope. Extra model weights have no source.
        draft.common_interests = common
        for owner, person in people.items():
            person.interests = individual.get(owner, {})
    else:
        for category in personal_categories - set(common):
            draft.common_interests.pop(category, None)
        draft.common_interests.update(common)
    for owner, values in individual.items():
        if owner in people:
            people[owner].interests.update(values)
    return uncertain
