import { useState } from "react";
import { api,money,type XoteloList,type XoteloObservation,type XoteloRate } from "../../lib/api";
import { lodgingNights,nextDate } from "../../lib/lodging";
import { baseEstimate,type PickedQuote } from "../../lib/xotelo";

export function XoteloSearch({destination,first,last,agesKnown,disabled,ensureGroup,value,onBusy,onChoose}:{destination:string;first:string;last:string;agesKnown:boolean;disabled:boolean;ensureGroup:()=>Promise<number>;value?:PickedQuote;onBusy:(s:string)=>void;onChoose:(p:PickedQuote|undefined,estimate?:number)=>void}) {
  const [list,setList]=useState<XoteloList>(),[key,setKey]=useState(""),[filter,setFilter]=useState("");
  const [checkin,setCheckin]=useState(first),[checkout,setCheckout]=useState(last>first?last:nextDate(first)),[rooms,setRooms]=useState("1");
  const [quote,setQuote]=useState<XoteloObservation>(),[error,setError]=useState(""),[loading,setLoading]=useState(false);
  const nights=lodgingNights(checkin,checkout);
  function invalidate(){setQuote(undefined);onChoose(undefined);}
  async function hotels(offset=0) {
    setLoading(true);onBusy("Загружаем отели Xotelo…");setError("");invalidate();
    try {setList(await api<XoteloList>(`/destinations/${encodeURIComponent(destination)}/xotelo-hotels?offset=${offset}`));setKey("");setFilter("");}
    catch(e){setError(e instanceof Error?e.message:"Xotelo недоступен");}
    finally{setLoading(false);onBusy("");}
  }
  async function prices(){
    if(!nights||checkin>first||checkout<last||!/^\d+$/.test(rooms)||Number(rooms)<1||Number(rooms)>6){setError("Проверьте покрытие дат поездки и количество номеров.");return;}
    setLoading(true);onBusy("Загружаем цены Xotelo…");setError("");invalidate();
    try {const gid=await ensureGroup();setQuote(await api<XoteloObservation>(`/groups/${gid}/lodging-quotes`,"POST",{destination,hotel_key:key,checkin,checkout,rooms:Number(rooms),list_offset:list!.offset}));}
    catch(e){setError(e instanceof Error?e.message:"Цены недоступны");}
    finally{setLoading(false);onBusy("");}
  }
  function choose(rate:XoteloRate,apply:boolean){
    if(!quote||Date.parse(quote.expires_at)<=Date.now()){setError("Предложение истекло. Загрузите цены снова.");return;}
    const estimate=baseEstimate(rate,nights!,Number(rooms));
    if(apply&&estimate===null)return;
    onChoose({observation:quote,rate,choice:{quote_id:quote.quote_id,rate_code:rate.code,apply_base_estimate:apply}},apply?estimate!:undefined);
  }
  if(destination==="Demo City")return null;
  return <section className="xotelo-search" aria-label="Цены проживания Xotelo"><h3>Проживание · цены Xotelo</h3>
    <p className="hint">Цены площадок на выбранные даты и состав группы. Тариф описан поставщиком как базовая цена за номер за ночь; налоги, сборы и размещение всех участников требуют проверки. Бронирование оформляется на внешнем сайте.</p>
    <div className="two"><label>Заезд для Xotelo<input type="date" value={checkin} disabled={disabled} onChange={e=>{setCheckin(e.target.value);invalidate();}}/></label><label>Выезд для Xotelo<input type="date" value={checkout} disabled={disabled} onChange={e=>{setCheckout(e.target.value);invalidate();}}/></label></div>
    <label>Номеров для Xotelo<input type="number" min="1" max="6" value={rooms} disabled={disabled} onChange={e=>{setRooms(e.target.value);invalidate();}}/></label>
    <button type="button" className="secondary" disabled={disabled||loading||!first} onClick={()=>hotels()}>Загрузить отели Xotelo</button>
    {list&&<><p className="hint">В списке города: {list.total}. Загружены позиции {list.offset+1}–{list.offset+list.hotels.length}. Поиск по названию — в пределах этой страницы.</p>
      <label>Фильтр отелей на странице<input value={filter} disabled={disabled} onChange={e=>setFilter(e.target.value)} placeholder="Название на загруженной странице"/></label>
      <label>Отель Xotelo<select value={key} disabled={disabled} onChange={e=>{setKey(e.target.value);invalidate();}}><option value="">Выберите отель</option>{list.hotels.filter(h=>h.hotel_key===key||h.name.toLocaleLowerCase().includes(filter.toLocaleLowerCase())).map(h=><option key={h.hotel_key} value={h.hotel_key}>{h.name}</option>)}</select></label>
      <div className="lodging-pages"><button type="button" className="text-button" disabled={disabled||list.offset===0} onClick={()=>hotels(Math.max(0,list.offset-list.limit))}>← Предыдущие отели</button><button type="button" className="text-button" disabled={disabled||list.offset+list.limit>=list.total} onClick={()=>hotels(list.offset+list.limit)}>Следующие отели →</button></div>
      {!agesKnown&&<p className="hint">Для запроса цен укажите возраст каждого участника. Ручной каталог ниже доступен без возраста.</p>}
      <button type="button" className="secondary" disabled={disabled||loading||!key||!nights||!agesKnown} onClick={prices}>Получить цены для группы</button>
    </>}
    {error&&<p className="message error" role="alert">{error} Можно использовать ручной каталог ниже.</p>}
    {quote&&<><p className="hint">Получено {new Date(quote.fetched_at).toLocaleString("ru-RU")} · срок выбора до {new Date(quote.expires_at).toLocaleTimeString("ru-RU")} · взрослых: {quote.adults}, возраст детей: {quote.children_ages.join(", ")||"детей нет"}. Наличие и вместимость не гарантированы.</p>
      {quote.status==="empty"?<p className="message">Xotelo не вернул предложения для этого состава и дат. Это не подтверждает отсутствие номеров или запрет для детей. Выберите другой отель либо используйте ручную оценку.</p>:<div className="lodging-results">{quote.rates.map(rate=>{const estimate=baseEstimate(rate,nights!,Number(rooms));return <article className="lodging-item" key={rate.code}><h4>{rate.name}</h4><p><b>{money(rate.nightly_base)} · базовый тариф за номер за ночь по описанию Xotelo</b></p><p>Налоги: {rate.tax_reported===null?"не указаны":`${money(rate.tax_reported)} (единица начисления не подтверждена)`}. Дополнительные сборы неизвестны.</p>{estimate!==null?<p>Базовая оценка за {nights} ночей, один номер: <b>{money(estimate)}</b>. Это не полная цена бронирования.</p>:<p>Для нескольких номеров общая сумма не вычисляется: тариф может не подтверждать размещение всей группы. Введите общую оценку вручную.</p>}
        <button type="button" className="secondary" disabled={disabled} onClick={()=>choose(rate,false)}>Выбрать тариф, оставить ручную сумму</button>
        {estimate!==null&&<button type="button" className="secondary" disabled={disabled} onClick={()=>choose(rate,true)}>Взять базовую оценку {money(estimate)} без сборов</button>}
      </article>})}</div>}
      <a href={quote.hotel.source_url} target="_blank" rel="noreferrer">Проверить отель на Tripadvisor ↗</a>
    </>}
    {value&&<div className="lodging-picked"><b>Выбран Xotelo: {value.observation.hotel.name} · {value.rate.name}</b><p>{value.choice.apply_base_estimate?"В бюджет перенесена базовая оценка за ночи. Налоги и сборы остаются неизвестными.":"В бюджете остаётся ваша ручная оценка. Проверьте сумму для всех ночей и номеров."}</p><button type="button" className="text-button" disabled={disabled} onClick={()=>onChoose(undefined)}>Убрать тариф Xotelo</button></div>}
  </section>;
}
