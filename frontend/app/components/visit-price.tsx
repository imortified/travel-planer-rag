import { money, type DataContext, type Visit } from "../../lib/api";
import { visitPrice } from "../../lib/visit-prices";

export function VisitPrice({visit,context,participantCount}:{visit:Visit;context?:DataContext|null;participantCount:number}) {
  const price=visitPrice(visit,context,participantCount);
  const food=visit.place_id.startsWith("meal:") || context?.pois?.some(p=>p.id===visit.place_id && p.category==="food");
  if (!price) return <p className="visit-price">Стоимость остановки не сохранена</p>;
  if (price.group===0) return <p className="visit-price"><strong>Бесплатно</strong></p>;
  return <p className="visit-price"><strong>{money(price.group)}{participantCount>1?(food?" за заказы группы":" за группу"):(food?" за заказ":" за посещение")}</strong>
    {participantCount>1 && <small>{money(price.perPerson)} / человек × {participantCount}</small>}
  </p>;
}
