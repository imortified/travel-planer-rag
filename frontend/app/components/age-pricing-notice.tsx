import type { Route } from "../../lib/api";

const reasons:Record<string,string>={age_required_for_access_check:"для проверки допуска нужен возраст всех участников",adult_companion_required:"для ребёнка требуется взрослый сопровождающий",age_admission_restricted:"возраст участника не соответствует условиям входа",age_access_review_expired:"условия допуска требуют повторной проверки"};

export function AgePricingNotice({route}:{route:Route}) {
  if(!route.result.data_context?.pricing_policy)return null;
  const days=route.result.days?.length?route.result.days:[route.result];
  const fallback=days.flatMap(d=>d.visits).flatMap(v=>v.participant_prices || []).filter(p=>p.reason.startsWith("regular_fallback"));
  const excluded=route.result.data_context.age_exclusions || [];
  return <div className="age-pricing-notice">
    <p>Билеты рассчитаны отдельно для каждого участника по возрасту на дату посещения. Расчёт и возраст сохранены в этой поездке. Льготы действуют при выполнении условий оператора; может потребоваться документ.</p>
    {!!fallback.length && <p>Для {fallback.length} билетов использован обычный тариф: возраст не указан или возрастная льгота не проверена. Подробности — в стоимости каждого посещения.</p>}
    {!!excluded.length && <details><summary>Исключено по возрастным условиям · {excluded.length}</summary>{excluded.map(p=><p key={`${p.id}:${p.travel_date}`}>{p.name} · {p.travel_date}: {reasons[p.reason] || p.reason}.</p>)}</details>}
  </div>;
}
