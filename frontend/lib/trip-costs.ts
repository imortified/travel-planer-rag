import type { BudgetSettings, MealSettings } from "./api";

export const mealNames = {breakfast:"Завтрак",lunch:"Обед",dinner:"Ужин"};
export type MealType = keyof typeof mealNames;
export type MealDraft = {enabled:boolean;start:string;end:string;duration:string;maxCost:string};
export type CostDraft = {budgetEnabled:boolean;lodging:string;transport:string;flights:string;reserve:string;
  mealsEnabled:boolean;mealMode?:"reviewed"|"estimate";meals:Record<MealType,MealDraft>};
export function defaultCostDraft():CostDraft {
  return {budgetEnabled:false,lodging:"",transport:"",flights:"",reserve:"0",mealsEnabled:false,mealMode:"estimate",meals:{
    breakfast:{enabled:false,start:"08:00",end:"11:00",duration:"45",maxCost:"25"},
    lunch:{enabled:true,start:"12:00",end:"15:00",duration:"60",maxCost:"25"},
    dinner:{enabled:false,start:"18:00",end:"21:00",duration:"60",maxCost:"30"},
  }};
}
export function amount(value:string,label:string,optional=false):number|null {
  if (!value.trim() && optional) return null;
  if (!/^\d+(?:[.,]\d{1,2})?$/.test(value.trim())) throw new Error(`Проверьте сумму: ${label}. Укажите неотрицательное число с точностью до цента.`);
  const result=Math.round(Number(value.replace(",","."))*100);
  if (!Number.isSafeInteger(result) || result>1_000_000_000) throw new Error(`Слишком большая сумма: ${label}.`);
  return result;
}
export function requireSelectedFlightBudget(draft:Pick<CostDraft,"budgetEnabled"|"flights">):number {
  if(!draft.budgetEnabled)throw new Error("Для выбранного перелёта включите бюджет всей поездки.");
  const total=amount(draft.flights,"перелёты на всю группу",true);
  if(total===null)throw new Error("Перелёт выбран, но его стоимость ещё не учтена. Укажите общую сумму всех билетов рядом с выбранным перелётом и повторите расчёт.");
  return total;
}
const minute=(value:string)=>{if(!/^\d{2}:\d{2}$/.test(value))throw new Error("Укажите время приёма пищи.");const [h,m]=value.split(":").map(Number);if(h>23||m>59)throw new Error("Проверьте время приёма пищи.");return h*60+m;};
export function costPayload(draft:CostDraft,destination:string):{budget_settings:BudgetSettings|null;meal_settings:MealSettings|null} {
  const budget_settings:BudgetSettings|null=draft.budgetEnabled?{lodging:amount(draft.lodging,"жильё",true),transport:amount(draft.transport,"наземный транспорт",true),flights:amount(draft.flights,"перелёты",true),transport_scope:"ground_only",reserve:amount(draft.reserve,"резерв")!}:null;
  let meal_settings:MealSettings|null=null;
  if(draft.mealsEnabled && destination!=="Demo City") {
    meal_settings={mode:draft.mealMode||"reviewed"};
    for (const type of Object.keys(mealNames) as MealType[]) {
      const slot=draft.meals[type];if(!slot.enabled)continue;
      const start=minute(slot.start),end=minute(slot.end),duration=Number(slot.duration);
      if(!Number.isInteger(duration)||duration<15||duration>180||end-start<duration)throw new Error(`Проверьте время и длительность: ${mealNames[type]}.`);
      meal_settings[type]={start,end,duration,max_cost_per_person:amount(slot.maxCost,mealNames[type])!};
    }
    if(!Object.keys(meal_settings).some(k=>k!=="mode"))throw new Error("Выберите хотя бы один приём пищи.");
  }
  return {budget_settings,meal_settings};
}
