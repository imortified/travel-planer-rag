import type { SemanticSearch } from "./api";

export function semanticSelection(value: SemanticSearch | undefined, manual: string) {
  const blocked = !value || value.can_apply === false || [
    "negative_or_complex", "unsupported_condition", "multiple_interests", "no_evidence",
  ].includes(value.reason);
  const category = blocked ? "" : value.category || manual;
  return {
    blocked,
    category,
    matches: blocked ? [] : value.matches.filter(m => m.score >= value.document_threshold).slice(0, 3),
    message: value?.message || (value?.reason === "negative_or_complex"
      ? "Отрицания задайте ползунками или исключите места в каталоге."
      : value?.category ? "Проверьте предлагаемый интерес перед применением."
      : "Выберите категорию вручную или уточните пожелание."),
  };
}
