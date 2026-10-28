import { money, type DataContext, type Member, type Visit } from "../../lib/api";
import { visitPrice } from "../../lib/visit-prices";

export function VisitPrice({visit,context,participantCount,members}:{visit:Visit;context?:DataContext|null;participantCount:number;members?:Member[]}) {
  const price=visitPrice(visit,context,participantCount,members?.map(m=>String(m.user_id)));
  const food=visit.place_id.startsWith("meal:") || context?.pois?.some(p=>p.id===visit.place_id && p.category==="food");
  const poi=context?.pois?.find(p=>p.id===visit.place_id);
  if (!price) return <p className="visit-price">Стоимость остановки не сохранена</p>;
  const names=Object.fromEntries((members || []).map(m=>[String(m.user_id),m.name]));
  return <div className="visit-price"><strong>{poi?.price_status==="user_estimate"?`Оценка: ${money(price.group)}${participantCount>1?" на группу":""}`:price.group===0?"Бесплатно":`${money(price.group)}${participantCount>1?(food?" за заказы группы":" за группу"):(food?" за заказ":" за посещение")}`}</strong>
    {poi?.price_status==="user_estimate" && <small>Ваша оценка расходов, не цена заказа из меню.</small>}
    {poi?.price_status==="reference_estimate" && <small>Справочная цена; на будущую дату не подтверждена.</small>}
    {food && poi?.website_url && <small><a href={poi.website_url} target="_blank" rel="noreferrer">Сайт заведения ↗</a>{poi.menu_url && <> · <a href={poi.menu_url} target="_blank" rel="noreferrer">Проверить меню ↗</a></>}</small>}
    {participantCount>1 && price.perPerson!==null && <small>{money(price.perPerson)} / человек × {participantCount}</small>}
    {price.individuals && <details open={price.perPerson===null}><summary>По участникам · {price.individuals.length}</summary>{price.individuals.map(row=><div className="individual-price" key={row.participant_id}>
      <span>{names[row.participant_id] || `Участник ${row.participant_id}`} · {row.age===null?"возраст не указан":`${row.age} лет`}<small>{row.label}</small></span><b>{money(row.cost)}</b>
    </div>)}</details>}
  </div>;
}
