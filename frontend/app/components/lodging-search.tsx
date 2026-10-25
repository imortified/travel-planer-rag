import { useRef, useState } from "react";
import { api, type LodgingDirectory, type LodgingProperty, type LodgingSelection } from "../../lib/api";
import { lodgingKinds, lodgingNights, lodgingSelection, nextDate } from "../../lib/lodging";

export type SelectedLodging={plan:LodgingSelection;property:LodgingProperty};

export function LodgingSearch({destination,first,last,guestCount,value,disabled,onChange}:{destination:string;first:string;last:string;guestCount:number;value?:SelectedLodging;disabled:boolean;onChange:(v:SelectedLodging|undefined)=>void}) {
  const [query,setQuery]=useState(""),[kind,setKind]=useState("");
  const [checkin,setCheckin]=useState(first),[checkout,setCheckout]=useState(last>first?last:nextDate(first)),[rooms,setRooms]=useState("1");
  const [directory,setDirectory]=useState<LodgingDirectory>(),[error,setError]=useState(""),[loading,setLoading]=useState(false);
  const searched=useRef({query:"",kind:""});
  const nights=lodgingNights(checkin,checkout);
  async function search(page=1) {
    if(loading)return;
    setLoading(true);setError("");
    const criteria=page===1?{query,kind}:searched.current;
    try {
      const result=await api<LodgingDirectory>(`/destinations/${encodeURIComponent(destination)}/lodgings?q=${encodeURIComponent(criteria.query)}${criteria.kind?`&kind=${encodeURIComponent(criteria.kind)}`:""}&page=${page}&page_size=10`);
      setDirectory(result);searched.current=criteria;
    } catch(e){setError(e instanceof Error?e.message:"Не удалось загрузить каталог проживания");}
    finally{setLoading(false);}
  }
  function choose(property:LodgingProperty) {
    try {const plan=lodgingSelection(property.id,directory!.catalog_version_id,checkin,checkout,rooms,first,last);onChange({plan,property});setError("");}
    catch(e){setError(e instanceof Error?e.message:"Проверьте параметры проживания");}
  }
  const changed=()=>{if(value)onChange(undefined);};
  if(destination==="Demo City")return null;
  return <section className="lodging-search" aria-label="Выбор проживания"><h3>Где остановимся</h3>
    <p className="hint">Каталог реальных объектов OpenStreetMap. Наличие номеров, вместимость, детские условия и цены не проверены. На сайте объекта можно проверить условия и забронировать; после этого укажите общую оценку жилья в расходах.</p>
    <div className="two"><label>Заезд<input type="date" value={checkin} disabled={disabled} onChange={e=>{setCheckin(e.target.value);changed();}}/></label><label>Выезд<input type="date" value={checkout} disabled={disabled} onChange={e=>{setCheckout(e.target.value);changed();}}/></label></div>
    <label>Номеров на всю группу<input type="number" min="1" max="6" step="1" value={rooms} disabled={disabled} onChange={e=>{setRooms(e.target.value);changed();}}/></label>
    <p className="hint">Участников: {guestCount} · {nights===null?"проверьте даты":`ночей: ${nights}`}. Дата выезда — день окончания проживания. Время заселения/выселения и хранение багажа не учитываются. Изменение дат, номеров или состава требует повторного выбора объекта и проверки общей цены.</p>
    <label>Название или адрес проживания<input value={query} maxLength={100} disabled={disabled || loading} onChange={e=>setQuery(e.target.value)} placeholder="Название отеля или улица"/></label>
    <label>Тип проживания<select value={kind} disabled={disabled || loading} onChange={e=>setKind(e.target.value)}><option value="">Все типы</option>{Object.entries(lodgingKinds).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
    <button type="button" className="secondary" disabled={disabled || loading || !first} onClick={()=>search()}>{loading?"Загружаем проживание…":"Найти объекты проживания"}</button>
    {!first && <p className="hint">Сначала задайте даты поездки.</p>}
    {error && <p className="message error" role="alert">{error}</p>}
    {value && <div className="lodging-picked"><b>Выбрано: {value.property.name}</b><p>{value.plan.checkin} — {value.plan.checkout} · номеров: {value.plan.rooms}</p><p>Включён бюджет всей поездки. Введите общую сумму жилья за все ночи и номера; пустая цена останется неизвестной. Объект ещё не забронирован. Старт маршрута остаётся в центральной точке города.</p><button type="button" className="text-button" disabled={disabled} onClick={()=>onChange(undefined)}>Убрать выбранное проживание</button></div>}
    {directory && <><p className="hint" aria-live="polite">Найдено объектов: {directory.total}. Снимок OSM: {new Date(directory.source_upstream_asof).toLocaleDateString("ru-RU")}. Поиск по названиям и адресам; фильтрации по наличию и цене нет.</p>
      <div className="lodging-results">{directory.properties.map(property=><article className="lodging-item" key={property.id}><h4>{property.name}</h4><p>{lodgingKinds[property.kind] || property.kind}{property.address?` · ${property.address}`:""}</p><p>Цена неизвестна · наличие и размещение группы требуют проверки</p><div className="lodging-links">{property.website_url && <a href={property.website_url} target="_blank" rel="noreferrer">Сайт из OSM ↗</a>}<a href={property.source_url} target="_blank" rel="noreferrer">Источник на карте ↗</a></div><button type="button" className="secondary" disabled={disabled || !nights} onClick={()=>choose(property)}>{value?.property.id===property.id?"Выбрано ✓":"Выбрать для поездки"}</button></article>)}</div>
      {!directory.total && <p>Совпадений в каталоге нет. Попробуйте другое название или тип.</p>}
      <div className="lodging-pages"><button type="button" className="text-button" disabled={disabled || loading || directory.page<=1} onClick={()=>search(directory.page-1)}>← Назад</button><span>Страница {directory.page}</span><button type="button" className="text-button" disabled={disabled || loading || directory.page*directory.page_size>=directory.total} onClick={()=>search(directory.page+1)}>Далее →</button></div>
      <p className="hint">{directory.attribution}. Каталог включает отмеченные узлы и контуры в выбранной области города, может содержать повторы и устаревшие записи. Центр контура не является подтверждённым входом.</p>
    </>}
  </section>;
}
