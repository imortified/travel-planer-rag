import { useState } from "react";
import { api,money } from "../../lib/api";
import { airportFor,departureLabel,pickFlight,validateFlightSearch,type FlightObservation,type FlightOption,type PickedFlight } from "../../lib/flights";

export function FlightSearch({destination,first,last,disabled,ensureGroup,value,onBusy,onChoose}:{destination:string;first:string;last:string;disabled:boolean;ensureGroup:()=>Promise<number>;value?:PickedFlight;onBusy:(value:string)=>void;onChoose:(value:PickedFlight|undefined)=>void}) {
  const [origin,setOrigin]=useState(""),[departure,setDeparture]=useState(first),[returning,setReturning]=useState(last>first),[back,setBack]=useState(last),[direct,setDirect]=useState(false);
  const [market,setMarket]=useState<"ru"|"us">("ru"),[result,setResult]=useState<FlightObservation>(),[error,setError]=useState("");
  const invalidate=()=>{setResult(undefined);onChoose(undefined);setError("");};
  async function search(){
    const input={destination,origin:origin.trim().toUpperCase(),departure,return_date:returning?back:null,market,direct};
    const issue=validateFlightSearch(input,first,last);if(issue){setError(issue);return;}
    onBusy("Ищем перелёты…");invalidate();
    try {const gid=await ensureGroup();setResult(await api<FlightObservation>(`/groups/${gid}/flight-searches`,"POST",input));}
    catch(e){setError(e instanceof Error?e.message:"Поиск перелётов недоступен");}
    finally{onBusy("");}
  }
  function choose(option:FlightOption){
    try {onChoose(pickFlight(result!,option));setError("");}
    catch(e){setError(e instanceof Error?e.message:"Повторите поиск");}
  }
  if(destination==="Demo City")return null;
  return <section className="flight-search" aria-label="Поиск перелётов"><h3>Как доберёмся · перелёты</h3>
    <p className="hint">Aviasales Data API показывает цены из предыдущих поисков. Они не подтверждают наличие билетов и стоимость для вашей группы. При покупке нужно проверить пассажиров, багаж и условия тарифа.</p>
    <div className="two"><label>IATA-код аэропорта вылета<input maxLength={3} placeholder="Например, WAW" value={origin} disabled={disabled} onChange={e=>{setOrigin(e.target.value.toUpperCase());invalidate();}}/></label><label>Аэропорт назначения<input value={airportFor(destination)} readOnly/></label></div>
    {destination==="Sintra"&&<p className="hint">Для Синтры ищем перелёт в Лиссабон (LIS). Трансфер до Синтры укажите в наземном транспорте.</p>}
    <label>Дата вылета<input type="date" value={departure} disabled={disabled} onChange={e=>{setDeparture(e.target.value);invalidate();}}/></label>
    <label className="cost-toggle"><input type="checkbox" checked={returning} disabled={disabled} onChange={e=>{setReturning(e.target.checked);invalidate();}}/> Туда и обратно</label>
    {returning&&<label>Дата обратного вылета<input type="date" value={back} disabled={disabled} onChange={e=>{setBack(e.target.value);invalidate();}}/></label>}
    <label className="cost-toggle"><input type="checkbox" checked={direct} disabled={disabled} onChange={e=>{setDirect(e.target.checked);invalidate();}}/> Только без пересадок</label>
    <label>Регион выдачи Aviasales<select value={market} disabled={disabled} onChange={e=>{setMarket(e.target.value as "ru"|"us");invalidate();}}><option value="ru">Россия (RU)</option><option value="us">США (US)</option></select></label>
    <button type="button" className="secondary" disabled={disabled||!first} onClick={search}>Найти перелёты</button>
    {error&&<p className="message error" role="alert">{error}</p>}
    {result&&<><p className="hint">Получено из API {new Date(result.fetched_at).toLocaleString("ru-RU")} · срок выбора до {new Date(result.expires_at).toLocaleString("ru-RU")}. Дата первоначального нахождения цены неизвестна. Состав группы не передаётся в этот метод.</p>
      {result.status==="empty"?<p className="message">В кэше нет подходящих записей. Это не означает, что рейсов нет. Попробуйте другие даты, аэропорт или рынок; общую оценку перелётов можно указать вручную.</p>:<div className="lodging-results">{result.options.map(option=><article className="lodging-item" key={option.option_id}>
        <h4>{option.origin_airport} → {option.destination_airport} · {option.airline} {option.flight_number}</h4>
        <p><b>{money(option.observed_price)} · наблюдение цены {option.return_at?"туда-обратно":"в одну сторону"}</b></p>
        <p>Вылет: {departureLabel(option.departure_at)}. Пересадок: {option.transfers}.</p>
        {option.return_at&&<p>Обратный вылет: {departureLabel(option.return_at)}. Пересадок обратно: {option.return_transfers}.</p>}
        <p className="hint">Смещение часового пояса указано в дате. Полные сегменты и прибытие не подтверждены; время посещений не меняется. Цена всей группы и детские тарифы не рассчитаны.</p>
        {option.search_url&&<a href={option.search_url} target="_blank" rel="noreferrer">Перепроверить цену на Aviasales ↗</a>}
        <button type="button" className="secondary" disabled={disabled} onClick={()=>choose(option)}>Выбрать перелёт</button>
      </article>)}</div>}</>}
    {value&&<div className="lodging-picked"><b>Выбран перелёт {value.option.origin_airport} → {value.option.destination_airport}</b><p>Укажите общую сумму всех билетов в поле «Перелёты на всю группу». Цена из кэша автоматически не переносится. Наземный транспорт учитывается отдельно.</p><button type="button" className="text-button" disabled={disabled} onClick={()=>onChoose(undefined)}>Убрать выбранный перелёт</button></div>}
  </section>;
}
