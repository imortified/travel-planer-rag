import { useState } from "react";
import type { AgeProfile } from "../../lib/api";

export function MemberAge({value,index,onChange}:{value?:AgeProfile;index:number;onChange:(profile:AgeProfile)=>void}) {
  const [mode,setMode]=useState(value?.birth_date?"birth":"years");
  return <div className="member-age">
    <label>Как указать возраст · участник {index+1}<select value={mode} onChange={e=>{setMode(e.target.value);onChange({age_on_start:null,birth_date:null});}}><option value="years">Возраст на начало поездки</option><option value="birth">Дата рождения</option></select></label>
    {mode==="years"?<label>Возраст на начало поездки · участник {index+1}<input type="number" min="0" max="120" step="1" placeholder="Не указан" value={value?.age_on_start ?? ""} onChange={e=>onChange({age_on_start:e.target.value===""?null:Number(e.target.value),birth_date:null})}/></label>:<label>Дата рождения · участник {index+1}<input type="date" value={value?.birth_date || ""} onChange={e=>onChange({age_on_start:null,birth_date:e.target.value || null})}/></label>}
    <p className="hint">Без возраста используется обычный тариф, а некоторые ограничения доступа требуют уточнения. Для учёта дня рождения во время поездки выберите дату рождения. Возраст в годах считается неизменным в течение поездки.</p>
  </div>;
}
