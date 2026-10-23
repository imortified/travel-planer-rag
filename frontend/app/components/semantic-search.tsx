"use client";

import { useState } from "react";
import { api, type SemanticSearch } from "../../lib/api";

const categories:Record<string,string>={history:"История",nature:"Природа",food:"Еда",art:"Искусство"};

export function SemanticInterests({date,endDate,disabled,members,selected,onApply,onClear,onBusy}: {
  date:string;endDate:string;disabled:boolean;members:{key:number;name:string}[];selected:string;
  onApply:(query:string,category:string,target:string)=>void;onClear:()=>void;onBusy:(label:string)=>void;
}) {
  const [text,setText]=useState("");
  const [target,setTarget]=useState("all");
  const [result,setResult]=useState<{value:SemanticSearch;signature:string}>();
  const [manual,setManual]=useState("");
  const [error,setError]=useState("");
  const signature=JSON.stringify([text.trim(),date,endDate]);
  const value=result?.signature===signature?result.value:undefined;
  async function search() {
    if(disabled || !date || !text.trim()) return;
    onBusy("Ищем по интересам…");setError("");setManual("");setResult(undefined);
    try {const value=await api<SemanticSearch>("/assistant/search","POST",{text:text.trim(),travel_date:date,end_date:endDate||null});setResult({value,signature});}
    catch(e){setError(e instanceof Error?e.message:"Не удалось найти места");}
    finally{onBusy("");}
  }
  const category=value?.category || manual;
  const matches=value?.matches.filter(m=>m.score>=value.document_threshold) || [];
  return <section className="card semantic-search" aria-label="Поиск по интересам">
    <h3>Что хочется увидеть?</h3><p className="hint">Опишите одно пожелание, например «археология и древние цивилизации». Найдём близкую категорию и подходящие места на выбранные даты.</p>
    <label>Пожелание<input disabled={disabled} maxLength={300} value={text} onChange={e=>setText(e.target.value)} placeholder="Живопись и скульптура"/></label>
    <button type="button" className="secondary" disabled={disabled || !date || !text.trim()} onClick={search}>Найти по смыслу</button>
    {!date && <p className="hint">Сначала укажите дату начала поездки.</p>}
    {error && <p className="message error" role="alert">{error}</p>}
    {value && <div aria-live="polite" className="semantic-results">
      {value.category?<p>Ближайший интерес: <b>{categories[value.category]}</b>.</p>:<p className="message">{value.reason==="negative_or_complex"?"Отрицания задайте ползунками интересов или исключите конкретные места в каталоге. Для поиска напишите положительное пожелание.":"Уверенного соответствия нет. Выберите категорию вручную или уточните пожелание."}</p>}
      {!value.category && value.reason!=="negative_or_complex" && <label>Уточните интерес<select disabled={disabled} value={manual} onChange={e=>setManual(e.target.value)}><option value="">Выберите категорию</option>{Object.entries(categories).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>}
      {value.reason!=="negative_or_complex" && (matches.length?<ol className="semantic-matches">{matches.slice(0,3).map(m=><li key={m.place_id}><b>{m.name}</b><p>{m.description}</p><small>Смысловая близость: {m.score.toFixed(2)} · <a href={m.source_url} target="_blank" rel="noreferrer">Источник</a>{m.checked_on?` · проверен ${m.checked_on}`:" · категория каталога"}</small></li>)}</ol>:<p className="hint">По этой теме нет достаточно близких мест с данными для расчёта на выбранные даты.</p>)}
      <p className="hint">Близость — оценка сходства текстов, а не вероятность. Найденные места не становятся обязательными.</p>
      {value.reason!=="negative_or_complex" && <><label>Чей интерес усиливаем?<select disabled={disabled} value={target} onChange={e=>setTarget(e.target.value)}><option value="all">Всех участников</option>{members.map((m,i)=><option key={m.key} value={String(m.key)}>{m.name || `Участник ${i+1}`}</option>)}</select></label><button type="button" className="secondary" disabled={disabled || !category || !members.length || (target!=="all" && !members.some(m=>String(m.key)===target))} onClick={()=>onApply(value.query,category,target)}>Применить интерес и тему поиска</button><p className="hint">Вес выбранной категории станет 100%. Остальные веса можно изменить ползунками. Тема поиска общая для маршрута.</p></>}
    </div>}
    {selected && <div className="message semantic-selected">Тема в следующем расчёте: <b>{selected}</b><button disabled={disabled} type="button" className="text-button" onClick={onClear}>Убрать тему поиска</button></div>}
  </section>;
}
