"""Finite single-interest policy: explicit topics corroborated by actual candidate categories."""

import re

from app.nlp.grounding import unsupported_conditions

VERSION = "semantic-evidence-v2"
NAMES = {"history": "история", "nature": "природа", "food": "еда", "art": "искусство"}
# Topic cues, not a universal Russian parser. A generic 'museum' does not imply history.
TOPICS = {
    "history": (
        r"\b(?:истори(?:я|и|ю|ей)\b|историческ\w*\s+(?:музе\w*|коллекц\w*)|"
        r"археолог\w*|руин\w*|древн\w*\s+цивилизац\w*|диктатур\w*|"
        r"сопротивлен\w*|политическ\w*\s+заключ\w*)"
    ),
    "art": r"\b(?:искусств\w*|живопис\w*|скульптур\w*|художествен\w*|галере\w*|реликвар\w*)",
    "nature": (
        r"\b(?:природ\w*|ботаническ\w*|растени\w*|оранжере\w*|дерев\w*|"
        r"зелень\b|зелени\b|озер\w*|пруд\w*|птиц\w*|парк\w*|сад(?:а|у|е|ом|ы|ов)?\b)"
    ),
    "food": (
        r"\b(?:ед(?:а|у|ы|ой)\b|кухн\w*|гастроном\w*|дегустац\w*|бранч\w*|"
        r"выпечк\w*|кондитер\w*|ресторан\w*|кафе\b|кофе\b|эспрессо\b|завтрак\w*|позавтрак\w*)"
    ),
}
NEGATIVE = (
    r"\b(?:не|нет|без|кроме|not|without)\b|"
    r"\b(?:ненави\w*|избега\w*|исключ\w*|неинтерес\w*)"
)


def assess(text, ranking, matches, categories, threshold, margin, document_threshold):
    text = text.casefold().replace("ё", "е")
    topics = [key for key, pattern in TOPICS.items() if re.search(pattern, text)]
    warnings = unsupported_conditions(text)
    if re.search(r"\b(?:аллерген\w*|лактоз\w*|глютен\w*|орех\w*|арахис\w*)", text) and not any(
        "Диетические" in warning for warning in warnings
    ):
        warnings.append("Диетические ограничения и аллергены не проверяются")
    if re.search(r"\b(?:погод\w*|прогноз\w*|осадк\w*|температур\w*)", text):
        warnings.append("Погода и прогноз не входят в поиск по описаниям мест")
    result = {
        "policy_version": VERSION,
        "detected_categories": topics,
        "category": None,
        "category_source": None,
        "can_apply": False,
        "needs_clarification": True,
        "warnings": warnings,
    }

    def blocked(reason, message):
        return {**result, "reason": reason, "message": message}

    if warnings:
        return blocked(
            "unsupported_condition",
            "Поиск по описаниям не подтверждает это условие. "
            "Проверьте его у источника и задайте тему отдельно.",
        )
    if re.search(NEGATIVE, text):
        return blocked(
            "negative_or_complex",
            "Пожелание содержит отрицание или исключение. "
            "Задайте веса ползунками, исключите места в каталоге "
            "или найдите положительные пожелания по отдельности.",
        )
    if len(topics) > 1:
        return blocked(
            "multiple_interests",
            "Указано несколько интересов: " + ", ".join(NAMES[t] for t in topics) + ". "
            "Найдите их по отдельности или задайте веса ползунками.",
        )
    top = matches[0] if matches and matches[0]["score"] >= document_threshold else None
    explicit = topics[0] if topics else None
    if explicit and top and categories[top["place_id"]] == explicit:
        result.update(category=explicit, category_source="explicit_topic_and_place")
    elif (
        ranking[0][1] >= threshold
        and ranking[0][1] - ranking[1][1] >= margin
        and (not explicit or explicit == ranking[0][0])
        and (not explicit or (top and categories[top["place_id"]] == explicit))
    ):
        result.update(category=ranking[0][0], category_source="category_profile")
    if result["category"]:
        return {
            **result,
            "can_apply": True,
            "needs_clarification": False,
            "reason": "matched",
            "message": "Проверьте предлагаемый интерес перед применением. "
            "Сходство описаний не подтверждает дополнительные условия поездки.",
        }
    if top is None:
        return blocked(
            "no_evidence",
            "В описаниях доступных мест не найдено подтверждения этому пожеланию. "
            "Уточните тему или задайте интересы ползунками.",
        )
    return {
        **result,
        "can_apply": True,
        "reason": "uncertain",
        "message": "Есть близкие описания, но категория неоднозначна. "
        "Выберите интерес вручную или уточните пожелание.",
    }
