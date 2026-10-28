import { useEffect,useId,useState } from "react";
import { api } from "../../lib/api";
import { flightPlaceLabel,type FlightPlace } from "../../lib/flights";

export function FlightOrigin({disabled,onChange}:{disabled:boolean;onChange:(code:string)=>void}) {
  const id=useId(),[query,setQuery]=useState(""),[manual,setManual]=useState(false),[selected,setSelected]=useState<FlightPlace>();
  const [places,setPlaces]=useState<FlightPlace[]>([]),[loading,setLoading]=useState(false),[error,setError]=useState(""),[active,setActive]=useState(-1),[open,setOpen]=useState(false);
  const term=query.trim();
  useEffect(()=>{
    if(manual||selected||disabled||term.length<2){setLoading(false);return;}
    let cancelled=false;
    const timer=setTimeout(async()=>{
      setLoading(true);setError("");
      try {
        const result=await api<{places:FlightPlace[]}>(`/flight-places?term=${encodeURIComponent(term)}`);
        if(!cancelled){setPlaces(result.places);setActive(-1);}
      } catch(e){if(!cancelled)setError(e instanceof Error?e.message:"Подсказки недоступны");}
      finally{if(!cancelled)setLoading(false);}
    },350);
    return ()=>{cancelled=true;clearTimeout(timer);};
  },[term,manual,selected,disabled]);
  function choose(place:FlightPlace){setSelected(place);setQuery(flightPlaceLabel(place));setPlaces([]);setOpen(false);setActive(-1);onChange(place.code);}
  return <div className="flight-origin">
    <label htmlFor={`${id}-input`}>{manual?"IATA-код города или аэропорта вылета":"Город или аэропорт вылета"}</label>
    <input id={`${id}-input`} value={query} disabled={disabled} maxLength={manual?3:80} placeholder={manual?"Например, MOW или MSQ":"Например, Москва, Минск или Варшава"}
      autoComplete="off" role={manual?undefined:"combobox"} aria-autocomplete={manual?undefined:"list"} aria-expanded={manual?undefined:open&&!selected&&term.length>=2}
      aria-controls={manual?undefined:`${id}-list`} aria-activedescendant={!manual&&open&&active>=0?`${id}-option-${active}`:undefined}
      onFocus={()=>setOpen(true)} onBlur={e=>{if(!e.currentTarget.parentElement?.contains(e.relatedTarget as Node))setOpen(false);}}
      onChange={e=>{const text=manual?e.target.value.toUpperCase():e.target.value;setQuery(text);setSelected(undefined);setPlaces([]);setError("");setActive(-1);setOpen(true);onChange(manual?text:"");}}
      onKeyDown={e=>{
        if(manual)return;
        if(e.key==="Escape"){setOpen(false);setActive(-1);}
        if(e.key==="ArrowDown"||e.key==="ArrowUp"){
          e.preventDefault();setOpen(true);if(places.length)setActive(old=>e.key==="ArrowDown"?(old+1)%places.length:(old<=0?places.length-1:old-1));
        }
        if(e.key==="Enter"&&open&&active>=0&&places[active]){e.preventDefault();choose(places[active]);}
      }}/>
    {!manual&&open&&!selected&&term.length>=2&&<div className="flight-place-options" id={`${id}-list`} role="listbox" aria-label="Города и аэропорты">
      {places.map((place,index)=><button type="button" role="option" aria-selected={index===active} id={`${id}-option-${index}`} key={`${place.type}:${place.code}`} disabled={disabled}
        onClick={()=>choose(place)}><b>{flightPlaceLabel(place)}</b><small>{place.country_name} · {place.type==="city"?"Поиск по аэропортам города":"Конкретный аэропорт"}</small></button>)}
    </div>}
    {!manual&&<p className="hint" role="status">{error|| (loading?"Ищем города и аэропорты…":selected?`Выбрано: ${flightPlaceLabel(selected)}.`:term.length<2?"Введите хотя бы две буквы и выберите подсказку.":places.length?"Выберите город для поиска по всем его аэропортам или отдельный аэропорт.":"Если подсказки не появились, уточните название или введите код вручную.")}</p>}
    <button type="button" className="text-button" disabled={disabled} onClick={()=>{setManual(!manual);setQuery("");setSelected(undefined);setPlaces([]);setError("");setOpen(false);setActive(-1);onChange("");}}>{manual?"Выбрать город по названию":"Ввести IATA-код вручную"}</button>
  </div>;
}
