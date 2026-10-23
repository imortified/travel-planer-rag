import { money, type Route, type RouteSet, type Trip } from "../../lib/api";
import { VisaReport } from "./visa-report";
import { SolverStatus } from "./solver-status";

const points = (value: number) => value.toLocaleString("ru-RU", {maximumFractionDigits:2});

export function RouteComparison({data, trip, selectedId, names, reasonLabels, disabled, onSelect}: {
  data: RouteSet; trip: Trip; selectedId?: number; names: Record<string,string>;
  reasonLabels: Record<string,string>;
  disabled: boolean; onSelect: (route: Route) => void;
}) {
  const allNames = {...names};
  data.options.forEach(r=>r.result.data_context?.pois.forEach(p=>{allNames[p.id]=p.name;}));
  const mainIds = new Set(data.options[0]?.result.visits.map(v=>v.place_id) || []);
  const rows: {label: string; value: (route: Route)=>string}[] = [
    ...(data.options.some(r=>r.result.objective)?[
      {label:"Общая оценка F ↑",value:(r:Route)=>r.result.objective?r.result.objective.value.toLocaleString("ru-RU",{maximumFractionDigits:4}):"—"},
      {label:"Переезды, мин",value:(r:Route)=>r.result.objective?String(r.result.objective.travel_minutes):"—"},
      {label:"Разрыв покрытия интересов ↓",value:(r:Route)=>r.result.objective?r.result.objective.fairness_gap.toLocaleString("ru-RU",{maximumFractionDigits:4}):"—"},
    ]:[]),
    {label:"Билеты на всю группу",value:r=>money(r.result.total_cost)},
    {label:"Посещения",value:r=>String(r.comparison!.visit_count)},
    {label:"Средняя полезность, баллы",value:r=>points(r.comparison!.mean_utility)},
    {label:"Минимум у участника, баллы",value:r=>points(r.comparison!.min_utility)},
    {label:"Разрыв интересов, баллы ↓",value:r=>points(r.comparison!.utility_gap)},
    ...trip.participants_snapshot.map(m=>({label:`${m.name}, баллы`,value:(r: Route)=>points(r.comparison!.participant_utility[String(m.user_id)] || 0)})),
  ];
  return <section className="card comparison" aria-label="Сравнение маршрутов">
    <div className="comparison-heading"><h3>Сравнение маршрутов</h3><span className="badge">Расчёт №{data.id}</span></div>
    <p className="hint">{trip.destination==="Lisbon"?"Лиссабон":"Демо-город"} · {trip.travel_date || "Без даты"}{trip.end_date && trip.end_date!==trip.travel_date ? ` — ${trip.end_date}` : ""} · бюджет {money(trip.budget)}</p>
    {!data.options.length ? <div role="status" className="message error">{data.diagnostic?.solver?.status==="INFEASIBLE"?"При заданных данных и ограничениях допустимого маршрута нет: CP-SAT доказал невозможность.":data.diagnostic?.solver?.status==="UNKNOWN"?"За доступное время CP-SAT не нашёл допустимого маршрута. Это не доказывает невозможность; попробуйте увеличить лимит.":"Допустимых маршрутов не найдено. Проверьте бюджет, время и обязательные места. Ограниченный поиск может пропустить существующий вариант."}
      {data.diagnostic && <ul>{[...new Set(data.diagnostic.reasons.map(r=>r.split(":")[0]))].map(code=><li key={code}>{reasonLabels[code] || code}</li>)}</ul>}
    </div> : <>
      <p className="hint">Выберите вариант, чтобы увидеть расписание ниже. Все показанные варианты прошли проверку бюджета и расписания. Визовые условия — в отдельном отчёте.</p>
      <div className="comparison-scroll" role="region" aria-label="Таблица сравнения вариантов" tabIndex={0}><table>
        <caption className="sr-only">Стоимость, полезность и баланс интересов для одного запроса</caption>
        <thead><tr><th scope="col">Показатель</th>{data.options.map((r,i)=><th scope="col" key={r.id}><button className="option-button" disabled={disabled} aria-label={i===0?"Основной маршрут":`Альтернатива ${i}`} aria-pressed={r.id===selectedId} onClick={()=>onSelect(r)}>{i===0?"Основной":`Вариант ${i+1}`}<small>{r.id===selectedId?"Выбран ✓":"Показать →"}</small></button></th>)}</tr></thead>
        <tbody>{rows.map(row=><tr key={row.label}><th scope="row">{row.label}</th>{data.options.map(r=><td key={r.id}>{row.value(r)}</td>)}</tr>)}
          <tr><th scope="row">Отличия по местам</th>{data.options.map((r,i)=>{
            if (i===0) return <td key={r.id}>Исходный вариант</td>;
            const ids=new Set(r.result.visits.map(v=>v.place_id));
            const added=[...ids].filter(id=>!mainIds.has(id));
            const removed=[...mainIds].filter(id=>!ids.has(id));
            return <td key={r.id}><details><summary>Посмотреть отличия</summary>{!!added.length && <p>Добавлены: {added.map(id=>allNames[id]||id).join(", ")}</p>}{!!removed.length && <p>Не включены: {removed.map(id=>allNames[id]||id).join(", ")}</p>}</details></td>;
          })}</tr>
        </tbody>
      </table></div>
      <p className="hint">Полезность — сумма интересов к посещениям, в среднем по участникам. Разрыв в баллах — разница между наибольшей и наименьшей суммой. {data.options[0]?.result.objective?"Основной вариант выбран по общей оценке F: интересы с учётом стоимости, переездов и неравенства покрытия интересов. Формула приведена ниже.":"Основной вариант выбран по прежнему порядку полезности и стоимости."} Поиск ограничен; оптимальность не гарантируется.</p>
    </>}
    {data.search_info.note_codes.includes("fewer_distinct_options_found") && <p className="hint" role="status">Найдено вариантов: {data.options.length} из 3. Поиск ограничен; при текущих условиях больше содержательно разных вариантов не найдено. Перестановки тех же мест не считаются альтернативой.</p>}
    {data.search_info.note_codes.includes("cp_sat_time_budget_exhausted") && <p className="hint" role="status">Бюджет времени CP-SAT исчерпан. Показаны найденные варианты; дополнительные альтернативы могли остаться ненайденными.</p>}
    {!data.options.length && <SolverStatus report={data.diagnostic?.solver}/>}
    {!data.options.length && trip.destination==="Lisbon" && <VisaReport report={data.visa_report}/>}
  </section>;
}
