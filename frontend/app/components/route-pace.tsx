import type { PlanningSummary } from "../../lib/api";

export const paceNames = {unrestricted:"Без ограничений темпа",relaxed:"Спокойный",balanced:"Обычный",intensive:"Насыщенный"};

const warnings:Record<string,string> = {
  availability_limits_coverage:"Доступность кандидатов по датам ограничивает число дней с посещениями. Одинаковое место нельзя распределить на несколько дней.",
  catalog_smaller_than_period:"Доступных уникальных мест меньше, чем дней поездки. Повторные посещения исключены.",
  dates_without_candidates:"На части дат нет мест с подходящими подтверждёнными данными. Проверьте каталог и даты.",
  unscheduled_days:"Часть дней осталась без посещений. Доступность мест, бюджет и ограничения расписания могут мешать заполнить весь период.",
  all_candidates_scheduled:"Все кандидаты этого расчёта включены. Увеличение бюджета само по себе не добавит новых мест.",
  candidate_subset:"На части дат расчёт рассматривает подмножество каталога. Число кандидатов может отличаться от числа доступных мест в каталоге.",
};

export function RoutePace({summary}:{summary?:PlanningSummary|null}) {
  if (!summary) return null;
  const pace=summary.pace;
  return <section className="route-objective" aria-label="Темп и заполнение поездки">
    <h3>Темп: {paceNames[pace?.mode || "unrestricted"]} · {summary.active_days} из {summary.total_days} дней с посещениями</h3>
    <p>В плане {summary.scheduled_count} из {summary.candidate_count} уникальных мест, переданных в расчёт.</p>
    {summary.coverage_upper_bound < summary.total_days && <p>По доступности кандидатов можно занять не более {summary.coverage_upper_bound} из {summary.total_days} дней. Это верхняя граница без учёта бюджета, переездов и дневных лимитов; достижимость не гарантируется.</p>}
    {pace && <p>До {pace.max_visits} мест и {pace.max_active_minutes} мин нагрузки в день. Перерыв между посещениями — минимум {pace.break_minutes} мин.</p>}
    <p className="hint">Нагрузка: посещения, переезды и обязательные перерывы. Дополнительное ожидание открытия показано отдельно. Бюджет покрывает билеты и указанные заказы в кафе; жильё, дорога до города, транспорт и остальное питание в сумму не входят.</p>
    {!!summary.warnings.length && <ul>{summary.warnings.map(code=><li key={code}>{warnings[code] || code}</li>)}</ul>}
  </section>;
}
