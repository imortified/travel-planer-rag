import type { SolverReport } from "../../lib/api";

const number=(value:number)=>value.toLocaleString("ru-RU",{maximumFractionDigits:6});
const bound=(value:number)=>number(Math.ceil(value*1_000_000)/1_000_000);
const labels:Record<string,string>={OPTIMAL:"Оптимум целочисленной модели доказан",FEASIBLE:"Допустимый маршрут найден; оптимум не доказан",INFEASIBLE:"Невозможность доказана в принятой модели",UNKNOWN:"Поиск завершён без решения; невозможность не доказана"};

export function SolverStatus({report}:{report?:SolverReport|null}) {
  if(!report) return null;
  return <section className="solver-status" aria-label="Статус CP-SAT"><h3>CP-SAT · {report.status}</h3><p>{labels[report.status] || report.status}</p>
    {report.absolute_gap_bound!==null && <p>Верхняя граница разрыва до оптимума исходной F: {bound(report.absolute_gap_bound)}.</p>}
    <p className="hint">Оценка масштабирована в целые числа. Погрешность F одного маршрута — не более {bound(report.objective_error_bound)}. Статус OPTIMAL относится к этой целочисленной модели.</p>
    <details><summary>Параметры и границы поиска</summary><p>Бюджет времени: {number(report.time_limit_seconds)} с, после подготовки на решатель осталось {number(report.search_time_limit_seconds)} с. Всего затрачено {number(report.elapsed_seconds)} с.</p>
      <p>Решатель: OR-Tools {report.version}, потоков: {report.workers}, seed: {report.seed}. Масштаб: {report.scale}. {report.warm_start?"Передан допустимый Greedy-план как стартовая подсказка.":"Допустимой стартовой подсказки не было."}</p>
      {report.original_upper_bound!==null && <p>Верхняя граница исходной F: {bound(report.original_upper_bound)}.</p>}
      <p>Доказательство относится к сохранённым данным и ограничениям этой попытки. Для альтернатив поиск может включать дополнительные исключения мест ради разнообразия. Дополнительные правила разрешения равенств отдельно не оптимизировались.</p>
    </details>
  </section>;
}
