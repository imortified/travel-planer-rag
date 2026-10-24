"use client";

import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import { clock, type Route, type Trip } from "../../lib/api";
import { routeMapDays, visibleDays } from "../../lib/route-map";

const MapCanvas=dynamic(()=>import("./route-map-canvas"),{ssr:false,loading:()=> <p role="status" className="hint">Загружаем карту…</p>});

export function RouteMap({route,trip,names}:{route:Route;trip:Trip;names:Record<string,string>}) {
  const days=useMemo(()=>routeMapDays(route,trip,names),[route,trip,names]);
  const [selected,setSelected]=useState<number|null>(null);
  const [active,setActive]=useState<{key:string}|null>(null);
  const [basemap,setBasemap]=useState(false);
  const visible=useMemo(()=>visibleDays(days,selected),[days,selected]);
  const stops=visible.flatMap(d=>d.stops);
  const mapped=stops.filter(s=>s.coordinates);
  if(!route.result.is_feasible) return null;
  return <section className="route-map" aria-label="Карта и порядок посещений">
    <div className="map-heading"><h3>{mapped.length ? "Карта маршрута":"Порядок посещений"}</h3><span className="badge">{stops.length} посещений</span></div>
    <div className="map-days" role="group" aria-label="Дни на карте">
      <button type="button" aria-pressed={selected===null} onClick={()=>{setSelected(null);setActive(null);}}>Все дни</button>
      {days.map(d=><button type="button" key={d.day} aria-pressed={selected===d.day} onClick={()=>{setSelected(d.day);setActive(null);}}><i style={{background:d.color}} aria-hidden="true"/>День {d.day}</button>)}
    </div>
    {mapped.length ? <>
      <MapCanvas key={`${route.id}:${selected}`} days={visible} active={active} onSelect={key=>setActive({key})} basemap={basemap}/>
      <button className="text-button map-basemap" type="button" aria-pressed={basemap} onClick={()=>setBasemap(v=>!v)}>{basemap?"Скрыть подложку OpenStreetMap":"Показать подложку OpenStreetMap"}</button>
      <p className="hint">Пунктир показывает порядок посещений между местами. Это не дорога и не навигация. Время переездов берётся из сохранённого расчёта. Начало каждого дня — указанная точка старта; обратный путь не включён.</p>
      {!basemap && <p className="hint">Подложка выключена. При включении загружаются только видимые фрагменты OpenStreetMap.</p>}
    </>:<p className="hint" role="status">{trip.destination==="Demo City"?"Демонстрационные места не имеют реальных координат. Показан порядок посещений.":"В выбранных днях нет посещений с сохранёнными координатами. Расписание доступно ниже."}</p>}
    {mapped.length>0 && mapped.length<stops.length && <p className="hint" role="status">Без координат: {stops.length-mapped.length}. Эти места есть в списке; пунктир через пропущенные точки не проводится.</p>}
    <div className="map-stop-list" aria-label="Места по дням">{visible.map(d=><div key={d.day}><p><i style={{background:d.color}} aria-hidden="true"/><b>День {d.day}</b>{d.date && <span> · {d.date}</span>}</p>
      {!d.stops.length && <p className="hint">Посещений на этот день нет.</p>}
      {d.stops.map(s=><button key={s.key} type="button" disabled={!s.coordinates} aria-pressed={active?.key===s.key} onClick={()=>setActive({key:s.key})}><span className="map-stop-number" style={{background:d.color}}>{s.order}</span><span><b>{s.name}</b><small>{clock(s.start)}–{clock(s.end)}{!s.coordinates?" · без координат":""}</small></span></button>)}
    </div>)}</div>
  </section>;
}
