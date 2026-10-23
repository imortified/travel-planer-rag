import type { ObjectiveReport, Trip } from "../../lib/api";

const number = (value:number) => (value===0?0:value).toLocaleString("ru-RU",{maximumFractionDigits:4});

export function RouteObjective({report, trip}: {report?:ObjectiveReport|null;trip:Trip}) {
  if (!report) return <p className="hint">Для этого результата общая оценка по новой формуле не сохранялась.</p>;
  const rows = [
    {key:"reward",label:"Соответствие интересам R",value:report.reward},
    {key:"cost",label:"Доля бюджета C",value:report.cost_ratio},
    {key:"travel",label:"Доля времени на переезды T",value:report.travel_ratio},
    {key:"fairness",label:"Неравенство покрытия интересов P",value:report.fairness_gap},
  ];
  const names = Object.fromEntries(trip.participants_snapshot.map(p=>[String(p.user_id),p.name]));
  return <section className="route-objective" aria-label="Оценка маршрута">
    <div className="objective-heading"><h3>Оценка маршрута</h3><strong>F = {number(report.value)}</strong></div>
    <p className="hint">Выше — лучше при одинаковом запросе и коэффициентах. Оценка объединяет интересы, расходы, переезды и баланс участников; это не процент удовлетворённости.</p>
    <details><summary>Формула и вклад критериев</summary>
      <p>F = {number(report.weights.reward)}R − {number(report.weights.cost)}C − {number(report.weights.travel)}T − {number(report.weights.fairness)}P</p>
      <div className="objective-scroll"><table><thead><tr><th>Критерий</th><th>Значение</th><th>Вклад в F</th></tr></thead><tbody>{rows.map(r=><tr key={r.key}><th scope="row">{r.label}</th><td>{number(r.value)}</td><td>{report.contributions[r.key]>0?"+":""}{number(report.contributions[r.key])}</td></tr>)}</tbody></table></div>
      <p>Переезды: {report.travel_minutes} мин из {report.available_minutes} мин дневных окон за всю поездку. Ожидание открытия не считается переездом.</p>
      <p>R — доля верхней оценки полезности доступного каталога ({report.candidate_count} мест). Граница не учитывает совместимость расписаний, бюджет и достижимость; она не гарантирует, что можно посетить все места.</p>
      <p>Покрытие участника — его накопленная полезность, делённая на сумму возможной полезности всех доступных ему мест. P — разница максимального и минимального покрытия.</p>
      <ul>{Object.entries(report.participant_coverage).map(([id,value])=><li key={id}>{names[id] || id}: {number(value*100)}% покрытия доступных интересов</li>)}</ul>
      {!!report.excluded_from_fairness.length && <p>В каталоге нет представленных интересов для: {report.excluded_from_fairness.map(id=>names[id] || id).join(", ")}. Они исключены из расчёта P; их предпочтения требуют уточнения или расширения каталога.</p>}
      <p>Коэффициенты — начальная настройка для экспериментов. Бюджет остаётся пределом расходов. Выполнимость проверяется отдельно; поиск не гарантирует глобального оптимума.</p>
      <p className="hint">Версия: {report.version}. Шкалы общие для всех дней и вариантов этого расчёта.</p>
    </details>
  </section>;
}
