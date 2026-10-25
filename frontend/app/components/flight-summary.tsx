import { money,type Member } from "../../lib/api";
import { departureLabel,type FlightSnapshot } from "../../lib/flights";

export function FlightSummary({value,members}:{value?:FlightSnapshot|null;members:Member[]}) {
  if(!value)return null;
  const flight=value.option,names=Object.fromEntries(members.map(m=>[String(m.user_id),m.name]));
  return <section className="flight-summary" aria-label="Сохранённый перелёт"><h3>Перелёт · {flight.origin_airport} → {flight.destination_airport}</h3>
    <p>{flight.airline} {flight.flight_number} · {flight.return_at?"туда и обратно":"в одну сторону"}</p>
    <p>Вылет: {departureLabel(flight.departure_at)} · пересадок: {flight.transfers}.</p>
    {flight.return_at&&<p>Обратный вылет: {departureLabel(flight.return_at)} · пересадок обратно: {flight.return_transfers}.</p>}
    <p>Справочная цена из кэша Aviasales: <b>{money(flight.observed_price)}</b>. Это не подтверждённая стоимость билетов для группы.</p>
    <p><b>Ваша общая оценка перелётов: {value.group_total_estimate===null?"не указана":money(value.group_total_estimate)}</b>. {value.group_total_estimate===null?"Стоимость перелётов ещё нужно добавить; цена из кэша её не заменяет.":"Она учтена один раз в разбивке расходов, отдельно от наземного транспорта."}</p>
    <p className="hint">Источник: Aviasales Data API. Получено из API {new Date(value.fetched_at).toLocaleString("ru-RU")}; дата первоначального нахождения цены неизвестна. Это сохранённое наблюдение, цена могла измениться. Наличие билетов, багаж и детские тарифы не подтверждены. Бронирование не оформлено.</p>
    <p className="hint">Полных сегментов и проверенного времени прибытия нет. Расписание посещений и трансферы не рассчитаны по перелёту; проверьте первый и последний день вручную.</p>
    <details><summary>Участники при выборе перелёта</summary>{value.passengers.map(p=><p key={p.participant_id}>{names[p.participant_id]||`Участник ${p.participant_id}`}: {p.age_at_departure===null?"возраст неизвестен":`${p.age_at_departure} лет на дату вылета`}</p>)}</details>
    {flight.search_url&&<a href={flight.search_url} target="_blank" rel="noreferrer">Перепроверить на Aviasales ↗</a>}
  </section>;
}
