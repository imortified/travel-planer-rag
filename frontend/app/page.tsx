"use client";

import { useEffect, useRef, useState } from "react";
import { RouteDays } from "./components/route-days";
import { api, clock, money, minutes, type Group, type Trip, type Route, type Poi, type Interests } from "../lib/api";

type Draft = {key: number; name: string; interests: Interests; userId?: number; joined?: boolean};
const categories = [["history", "История"], ["nature", "Природа"], ["food", "Еда"]];
const demoPois: Poi[] = [{id:"museum",name:"Музей",eligible:true,issues:[]}, {id:"market",name:"Рынок",eligible:true,issues:[]}, {id:"park",name:"Парк",eligible:true,issues:[]}];
const issueNames: Record<string,string> = {unknown_price:"Нет цены", unknown_closed_or_unsupported_hours:"Нет подходящих часов", restricted_access:"Ограничен доступ", unsupported_conditional_access_or_fee:"Условный доступ"};
const reasonNames: Record<string,string> = {mandatory_excluded_conflict:"Место одновременно обязательно и запрещено", no_feasible_visits:"Не найдено допустимых посещений", mandatory_not_scheduled:"Обязательное место не включено", budget_exceeded:"Превышен бюджет", cost_mismatch:"Стоимость рассчитана неверно", duplicate_visit_across_days:"Место повторяется в разные дни", unreachable_arrival:"Недостаточно времени на переезд", outside_opening_hours:"Посещение вне часов работы", missing_travel_time:"Нет данных о переезде"};

