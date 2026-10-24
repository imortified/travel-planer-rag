"use client";

import { useState } from "react";
import { api, clock, money, type ParseOutput } from "../../lib/api";
import { destinationName } from "../../lib/destinations";

const interestNames: Record<string,string>={history:"История",nature:"Природа",food:"Еда",art:"Искусство"};
const interestText=(values:Record<string,number>)=>Object.entries(values).map(([k,v])=>`${interestNames[k]||k}: ${Math.round(v*100)}%`).join(", ") || "Не указаны";

export function TextRequest({disabled, memberNames, onBusy, onApply}: {
  disabled: boolean; memberNames: string[]; onBusy: (label:string)=>void;
  onApply: (parsed: ParseOutput)=>Promise<void>;
}) {
  const [text,setText]=useState("");
  const [parsed,setParsed]=useState<ParseOutput>();
  const [error,setError]=useState("");
  const [working,setWorking]=useState(false);
  const [applied,setApplied]=useState(false);
  async function run(label: string, action:()=>Promise<void>) {
    if (disabled || working) return;
    setWorking(true);setError("");onBusy(label);
    try {await action();} catch(e) {setError(e instanceof Error?e.message:"Не удалось обработать запрос");}
    finally {setWorking(false);onBusy("");}
  }
  const draft=parsed?.draft;
  return <section className="card text-request" aria-label="Запрос обычным текстом">
    <h2>Расскажите о поездке</h2><p className="hint">Можно заполнить форму ниже или описать пожелания. Сначала проверим распознанные параметры — поездка сама не создаётся.</p>
    <label>Пожелания к поездке<textarea maxLength={2000} rows={4} disabled={disabled || working} value={text} onChange={e=>{setText(e.target.value);setParsed(undefined);setApplied(false);}}
      placeholder="Анна и Максим едут в Лиссабон с 23 по 25 октября 2026. Общий бюджет 100 евро, любим историю и природу. Каждый день с 9 до 18."/></label>
    <button className="secondary" disabled={disabled || working || !text.trim()} onClick={()=>run("Разбираем текст…",async()=>{setParsed(await api<ParseOutput>("/assistant/parse","POST",{text,member_names:memberNames}));setApplied(false);})}>Разобрать текст</button>
    <div aria-live="polite">{error && <div role="alert" className="message error">{error} Форма остаётся доступной.</div>}
    {parsed && draft && <div className="draft-preview"><h3>Распознанные параметры</h3>
      <dl><dt>Направление</dt><dd>{draft.destination?destinationName(draft.destination):"Не указано"}</dd>
      <dt>Даты</dt><dd>{draft.travel_date || "Не указаны"}{draft.end_date?` — ${draft.end_date}`:""}</dd>
      <dt>Бюджет группы</dt><dd>{parsed.group_budget===null?"Требует уточнения":money(parsed.group_budget)}</dd>
      <dt>Участники</dt><dd>{draft.participants.map(m=>m.name).join(", ") || (draft.participant_count?`${draft.participant_count} чел.`:memberNames.join(", ") || "Не указаны")}</dd>
      <dt>Часы дня</dt><dd>{draft.start===null?"Из формы":clock(draft.start)} — {draft.end===null?"Из формы":clock(draft.end)}</dd>
      <dt>Обязательные места</dt><dd>{draft.mandatory_names.join(", ") || "Не указаны"}</dd>
      <dt>Исключённые места</dt><dd>{draft.excluded_names.join(", ") || "Не указаны"}</dd></dl>
      <details><summary>Распознанные интересы</summary><p>Общие: {interestText(draft.common_interests)}</p>{draft.participants.map(m=><p key={m.name}>{m.name}: {interestText(m.interests)}</p>)}<p className="hint">Неуказанные веса остаются в форме; у новых участников — 50%.</p></details>
      {!!parsed.questions.length && <div className="message"><b>Нужно уточнить</b><ul>{parsed.questions.map((q,i)=><li key={i}>{q.text}</li>)}</ul><p>Дополните текст выше или имена участников в форме и повторите разбор.</p></div>}
      {parsed.warnings.map((warning,i)=><p className="hint" key={i}>{warning}</p>)}
      <button className="secondary" disabled={disabled || working || !!parsed.questions.length || applied} onClick={()=>run("Заполняем форму…",async()=>{await onApply(parsed);setApplied(true);})}>{applied?"Параметры применены ✓":"Применить параметры к форме"}</button>
      <p className="hint">{parsed.model} · После применения проверьте интересы и ограничения, затем нажмите «Построить маршрут».</p>
    </div>}</div>
  </section>;
}
