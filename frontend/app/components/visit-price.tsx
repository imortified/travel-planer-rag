import { money, type DataContext, type Visit } from "../../lib/api";
import { visitPrice } from "../../lib/visit-prices";

export function VisitPrice({visit,context,participantCount}:{visit:Visit;context?:DataContext|null;participantCount:number}) {
  const price=visitPrice(visit,context,participantCount);
  if (!price) return <p className="visit-price">Стоимость посещения не сохранена</p>;
  if (price.group===0) return <p className="visit-price"><strong>Бесплатно</strong></p>;
  return <p className="visit-price"><strong>{money(price.group)}{participantCount>1?" за группу":" за посещение"}</strong>
    {participantCount>1 && <small>{money(price.perPerson)} / человек × {participantCount}</small>}
  </p>;
}