export default function Home() {
  const [name,setName] = useState("Наше путешествие");
  const [members,setMembers] = useState<Draft[]>([
    {key:1,name:"",interests:{history:1,nature:0.5,food:0.5}},
    {key:2,name:"",interests:{history:0.5,nature:1,food:0.5}},
  ]);
  const key = useRef(3);
  const [gid,setGid] = useState<number>();
  const [loadId,setLoadId] = useState("");
  const [destination,setDestination] = useState("Demo City");
  const [date,setDate] = useState("");
  const [endDate,setEndDate] = useState("");
  const [budget,setBudget] = useState("100");
  const [start,setStart] = useState("09:00");
  const [end,setEnd] = useState("18:00");
  const [algorithm,setAlgorithm] = useState("beam");
  const [pois,setPois] = useState<Poi[]>(demoPois);
  const [choices,setChoices] = useState<Record<string,string>>({});
  const [catalogKey,setCatalogKey] = useState("Demo City");
  const [busy,setBusy] = useState("");
  const [error,setError] = useState("");
  const [notice,setNotice] = useState("");
  const [trips,setTrips] = useState<Trip[]>([]);
  const [shown,setShown] = useState<{route: Route; trip: Trip}>();
  const pendingTrip = useRef<{signature: string; trip: Trip} | undefined>(undefined);

  useEffect(() => { const stored = localStorage.getItem("travel-group-id"); if (stored) setLoadId(stored); }, []);
  function changeMember(index: number, patch: Partial<Draft>) {setMembers(ms => ms.map((m,i) => i===index ? {...m,...patch} : m));}
  function changeDate(value: string) {
    if (value === date) return;
    setDate(value); setChoices({});
    if (destination === "Lisbon") {setPois([]); setCatalogKey("");}
  }
  function changeEndDate(value: string) {
    if (value === endDate) return;
    setEndDate(value); setChoices({});
    if (destination === "Lisbon") {setPois([]); setCatalogKey("");}
  }
  async function task(label: string, action: () => Promise<void>) {
    if (busy) return; setBusy(label); setError(""); setNotice("");
    try {await action();} catch(e) {setError(e instanceof Error ? e.message : "Не удалось выполнить запрос");} finally {setBusy("");}
  }
  async function saveGroup() {
    if (!name.trim() || members.some(m=>!m.name.trim())) throw new Error("Введите название группы и имена всех участников.");
    let id = gid;
    if (!id) {
      const group = await api<Group>("/groups","POST",{name:name.trim()});
      id=group.id; setGid(id); setLoadId(String(id)); localStorage.setItem("travel-group-id",String(id));
    }
    for (let i=0;i<members.length;i++) {
      const m = {...members[i]};
      if (!m.userId) {const user = await api<{id:number}>("/users","POST",{name:m.name.trim()}); m.userId=user.id; changeMember(i,{userId:user.id});}
      if (!m.joined) {await api(`/groups/${id}/members`,"POST",{user_id:m.userId}); m.joined=true; changeMember(i,{joined:true});}
      await api(`/groups/${id}/members/${m.userId}/preferences`,"PUT",{interests:m.interests});
    }
    return id;
  }
  async function loadGroup() {
    const id=Number(loadId); if (!Number.isInteger(id) || id<=0) throw new Error("Укажите положительный ID группы.");
    const group = await api<Group>(`/groups/${id}`);
    const history = await api<Trip[]>(`/groups/${id}/trips`);
    setGid(id); setName(group.name); setMembers(group.members.map(m=>({key:key.current++,name:m.name,userId:m.user_id,joined:true,interests:m.interests})));
    setTrips(history); setShown(undefined); pendingTrip.current=undefined;
    localStorage.setItem("travel-group-id",String(id)); setNotice(`Группа №${id} загружена`);
  }
  async function calculate() {
    if (!members.length) throw new Error("Добавьте хотя бы одного участника.");
    if (destination==="Lisbon" && !date) throw new Error("Выберите дату поездки.");
    if (destination==="Lisbon" && catalogKey!==`Lisbon:${date}:${endDate || date}`) throw new Error("Сначала загрузите места для выбранной даты.");
    if (!Number.isFinite(Number(budget)) || Number(budget)<0 || Number(budget)>10000000 || !budget.trim()) throw new Error("Проверьте общий бюджет.");
    if (!start || !end || minutes(start)>=minutes(end)) throw new Error("Окончание дня должно быть позже начала.");
    if (endDate && (!date || endDate<date || (Date.parse(endDate)-Date.parse(date))/86400000>13)) throw new Error("Период должен содержать от 1 до 14 дней.");
    const id = await saveGroup();
    const payload={destination, travel_date:date || null, end_date:endDate || null, budget:Math.round(Number(budget)*100), start:minutes(start), end:minutes(end),
      mandatory:Object.keys(choices).filter(k=>choices[k]==="mandatory"), excluded:Object.keys(choices).filter(k=>choices[k]==="excluded")};
    const signature=JSON.stringify({id,payload,interests:members.map(m=>m.interests)});
    let trip = pendingTrip.current?.signature===signature ? pendingTrip.current.trip : undefined;
    if (!trip) {trip = await api<Trip>(`/groups/${id}/trips`,"POST",payload); pendingTrip.current={signature,trip}; setTrips(old=>[...old,trip!]);}
    const route = await api<Route>(`/trips/${trip.id}/routes?algorithm=${algorithm}`,"POST");
    setShown({route,trip}); pendingTrip.current=undefined;
  }
  async function showTrip(trip: Trip) {
    const routes=await api<Route[]>(`/trips/${trip.id}/routes`);
    if (!routes.length) {setNotice("В запросе ещё нет сохранённых маршрутов. Повторите расчёт с нужными параметрами."); return;}
    setShown({route:routes[routes.length-1],trip});
  }
  const result=shown?.route.result;
  const resultPois=Object.fromEntries((result?.data_context?.pois || demoPois).map(p=>[p.id,p.name]));

  return <main className="shell">
    <aside className="sidebar"><a className="brand" href="/">попутно<span>↗</span></a><p className="brand-note">Хорошая поездка начинается<br/>с общего плана.</p>
      <div className="nav-item active"><span>01</span> Планировщик</div><div className="nav-item"><span>02</span> Ваши поездки <b>{trips.length}</b></div>
      <div className="sidebar-bottom"><span className="dot"/> Локальная версия MVP<p>Общие интересы. Несколько дней.<br/>Проверяемый маршрут.</p></div>
    </aside>
    <section className="workspace"><header><span className="eyebrow">ПУТЕШЕСТВУЙТЕ ВМЕСТЕ</span><span className="header-tag">План на 1–14 дней</span></header>
      <div className="intro"><h1>Разные интересы.<br/><em>Один маршрут.</em></h1><p>Соберите компанию, расскажите о предпочтениях.<br/>Мы найдём места, на которые хватит времени и бюджета.</p></div>
      <div className="columns"><div className="form-column">
        <fieldset disabled={!!busy} className="card"><legend><span className="step">1</span> Ваша компания</legend>
          <div className="card-top"><label>Название группы<input value={name} maxLength={100} disabled={!!gid} onChange={e=>setName(e.target.value)} /></label>{gid && <span className="badge">Группа №{gid}</span>}</div>
          {members.map((m,i)=><div className="member" key={m.key}>
            <div className="member-heading"><span className="avatar">{i+1}</span><label className="member-name"><span className="sr-only">Имя участника {i+1}</span><input placeholder={`Имя участника ${i+1}`} value={m.name} disabled={!!m.userId} maxLength={100} onChange={e=>changeMember(i,{name:e.target.value})}/></label>{!m.userId && <button type="button" className="text-button" aria-label={`Удалить участника ${i+1}`} onClick={()=>setMembers(ms=>ms.filter(x=>x.key!==m.key))}>×</button>}</div>
            <div className="interests">{categories.map(([id,label])=><label key={id}>{label}<output>{Math.round((m.interests[id] || 0)*100)}%</output><input type="range" aria-label={`${label}, участник ${i+1}`} min="0" max="1" step="0.1" value={m.interests[id] || 0} onChange={e=>changeMember(i,{interests:{...m.interests,[id]:Number(e.target.value)}})} /></label>)}</div>
          </div>)}
          <div className="actions"><button type="button" className="secondary" disabled={members.length>=6} onClick={()=>setMembers(ms=>[...ms,{key:key.current++,name:"",interests:{history:0.5,nature:0.5,food:0.5}}])}>＋ Участник</button><button className="text-button" onClick={()=>task("Сохраняем группу…",async()=>{const id=await saveGroup();setNotice(`Группа №${id} и предпочтения сохранены`);})}>Сохранить группу</button><small>{members.length} / 6</small></div>
          <details className="load"><summary>Продолжить с существующей группой</summary><div className="actions"><label>ID группы<input type="number" min="1" value={loadId} onChange={e=>setLoadId(e.target.value)}/></label><button className="secondary" onClick={()=>task("Загружаем группу…",loadGroup)}>Загрузить</button></div></details>
        </fieldset>
        <fieldset disabled={!!busy} className="card"><legend><span className="step">2</span> Параметры поездки</legend>
          <div className="two"><label>Куда отправимся<select value={destination} onChange={e=>{setDestination(e.target.value);setChoices({});setPois(e.target.value==="Demo City"?demoPois:[]);setCatalogKey(e.target.value==="Demo City"?"Demo City":"");}}><option value="Demo City">Демо-город · без сети</option><option value="Lisbon">Лиссабон · реальные места</option></select></label><label>Начало поездки<input type="date" value={date} onChange={e=>changeDate(e.target.value)} onInput={e=>changeDate(e.currentTarget.value)}/></label></div>
          <div className="two"><label>Конец поездки<input type="date" value={endDate} min={date || undefined} onChange={e=>changeEndDate(e.target.value)} onInput={e=>changeEndDate(e.currentTarget.value)}/></label><p className="hint">До 14 дней включительно. Без даты окончания — один день. Бюджет общий на весь период.</p></div>
          <div className="two"><label>Бюджет на всю группу, EUR<input type="number" min="0" max="10000000" step="0.01" value={budget} onChange={e=>setBudget(e.target.value)}/></label><label>Алгоритм<select value={algorithm} onChange={e=>setAlgorithm(e.target.value)}><option value="beam">Beam search</option><option value="greedy">Greedy</option></select></label></div>
          <div className="two"><label>Начало дня<input type="time" value={start} onChange={e=>setStart(e.target.value)}/></label><label>Окончание дня<input type="time" value={end} onChange={e=>setEnd(e.target.value)}/></label></div>
          <p className="hint">{destination==="Lisbon"?"Центр Лиссабона, старт — Rossio. Автомобильные переезды. В расчёт входят бесплатные места с известными часами работы. Время — местное.":"Синтетические музей, рынок и парк для проверки сценария. Бюджет учитывает входные билеты на всех участников."}</p>
          {destination==="Lisbon" && <button className="secondary" disabled={!date} onClick={()=>task("Загружаем каталог…",async()=>{const c=await api<{pois:Poi[]}>(`/destinations/Lisbon/pois?travel_date=${date}${endDate?`&end_date=${endDate}`:""}`);setPois(c.pois);setCatalogKey(`Lisbon:${date}:${endDate || date}`);setNotice(`Найдено ${c.pois.length} мест, подходят для расчёта: ${c.pois.filter(p=>p.eligible).length}`);})}>Загрузить места на период</button>}
          {!!pois.length && <details className="places"><summary>Места и ограничения <span>{pois.filter(p=>p.eligible).length} доступны</span></summary>{pois.map(p=><div className="poi" key={p.id}><div><b>{p.name}</b>{p.eligible && p.available_dates && <small>Открыто: {p.available_dates.join(", ")}</small>}{!p.eligible && <small>{p.issues.map(x=>issueNames[x]||x).join(" · ")}</small>}</div><select aria-label={`Ограничение: ${p.name}`} value={choices[p.id]||"optional"} onChange={e=>setChoices(c=>({...c,[p.id]:e.target.value}))}><option value="optional">По желанию</option><option value="mandatory" disabled={!p.eligible}>Обязательно</option><option value="excluded">Исключить</option></select></div>)}</details>}
        </fieldset>
        <div aria-live="polite">{error && <div role="alert" className="message error">{error}</div>}{notice && <div className="message">{notice}</div>}</div>
        <button className="primary" disabled={!!busy || !members.length} onClick={()=>task("Собираем маршрут…",calculate)}>{busy||"Построить маршрут"}<span aria-hidden="true">↗</span></button>
        <p className="under-button">Запрос и результат сохраняются в вашей группе.</p>
      </div>
      <div className="result-column"><section className="result card" aria-live="polite"><div className="result-header"><span className="eyebrow">ВАШ ПЛАН</span><span className="badge">{shown?`Маршрут №${shown.route.id}`:"Всё впереди"}</span></div>
        {!shown ? <div className="empty"><div className="journey-art" aria-hidden="true"><span>А</span><i/><span>Б</span><i/><span>В</span></div><h2>Место для новых<br/>впечатлений</h2><p>Здесь появится маршрут с временем<br/>посещений, переездами и стоимостью.</p><div className="empty-note">Сначала — ваша компания и пожелания.<br/>Затем — план, который можно проверить.</div></div> : <>
          <h2>{shown.trip.destination==="Lisbon"?"Поездка в Лиссабон":"Поездка в демо-город"}</h2><p className="muted">{shown.trip.travel_date || "Дата не задана"}{shown.trip.end_date && shown.trip.end_date!==shown.trip.travel_date ? ` — ${shown.trip.end_date}` : ""} · {shown.route.algorithm}</p>
          <div className={`verdict ${result!.is_feasible?"valid":"invalid"}`}>{result!.is_feasible?"✓ Маршрут прошёл проверку":"! Найдены ограничения — маршрут не выполним"}</div>
          <div className="stats"><div><small>Входные билеты</small><strong>{money(result!.total_cost)}</strong></div><div><small>Бюджет группы</small><strong>{money(shown.trip.budget)}</strong></div><div><small>Мест в плане</small><strong>{result!.visits.length}</strong></div></div>
          <RouteDays route={shown.route} trip={shown.trip} names={resultPois}/>
          {!!result!.reasons.length && <div className="message error">{result!.reasons.map(r=><p key={r}>{reasonNames[r.split(":")[0]] || r}</p>)}</div>}
          <h3 className="subheading">Интересы участников</h3>{shown.trip.participants_snapshot.map(m=><div className="satisfaction" key={m.user_id}><span>{m.name}</span><meter min="0" max="1" value={result!.satisfaction[String(m.user_id)]||0}/><b>{Math.round((result!.satisfaction[String(m.user_id)]||0)*100)}%</b></div>)}
          <p className="hint">Средний интерес к категориям посещённых мест. Это оценка предпочтений, а не гарантия впечатлений.</p>
          <details className="checks"><summary>Проверки и происхождение данных</summary><p>{result!.validation?`${result!.validation.is_valid?"Пройдены":"Есть нарушения"} · пересчитанная стоимость ${money(result!.validation.computed_cost)}`:"Для старого результата отчёт отсутствует"}</p>{result!.validation?.violations.map((v,i)=><p key={i}>{reasonNames[v.code] || v.message}{v.place_id?` · ${resultPois[v.place_id]||v.place_id}`:""}</p>)}<p>{shown.route.data_source}</p><p>{result!.data_context?"Автомобильные переезды OSRM. Данные OSM могут быть неполными. По 60 минут на место — правило планирования. Транспорт, парковка, жильё и обратный путь не включены.":"Демонстрационные места и время переездов."}</p>{result!.data_context && <p>{result!.data_context.attribution} · Данные получены {new Date(result!.data_context.fetched_at).toLocaleString("ru-RU")}</p>}</details>
        </>}
      </section>
      {!!trips.length && <section className="card history"><h3>Сохранённые запросы</h3><p className="hint">Открывается последний расчёт выбранного запроса.</p>{[...trips].reverse().map(t=><button key={t.id} disabled={!!busy} onClick={()=>task("Открываем маршрут…",()=>showTrip(t))}><span>№{t.id} · {t.destination==="Lisbon"?"Лиссабон":"Демо-город"}<small>{t.travel_date||"Без даты"}{t.end_date && t.end_date!==t.travel_date ? ` — ${t.end_date}` : ""} · {money(t.budget)}</small></span><span>↗</span></button>)}</section>}
      </div></div><footer>попутно · Система группового планирования путешествий</footer>
    </section>
  </main>;
}
