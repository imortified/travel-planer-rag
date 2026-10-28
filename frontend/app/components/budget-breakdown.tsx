import { money,type BudgetBreakdown as Costs } from "../../lib/api";

export function BudgetBreakdown({value,flightSelected=false}:{value?:Costs|null;flightSelected?:boolean}) {
  if(!value)return null;
  const estimate=(amount:number|null)=>amount===null?"Не указано":money(amount);
  return <section className="budget-breakdown"><h3>Разбивка расходов на группу</h3>
    <dl><dt>Посещения достопримечательностей</dt><dd>{money(value.admissions)}</dd>
      <dt>Питание · заказы и оценки</dt><dd>{money(value.food)}</dd>
      {!!value.food_estimate && <><dt>Из них питание · ваша оценка</dt><dd>{money(value.food_estimate)}</dd></>}
      <dt>{value.lodging_price_basis==="xotelo_base_estimate"?"Жильё · базовая оценка Xotelo":"Жильё · ваша оценка"}</dt><dd>{estimate(value.lodging_estimate)}</dd>
      <dt>{value.transport_scope==="ground_only"?"Наземный транспорт · ваша оценка":"Транспорт · ваша оценка"}</dt><dd>{estimate(value.transport_estimate)}</dd>
      {value.transport_scope==="ground_only"&&<><dt>Перелёты · ваша общая оценка</dt><dd>{estimate(value.flights_estimate??null)}</dd></>}
      <dt><b>Учтённая сумма</b></dt><dd><b>{money(value.known_total)}</b></dd>
      <dt>Резерв · остаётся неизрасходованным</dt><dd>{money(value.reserve)}</dd>
      <dt>Свободно после резерва</dt><dd>{money(value.unallocated_after_reserve)}</dd></dl>
    {flightSelected&&value.flights_estimate==null&&<p className="message flight-budget-pending" role="status">В этом сохранённом расчёте стоимость выбранного перелёта не указана и не входит в учтённую сумму. Заполните общую сумму билетов в форме и постройте маршрут заново.</p>}
    {value.scope==="stops_only"?<p className="hint">Бюджет задан на остановки. Жильё и транспорт в эту сумму не включены.</p>:<p className="hint">Общий бюджет {money(value.budget)}. {value.lodging_price_basis==="xotelo_base_estimate"?"Жильё — базовая оценка Xotelo без подтверждённых налогов и сборов; транспорт — ваша оценка.":"Жильё и транспорт введены вручную; цена бронирования не подтверждена."}{!!value.unknown_categories.length && " Неуказанные расходы ещё нужно добавить."}</p>}
    <p className="hint">Питание включает выбранные заказы или явно заданные оценки в маршруте. Оценки не подтверждают цены меню. Остальные блюда, напитки и дополнительные расходы не учтены. Резерв не прибавляется к стоимости.</p>
  </section>;
}
