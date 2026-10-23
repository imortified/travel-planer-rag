import { money, type Poi } from "../../lib/api";

const categories: Record<string,string> = {history:"История",nature:"Природа",art:"Искусство",food:"Еда"};

export function PoiFacts({poi, sources=false}: {poi: Poi; sources?: boolean}) {
  return <div className="poi-facts">
    <small>{typeof poi.cost_per_person === "number" ? `${money(poi.cost_per_person)} / человек` : "Цена не указана"}
      {poi.category ? ` · ${categories[poi.category] || poi.category}` : ""}
      {poi.duration ? ` · ${poi.duration} мин` : ""}</small>
    {poi.provenance && <small>Проверено {poi.provenance.checked_on} · для поездок до {poi.provenance.valid_until}</small>}
    {sources && poi.provenance && <small><a href={poi.provenance.price_source} target="_blank" rel="noreferrer">Источник цены ↗</a>{" · "}<a href={poi.provenance.hours_source} target="_blank" rel="noreferrer">Часы работы ↗</a></small>}
  </div>;
}
