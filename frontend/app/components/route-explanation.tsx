"use client";

import { useEffect, useState } from "react";
import { api, type Explanation, type Route } from "../../lib/api";

export function RouteExplanation({route,disabled,onBusy,onGenerated}: {
  route: Route; disabled:boolean; onBusy:(label:string)=>void; onGenerated:(value:Explanation)=>void;
}) {
  const [error,setError]=useState("");
  const [working,setWorking]=useState(false);
  useEffect(()=>{setError("");},[route.id]);
  async function generate() {
    if (disabled || working) return;
    setWorking(true);setError("");onBusy("Готовим объяснение…");
    try {onGenerated(await api<Explanation>(`/routes/${route.id}/explanation${route.explanation?"?refresh=true":""}`,"POST"));}
    catch(e){setError(e instanceof Error?e.message:"Не удалось получить объяснение");}
    finally {setWorking(false);onBusy("");}
  }
  const value=route.explanation;
  return <section className="route-explanation" aria-label="Объяснение выбора маршрута"><h3>Почему этот маршрут</h3>
    {value && <div aria-live="polite"><span className="badge">{value.mode==="llm"?"llama.cpp · факты проверены":"Шаблон · факты проверены"}</span>
      {value.paragraphs.map((paragraph,i)=><p key={i}>{paragraph}</p>)}
      {value.mode==="template" && <p className="hint">Модель недоступна или её ответ не прошёл проверку. Показано объяснение из вычисленных фактов.</p>}
      {value.model && <p className="hint">Модель: {value.model}</p>}
    </div>}
    <button className="secondary" disabled={disabled || working} onClick={generate}>{working?"Готовим объяснение…":value?"Обновить объяснение":"Объяснить выбор"}</button>
    {error && <p role="alert" className="message error">{error}</p>}
  </section>;
}
