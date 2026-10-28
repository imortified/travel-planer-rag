import { money } from "../../lib/api";
import { amount,mealNames,type CostDraft,type MealType } from "../../lib/trip-costs";

export function TripCostForm({value,onChange,destination,budget}:{value:CostDraft;onChange:(v:CostDraft)=>void;destination:string;budget:string}) {
  const patch=(next:Partial<CostDraft>)=>onChange({...value,...next});
  let available:number|undefined;
  try {available=(amount(budget,"бюджет")||0)-(amount(value.lodging,"жильё",true)||0)-(amount(value.transport,"транспорт",true)||0)-(amount(value.flights,"перелёты",true)||0)-(amount(value.reserve,"резерв")||0);} catch {}
  return <section className="trip-costs">
    <h3>Расходы и питание</h3>
    <label className="cost-toggle"><input type="checkbox" checked={value.budgetEnabled} onChange={e=>patch({budgetEnabled:e.target.checked})}/> Бюджет всей поездки</label>
    {value.budgetEnabled?<>
      <p className="hint">Оценки на всю группу за весь период. Пустое поле означает «не указано», ноль — расходов по этой статье нет. Уже оплаченные расходы тоже включайте, если они входят в общий бюджет. Это ваши оценки, а не цены бронирования.</p>
      <div className="two"><label>Жильё на всю группу, EUR<input type="number" min="0" max="10000000" step="0.01" placeholder="Не указано" value={value.lodging} onChange={e=>patch({lodging:e.target.value})}/></label><label>Наземный транспорт на всю группу, EUR<input type="number" min="0" max="10000000" step="0.01" placeholder="Не указано" value={value.transport} onChange={e=>patch({transport:e.target.value})}/></label></div>
      <label>Перелёты на всю группу, EUR<input type="number" min="0" max="10000000" step="0.01" placeholder="Не указано" value={value.flights} onChange={e=>patch({flights:e.target.value})}/></label>
      <p className="hint">Перелёты — общая сумма всех билетов туда и обратно для всех участников с нужным багажом. В наземный транспорт включайте трансферы, городской транспорт и аренду; авиабилеты в него повторно не добавляйте. Если перелёта нет, укажите 0.</p>
      <label>Неприкосновенный резерв, EUR<input type="number" min="0" max="10000000" step="0.01" value={value.reserve} onChange={e=>patch({reserve:e.target.value})}/></label>
      {available!==undefined && <p className={`hint${available<0?" error":""}`}>На посещения и выбранные заказы питания остаётся {money(available)} после указанных оценок и резерва.</p>}
    </>:<p className="hint">Бюджет ограничивает посещения и выбранные заказы. Жильё, транспорт и прочие расходы отдельно не учтены.</p>}
    <label className="cost-toggle"><input type="checkbox" disabled={destination==="Demo City"} checked={value.mealsEnabled && destination!=="Demo City"} onChange={e=>patch({mealsEnabled:e.target.checked})}/> Планировать приёмы пищи каждый день</label>
    {destination==="Demo City" && <p className="hint">Для питания выберите реальный город и загрузите каталог.</p>}
    {value.mealsEnabled && destination!=="Demo City" && <>
      <label>Как учитывать питание<select value={value.mealMode||"reviewed"} onChange={e=>patch({mealMode:e.target.value as "reviewed"|"estimate"})}><option value="estimate">Моя оценка расходов · кафе по карте</option><option value="reviewed">Проверенный заказ из меню</option></select></label>
      <p className="hint">{value.mealMode==="estimate"?"Время резервируется в реальном заведении с известными обычными часами работы. Указанная ниже сумма целиком учитывается как ваша оценка на человека; цена меню, наличие блюд и столиков не подтверждены.":"В расписание включается заведение с проверенным конкретным заказом. Если подходящего заказа нет, расчёт попросит изменить условия или выбрать режим оценки."} Выбранные приёмы пищи обязательны каждый день, занимают время и входят в дневную нагрузку. Заведение может повторяться.</p>
      {(Object.keys(mealNames) as MealType[]).map(type=>{const slot=value.meals[type];const change=(next:Partial<typeof slot>)=>patch({meals:{...value.meals,[type]:{...slot,...next}}});return <div className="meal-slot" key={type}>
        <label className="cost-toggle"><input type="checkbox" checked={slot.enabled} onChange={e=>change({enabled:e.target.checked})}/>{mealNames[type]}</label>
        {slot.enabled && <><div className="two"><label>{mealNames[type]}: с<input type="time" value={slot.start} onChange={e=>change({start:e.target.value})}/></label><label>{mealNames[type]}: до<input type="time" value={slot.end} onChange={e=>change({end:e.target.value})}/></label></div><div className="two"><label>{mealNames[type]}: минимум минут<input type="number" min="15" max="180" value={slot.duration} onChange={e=>change({duration:e.target.value})}/></label><label>{mealNames[type]}: {value.mealMode==="estimate"?"оценка":"лимит"} на человека, EUR<input type="number" min="0" max="10000000" step="0.01" value={slot.maxCost} onChange={e=>change({maxCost:e.target.value})}/></label></div></>}
      </div>})}
      <p className="hint">Каждому участнику, включая детей, пока назначается одинаковый указанный заказ. Детские порции и меню ещё не подключены. Очереди, наличие столиков и диетические условия не подтверждены. При отсутствии подходящего заведения система попросит изменить параметры. Для ужина может потребоваться более позднее окончание дня.</p>
    </>}
  </section>;
}
