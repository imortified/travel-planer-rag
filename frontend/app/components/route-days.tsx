import { clock, money, type Route, type Trip } from "../../lib/api";
import { VisitPrice } from "./visit-price";

export function RouteDays({ route, trip, names }: {route: Route; trip: Trip; names: Record<string,string>}) {
  const days = route.result.days || [{travel_date: trip.travel_date || "", total_cost: route.result.total_cost, visits: route.result.visits}];
  return <div>{days.map((day,index)=><section className="day-plan" key={`${day.travel_date}:${index}`}>
    <h3 className="day-heading">День {index+1}{day.travel_date ? ` · ${day.travel_date}` : ""}<span>{money(day.total_cost)}</span></h3>
    {route.result.planning_summary?.days[index] && <p className="hint">Достопримечательностей: {route.result.planning_summary.days[index].attraction_count ?? route.result.planning_summary.days[index].visit_count} · питание {route.result.planning_summary.days[index].meal_count || 0} · нагрузка {route.result.planning_summary.days[index].active_minutes} мин: посещения {route.result.planning_summary.days[index].activity_minutes}, переезды {route.result.planning_summary.days[index].drive_minutes}, перерывы {route.result.planning_summary.days[index].rest_minutes}. Дополнительное ожидание открытия: {route.result.planning_summary.days[index].opening_wait_minutes} мин. Кандидатов в расчёте на дату: {route.result.planning_summary.days[index].eligible_count}.</p>}
    {!day.visits.length && <p className="hint">В выбранном варианте посещений на этот день нет.</p>}
    <ol className="timeline">{day.visits.map((v,i)=><li key={`${v.place_id}:${i}`}>
      <div className="timeline-time">{clock(v.start)}<small>{clock(v.end)}</small></div>
      <div><span className="stop-number">{String(i+1).padStart(2,"0")}</span><h3>{names[v.place_id] || v.place_id}</h3>
        <p>Прибытие {clock(v.arrival)}{v.start>v.arrival?` · до начала ${v.start-v.arrival} мин (перерыв и ожидание)`:""}</p><small>{v.place_id.startsWith("meal:")?"Приём пищи":"Посещение"} {v.end-v.start} мин</small>
        <VisitPrice visit={v} context={route.result.days ? day.data_context : route.result.data_context} participantCount={trip.participants_snapshot.length} members={trip.participants_snapshot}/></div>
    </li>)}</ol>
  </section>)}</div>;
}
