import { money, type LodgingSnapshot, type Member } from "../../lib/api";

export function LodgingSummary({value,members}:{value?:LodgingSnapshot|null;members:Member[]}) {
  if(!value)return null;
  const names=Object.fromEntries(members.map(m=>[String(m.user_id),m.name]));
  return <section className="lodging-summary" aria-label="Сохранённое проживание"><h3>Проживание · {value.property.name}</h3>
    <p>{value.selection.checkin} — {value.selection.checkout} · {value.nights} ночей · номеров: {value.selection.rooms}</p>
    <p><b>Общая оценка на группу: {value.group_total_estimate===null?"не указана":money(value.group_total_estimate)}</b></p>
    <p>Цена введена пользователем за все ночи и номера. Она уже учтена в разбивке расходов, если указана. Наличие, итоговая цена поставщика, вместимость и детские условия не подтверждены. Бронирование не оформлено.</p>
    <details><summary>Участники и возраст на дату заезда</summary>{value.guests.map(g=><p key={g.participant_id}>{names[g.participant_id] || `Участник ${g.participant_id}`}: {g.age_at_checkin===null?"возраст не указан":`${g.age_at_checkin} лет`}</p>)}<p>Возраст сохранён без назначения детских скидок. Проверьте вместимость и правила размещения у объекта; неизвестный возраст не считается взрослым автоматически.</p></details>
    <div className="lodging-links">{value.property.website_url && <a href={value.property.website_url} target="_blank" rel="noreferrer">Сайт из OSM ↗</a>}<a href={value.property.source_url} target="_blank" rel="noreferrer">Источник на карте ↗</a></div>
    <p className="hint">Это сохранённый выбор из каталога, а не подтверждение брони. Маршрут начинается в центральной точке города; переезд от/к отелю и время заселения не моделируются. Снимок источника: {new Date(value.source_upstream_asof).toLocaleDateString("ru-RU")}. {value.attribution}.</p>
  </section>;
}
