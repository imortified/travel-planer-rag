import { money, type Poi } from "../../lib/api";
import { poiPriceRange } from "../../lib/visit-prices";

const categories: Record<string,string> = {history:"История",nature:"Природа",art:"Искусство",food:"Еда"};

export function PoiFacts({poi, sources=false}: {poi: Poi; sources?: boolean}) {
  const range=poiPriceRange(poi);
  const varies=range && range.min!==range.max;
  return <div className="poi-facts">
    <small>{range ? `${money(range.min)}${varies?`–${money(range.max)}`:""} / человек` : "Цена не указана"}
      {poi.category ? ` · ${categories[poi.category] || poi.category}` : ""}
      {poi.duration ? ` · ${poi.duration} мин` : ""}</small>
    {varies && <small>Цена зависит от даты; точная сумма указана у посещения в маршруте.</small>}
    {poi.provenance && <small>Проверено {poi.provenance.checked_on} · для поездок до {poi.provenance.valid_until}</small>}
    {poi.category==="food" && poi.provenance?.price_policy && <small>{poi.provenance.price_policy}</small>}
    {sources && poi.provenance && <details><summary>Условия посещения и стоимости</summary><p>{poi.provenance.notes}</p></details>}
    {sources && poi.provenance && <small><a href={poi.provenance.price_source} target="_blank" rel="noreferrer">Источник цены ↗</a>{" · "}<a href={poi.provenance.hours_source} target="_blank" rel="noreferrer">Часы работы ↗</a></small>}
  </div>;
}
