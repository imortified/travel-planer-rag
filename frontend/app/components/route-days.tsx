import { clock, money, type Route, type Trip } from "../../lib/api";

export function RouteDays({ route, trip, names }: {route: Route; trip: Trip; names: Record<string,string>}) {
  const days = route.result.days || [{travel_date: trip.travel_date || "", total_cost: route.result.total_cost, visits: route.result.visits}];
  return <div>{days.map((day,index)=><section className="day-plan" key={`${day.travel_date}:${index}`}>
    <h3 className="day-heading">День {index+1}{day.travel_date ? ` · ${day.travel_date}` : ""}<span>{money(day.total_cost)}</span></h3>
    {!day.visits.length && <p className="hint">Посещений нет: не осталось подходящих мест с учётом расписания, бюджета и предыдущих дней.</p>}
    <ol className="timeline">{day.visits.map((v,i)=><li key={`${v.place_id}:${i}`}>
      <div className="timeline-time">{clock(v.start)}<small>{clock(v.end)}</small></div>
      <div><span className="stop-number">{String(i+1).padStart(2,"0")}</span><h3>{names[v.place_id] || v.place_id}</h3>
        <p>Прибытие {clock(v.arrival)}{v.start>v.arrival?` · ожидание ${v.start-v.arrival} мин`:""}</p><small>Посещение {v.end-v.start} мин</small></div>
    </li>)}</ol>
  </section>)}</div>;
}
