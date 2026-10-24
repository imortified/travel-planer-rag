import type { AgeProfile } from "./api";

export function ageProfile(value?:AgeProfile):AgeProfile {
  const age=value?.age_on_start ?? null,birth=value?.birth_date || null;
  if(age!==null && (!Number.isInteger(age)||age<0||age>120))throw new Error("Возраст должен быть целым числом от 0 до 120 лет.");
  if(age!==null && birth)throw new Error("Укажите возраст или дату рождения, не оба поля.");
  if(birth && (!/^\d{4}-\d{2}-\d{2}$/.test(birth)||!Number.isFinite(Date.parse(`${birth}T12:00:00Z`))||new Date(`${birth}T12:00:00Z`).toISOString().slice(0,10)!==birth))throw new Error("Проверьте дату рождения.");
  return {age_on_start:age,birth_date:birth};
}
