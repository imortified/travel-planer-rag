import type { TravelProfile, VisaReport as Report } from "../../lib/api";

const countries = [["BY","Беларусь"],["RU","Россия"],["UA","Украина"],["GE","Грузия"],["DE","Германия"],["GB","Великобритания · British citizen"],["US","США"],["ZZ","Другое / ручная проверка"]];
export const emptyProfile: TravelProfile = {citizenship:null,passport_kind:"unknown",biometric:null,special_entry_basis:null,visa_status:"unknown"};
const tri = (value:string) => value==="unknown"?null:value==="yes";

export function TravelDocuments({profile, index, onChange}: {profile?:TravelProfile;index:number;onChange:(profile:TravelProfile)=>void}) {
  const p=profile || emptyProfile;
  const patch=(value:Partial<TravelProfile>)=>onChange({...p,...value});
  return <details className="travel-documents"><summary>Гражданство и документы · участник {index+1}</summary>
    <label>Гражданство, участник {index+1}<select value={p.citizenship || ""} onChange={e=>onChange({...emptyProfile,citizenship:e.target.value || null})}><option value="">Не указано</option>{countries.map(([id,name])=><option value={id} key={id}>{name}</option>)}</select></label>
    {p.citizenship && p.citizenship!=="DE" && <>
      <label>Паспорт, участник {index+1}<select value={p.passport_kind} onChange={e=>patch({passport_kind:e.target.value as TravelProfile["passport_kind"]})}><option value="unknown">Не уточнён</option><option value="ordinary">Обычный заграничный паспорт</option><option value="special">Дипломатический, служебный или иной</option></select></label>
      {["UA","GE"].includes(p.citizenship) && <label>Биометрический паспорт, участник {index+1}<select value={p.biometric===null?"unknown":p.biometric?"yes":"no"} onChange={e=>patch({biometric:tri(e.target.value)})}><option value="unknown">Не уточнено</option><option value="yes">Да</option><option value="no">Нет</option></select></label>}
      <label>Особое основание для въезда, участник {index+1}<select value={p.special_entry_basis===null?"unknown":p.special_entry_basis?"yes":"no"} onChange={e=>patch({special_entry_basis:tri(e.target.value)})}><option value="unknown">Не уточнено</option><option value="no">Нет</option><option value="yes">Есть / нужна отдельная проверка</option></select></label>
      <p className="hint">Например, ВНЖ, долгосрочная виза, второе гражданство или права члена семьи гражданина ЕС — основания для ручной проверки.</p>
      <label>Краткосрочная виза, участник {index+1}<select value={p.visa_status} onChange={e=>patch({visa_status:e.target.value as TravelProfile["visa_status"]})}><option value="unknown">Не уточнено</option><option value="not_held">Нет визы</option><option value="held">Есть виза (со слов участника)</option></select></label>
    </>}
    <p className="hint">Укажите гражданство используемого документа. Номера документов не нужны. Срок действия паспорта, визы и разрешённое число въездов здесь не проверяются.</p>
  </details>;
}

const reasons:Record<string,string>={
  unsupported_destination:"Направление не входит в справочник",unsupported_purpose:"Справочник охватывает только туризм",missing_dates:"Укажите даты поездки",past_or_invalid_dates:"Проверьте даты: поддерживаются будущие поездки на 1–14 дней",rules_outside_review_window:"Для даты расчёта или поездки требуется повторная проверка источников",missing_citizenship:"Укажите гражданство",unsupported_citizenship:"Гражданство не входит в справочник",ordinary_passport_not_confirmed:"Обычный паспорт не подтверждён",special_basis_needs_review:"Уточните особое основание для въезда",biometric_passport_not_confirmed:"Уточните, биометрический ли паспорт",held_visa_not_verified:"Участник указал наличие визы; её действительность и условия не проверены",visa_not_held:"Виза нужна, участник указал её отсутствие",visa_status_unknown:"Виза нужна, её наличие не уточнено",short_application_notice:"До поездки меньше обычного минимального срока подачи — 15 дней",short_processing_time:"До поездки меньше обычного срока рассмотрения заявления",extended_processing_risk:"При продлении рассмотрения времени до поездки может не хватить",
};
export function VisaReport({report}: {report?:Report|null}) {
  if (report?.status==="not_applicable") return null;
  if (!report) return <p className="hint">Для этого сохранённого результата визовый отчёт отсутствует.</p>;
  return <section className="visa-report" aria-label="Визовые условия">
    <h3>Визовые условия · Португалия</h3>
    <p className="hint">Требования к визе и предупреждения о сроках. Готовность документов и разрешение на въезд не подтверждены.</p>
    <p className="visa-meta">Расчёт: {report.assessed_on} · Источники проверены: {report.checked_on}<br/>Повторная проверка справочника — до {report.review_by}. Это срок пересмотра данных проекта.</p>
    {report.participants.map((p,i)=><article className={`visa-person ${p.status}`} key={i}>
      <b>{p.name} · {p.citizenship_name || "Гражданство не указано"}</b>
      <p className="visa-status">{p.requirement==="unknown"?"Нужна ручная проверка":p.requirement==="required"?"Требуется виза":"Освобождение от визы при соблюдении условий"}</p>
      <p>{p.requirement==="unknown"?"Справочное условие, применимость не подтверждена: ":""}{p.summary}</p>
      {!!p.reasons.length && <ul>{p.reasons.map(r=><li key={r}>{reasons[r] || r}</li>)}</ul>}
      {p.timing && <div className="visa-timing"><p>До поездки: {p.timing.days_until_trip} дн. Обычная подача — не позднее {p.timing.recommended_apply_by} (за {p.timing.application_notice_days} дней).</p>
        {p.timing.processing_days!==null && <p>Обычное рассмотрение: {p.timing.processing_days} календарных дней; возможное продление — до {p.timing.extended_processing_days} дней после приёма заявления и необходимых документов.</p>}
        <p>Запись на приём и доставка не учтены; срок не гарантирован. Исключения для срочной подачи требуют ручной проверки.</p><a href={p.timing.source_url} target="_blank" rel="noreferrer">Порядок подачи · gov.pt ↗</a></div>}
      <div className="visa-sources">{p.sources.map(s=><a key={s.url} href={s.url} target="_blank" rel="noreferrer">{s.title} ↗</a>)}</div>
    </article>)}
    <details><summary>Границы проверки и версия</summary><p>{report.version} · {report.travel_date || "Без даты"}{report.end_date && report.end_date!==report.travel_date?` — ${report.end_date}`:""}</p><ul>{report.limitations.map(l=><li key={l}>{l}</li>)}</ul></details>
  </section>;
}
