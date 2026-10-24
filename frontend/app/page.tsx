"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { RouteDays } from "./components/route-days";
import { PoiFacts } from "./components/poi-facts";
import { RouteComparison } from "./components/route-comparison";
import { TextRequest } from "./components/text-request";
import { RouteExplanation } from "./components/route-explanation";
import { SemanticInterests } from "./components/semantic-search";
import { TravelDocuments, VisaReport, emptyProfile } from "./components/visa-report";
import { RouteObjective } from "./components/route-objective";
import { SolverStatus } from "./components/solver-status";
import { RouteMap } from "./components/route-map";
import { CatalogStatus } from "./components/catalog-status";
import { destinationName, type DestinationInfo } from "../lib/destinations";
import { RoutePace, paceNames } from "./components/route-pace";
import type { PaceMode, PaceSettings } from "../lib/api";
import type { CatalogSnapshot, CatalogResponse } from "../lib/api";
import { api, clock, money, minutes, type Group, type Trip, type Route, type RouteSet, type Poi, type Interests, type ParseOutput, type TravelProfile, type VisaReport as VisaData } from "../lib/api";

type Draft = {key: number; name: string; interests: Interests; userId?: number; joined?: boolean; travel_profile?:TravelProfile};
type PendingConstraint = {key:string;name:string;kind:"mandatory"|"excluded"};
const normalized=(text:string)=>text.normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLocaleLowerCase().trim();
const categories = [["history", "История"], ["nature", "Природа"], ["food", "Еда"], ["art", "Искусство"]];
const demoPois: Poi[] = [{id:"museum",name:"Музей",eligible:true,issues:[]}, {id:"market",name:"Рынок",eligible:true,issues:[]}, {id:"park",name:"Парк",eligible:true,issues:[]}];
const issueNames: Record<string,string> = {unknown_price:"Нет цены", unknown_closed_or_unsupported_hours:"Нет подходящих часов", restricted_access:"Ограничен доступ", curation_outside_validity:"Данные требуют повторной проверки", unsupported_conditional_access_or_fee:"Условный доступ"};
issueNames.catalog_expired="Срок свежести каталога истёк";
issueNames.curation_review_expired="Срок ручной проверки истёк";
const reasonNames: Record<string,string> = {mandatory_excluded_conflict:"Место одновременно обязательно и запрещено", no_feasible_visits:"Не найдено допустимых посещений", mandatory_not_scheduled:"Обязательное место не включено", budget_exceeded:"Превышен бюджет", cost_mismatch:"Стоимость рассчитана неверно", duplicate_visit_across_days:"Место повторяется в разные дни", unreachable_arrival:"Недостаточно времени на переезд", outside_opening_hours:"Посещение вне часов работы", missing_travel_time:"Нет данных о переезде"};
reasonNames.cp_sat_unknown="Время поиска закончилось без решения; невозможность не доказана";
reasonNames.cp_sat_infeasible="Допустимого маршрута нет при заданных данных и ограничениях";
reasonNames.daily_visit_limit="Превышен лимит посещений за день";
reasonNames.daily_load_limit="Превышена дневная нагрузка выбранного темпа";
reasonNames.insufficient_rest="Недостаточно времени на перерыв между посещениями";

export default function Home() {
  const [name,setName] = useState("Наше путешествие");
  const [members,setMembers] = useState<Draft[]>([
    {key:1,name:"",interests:{history:1,nature:0.5,food:0.5,art:0.5}},
    {key:2,name:"",interests:{history:0.5,nature:1,food:0.5,art:0.5}},
  ]);
  const key = useRef(3);
  const [gid,setGid] = useState<number>();
  const [loadId,setLoadId] = useState("");
  const [destination,setDestination] = useState("Demo City");
  const [destinations,setDestinations] = useState<DestinationInfo[]>([{id:"Demo City",name:"Демо-город",synthetic:true},{id:"Lisbon",name:"Лиссабон",synthetic:false}]);
  const destinationInfo=destinations.find(city=>city.id===destination);
  const [date,setDate] = useState("");
  const [endDate,setEndDate] = useState("");
  const [purpose,setPurpose] = useState("tourism");
  const [visaPreview,setVisaPreview] = useState<{signature:string;report:VisaData}>();
  const visaInput={destination,travel_date:date || null,end_date:endDate || null,purpose,travellers:members.map(m=>({name:m.name.trim(),profile:m.travel_profile || emptyProfile}))};
  const visaSignature=JSON.stringify(visaInput);
  const [budget,setBudget] = useState("100");
  const [start,setStart] = useState("09:00");
  const [end,setEnd] = useState("18:00");
  const [algorithm,setAlgorithm] = useState("beam");
  const [pace,setPace] = useState<PaceMode>("balanced");
  const [paceProfiles,setPaceProfiles] = useState<Partial<Record<PaceMode,PaceSettings>>>({});
  const [solverLimit,setSolverLimit] = useState("5");
  const [pois,setPois] = useState<Poi[]>(demoPois);
  const [choices,setChoices] = useState<Record<string,string>>({});
  const [pendingConstraints,setPendingConstraints] = useState<PendingConstraint[]>([]);
  const [catalogKey,setCatalogKey] = useState("Demo City");
  const [catalogSnapshot,setCatalogSnapshot] = useState<CatalogSnapshot>();
  const [busy,setBusy] = useState("");
  const [error,setError] = useState("");
  const [notice,setNotice] = useState("");
  const [semanticQuery,setSemanticQuery] = useState("");
  const [trips,setTrips] = useState<Trip[]>([]);
  const [shown,setShown] = useState<{route: Route; trip: Trip}>();
  const [comparison,setComparison] = useState<{data: RouteSet; trip: Trip}>();
  const pendingTrip = useRef<{signature: string; trip: Trip} | undefined>(undefined);

  useEffect(() => { const stored = localStorage.getItem("travel-group-id"); if (stored) setLoadId(stored); }, []);
  useEffect(() => {let active=true;api<Record<string,PaceSettings>>("/planning/pace-profiles").then(profiles=>{if(active)setPaceProfiles(profiles);}).catch(()=>{});return ()=>{active=false;};}, []);
  useEffect(() => {let active=true;api<DestinationInfo[]>("/destinations").then(cities=>{if(active)setDestinations(cities);}).catch(()=>{});return ()=>{active=false;};}, []);
  function changeMember(index: number, patch: Partial<Draft>) {setMembers(ms => ms.map((m,i) => i===index ? {...m,...patch} : m));}
  function changeDate(value: string) {
    if (value === date) return;
    setDate(value); setChoices({});
    if (destination !== "Demo City") {setPois([]); setCatalogKey("");setCatalogSnapshot(undefined);}
  }
  function changeEndDate(value: string) {
    if (value === endDate) return;
    setEndDate(value); setChoices({});
    if (destination !== "Demo City") {setPois([]); setCatalogKey("");setCatalogSnapshot(undefined);}
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
      await api(`/groups/${id}/members/${m.userId}/travel-profile`,"PUT",m.travel_profile || emptyProfile);
    }
    return id;
  }
  async function loadGroup() {
    const id=Number(loadId); if (!Number.isInteger(id) || id<=0) throw new Error("Укажите положительный ID группы.");
    const group = await api<Group>(`/groups/${id}`);
    const history = await api<Trip[]>(`/groups/${id}/trips`);
    setGid(id); setName(group.name); setMembers(group.members.map(m=>({key:key.current++,name:m.name,userId:m.user_id,joined:true,interests:m.interests,travel_profile:m.travel_profile})));
    setTrips(history); setShown(undefined); setComparison(undefined); setSemanticQuery(""); pendingTrip.current=undefined;
    localStorage.setItem("travel-group-id",String(id)); setNotice(`Группа №${id} загружена`);
  }
  async function calculate() {
    if (!members.length) throw new Error("Добавьте хотя бы одного участника.");
    if (algorithm==="cp-sat" && (!solverLimit.trim() || !Number.isFinite(Number(solverLimit)) || Number(solverLimit)<0.05 || Number(solverLimit)>30)) throw new Error("Лимит CP-SAT должен быть от 0,05 до 30 секунд.");
    if (destination!=="Demo City" && !date) throw new Error("Выберите дату поездки.");
    if (destination!=="Demo City" && catalogKey!==`${destination}:${date}:${endDate || date}`) throw new Error("Сначала загрузите места для выбранной даты.");
    if (destination!=="Demo City" && catalogSnapshot && (catalogSnapshot.status==="expired" || Date.parse(catalogSnapshot.fresh_until)<=Date.now())) throw new Error("Срок свежести каталога истёк. Требуется обновление данных.");
    if (pendingConstraints.length) throw new Error("Сначала сопоставьте обязательные и исключённые места из текста с каталогом или явно уберите условие.");
    if (!Number.isFinite(Number(budget)) || Number(budget)<0 || Number(budget)>10000000 || !budget.trim()) throw new Error("Проверьте общий бюджет.");
    if (!start || !end || minutes(start)>=minutes(end)) throw new Error("Окончание дня должно быть позже начала.");
    if (endDate && (!date || endDate<date || (Date.parse(endDate)-Date.parse(date))/86400000>13)) throw new Error("Период должен содержать от 1 до 14 дней.");
    const id = await saveGroup();
    const payload={destination, purpose, pace, travel_date:date || null, end_date:endDate || null, semantic_query:destination!=="Demo City" && semanticQuery?semanticQuery:null, budget:Math.round(Number(budget)*100), start:minutes(start), end:minutes(end),
      mandatory:Object.keys(choices).filter(k=>choices[k]==="mandatory"), excluded:Object.keys(choices).filter(k=>choices[k]==="excluded")};
    const signature=JSON.stringify({id,payload,members:members.map(m=>({interests:m.interests,profile:m.travel_profile || emptyProfile}))});
    let trip = pendingTrip.current?.signature===signature ? pendingTrip.current.trip : undefined;
    if (!trip) {trip = await api<Trip>(`/groups/${id}/trips`,"POST",payload); pendingTrip.current={signature,trip}; setTrips(old=>[...old,trip!]);}
    const data = await api<RouteSet>(`/trips/${trip.id}/route-sets?algorithm=${algorithm}${algorithm==="cp-sat"?`&time_limit_seconds=${Number(solverLimit)}`:""}`,"POST");
    openComparison(data,trip); pendingTrip.current=undefined;
  }
  function openComparison(data: RouteSet, trip: Trip) {
    setComparison({data,trip});
    setShown(data.options.length ? {route:data.options[0],trip} : undefined);
  }
  async function applyParsed(parsed: ParseOutput) {
    const d=parsed.draft;
    if (parsed.questions.length || !d.destination || parsed.group_budget===null || !d.travel_date) throw new Error("Сначала уточните недостающие параметры.");
    if (gid && d.participants.some(p=>!members.some(m=>normalized(m.name)===normalized(p.name)))) throw new Error("Имена в тексте отличаются от состава сохранённой группы. Укажите текущих участников или создайте новую группу.");
    let catalog=demoPois;
    let snapshot:CatalogSnapshot|undefined;
    if (d.destination!=="Demo City") {
      const response=await api<CatalogResponse>(`/destinations/${d.destination}/pois?travel_date=${d.travel_date}${d.end_date?`&end_date=${d.end_date}`:""}`);
      catalog=response.pois;snapshot=response.catalog_snapshot;
    }
    setSemanticQuery("");
    const nextChoices:Record<string,string>={}, unresolved:PendingConstraint[]=[];
    const aliases:Record<string,string>={"музей":"museum","парк":"park","рынок":"market"};
    for (const kind of ["mandatory","excluded"] as const) for (const [index,placeName] of (kind==="mandatory"?d.mandatory_names:d.excluded_names).entries()) {
      const query=normalized(placeName);
      const matches=catalog.filter(p=>(kind==="excluded" || p.eligible) && (p.id===placeName || normalized(p.name)===query || aliases[query]===p.id || (query.length>=4 && normalized(p.name).includes(query))));
      if (matches.length===1) {
        const id=matches[0].id;
        if (nextChoices[id] && nextChoices[id]!==kind) throw new Error(`Место «${matches[0].name}» одновременно обязательно и исключено. Уточните текст.`);
        nextChoices[id]=kind;
      } else unresolved.push({key:`${kind}:${index}:${placeName}`,name:placeName,kind});
    }
    if (!gid && d.participants.length) setMembers(d.participants.map(p=>({key:key.current++,name:p.name,interests:{history:0.5,nature:0.5,food:0.5,art:0.5,...d.common_interests,...p.interests}})));
    else setMembers(ms=>ms.map(m=>({...m,interests:{...m.interests,...d.common_interests,...d.participants.find(p=>normalized(p.name)===normalized(m.name))?.interests}})));
    setDestination(d.destination);setDate(d.travel_date);setEndDate(d.end_date || "");setBudget(String(parsed.group_budget/100));
    if (d.start!==null) setStart(clock(d.start));
    if (d.end!==null) setEnd(clock(d.end));
    setPois(catalog);setCatalogKey(d.destination!=="Demo City"?`${d.destination}:${d.travel_date}:${d.end_date || d.travel_date}`:"Demo City");
    setCatalogSnapshot(snapshot);
    setChoices(nextChoices);setPendingConstraints(unresolved);pendingTrip.current=undefined;
    setNotice(unresolved.length?"Параметры заполнены. Сопоставьте оставшиеся названия мест с каталогом.":"Параметры заполнены. Проверьте форму перед расчётом маршрута.");
  }
  async function showTrip(trip: Trip) {
    const [routes,sets]=await Promise.all([api<Route[]>(`/trips/${trip.id}/routes`),api<RouteSet[]>(`/trips/${trip.id}/route-sets`)]);
    const lastSet=sets[sets.length-1], lastRoute=routes[routes.length-1];
    if (lastSet && (!lastRoute || lastRoute.route_set_id===lastSet.id || new Date(lastSet.created_at)>=new Date(lastRoute.created_at))) {
      openComparison(lastSet,trip); return;
    }
    if (!routes.length) {setNotice("В запросе ещё нет сохранённых маршрутов. Повторите расчёт с нужными параметрами."); return;}
    setComparison(undefined); setShown({route:lastRoute,trip});
  }
  const result=shown?.route.result;
  const resultPois=useMemo(()=>Object.fromEntries((result?.data_context?.pois || demoPois).map(p=>[p.id,p.name])),[result?.data_context?.pois]);

  return <main className="shell">
    <aside className="sidebar"><a className="brand" href="/">попутно<span>↗</span></a><p className="brand-note">Хорошая поездка начинается<br/>с общего плана.</p>
      <div className="nav-item active"><span>01</span> Планировщик</div><div className="nav-item"><span>02</span> Ваши поездки <b>{trips.length}</b></div>
      <div className="sidebar-bottom"><span className="dot"/> Локальная версия MVP<p>Общие интересы. Несколько дней.<br/>Проверяемый маршрут.</p></div>
    </aside>
    <section className="workspace"><header><span className="eyebrow">ПУТЕШЕСТВУЙТЕ ВМЕСТЕ</span><span className="header-tag">План на 1–14 дней</span></header>
      <div className="intro"><h1>Разные интересы.<br/><em>Один маршрут.</em></h1><p>Соберите компанию, расскажите о предпочтениях.<br/>Мы найдём места, на которые хватит времени и бюджета.</p></div>
      <div className="columns"><div className="form-column">
        <TextRequest disabled={!!busy} memberNames={members.map(m=>m.name.trim()).filter(Boolean)} onBusy={setBusy} onApply={applyParsed}/>
        <fieldset disabled={!!busy} className="card"><legend><span className="step">1</span> Ваша компания</legend>
          <div className="card-top"><label>Название группы<input value={name} maxLength={100} disabled={!!gid} onChange={e=>setName(e.target.value)} /></label>{gid && <span className="badge">Группа №{gid}</span>}</div>
          {members.map((m,i)=><div className="member" key={m.key}>
            <div className="member-heading"><span className="avatar">{i+1}</span><label className="member-name"><span className="sr-only">Имя участника {i+1}</span><input placeholder={`Имя участника ${i+1}`} value={m.name} disabled={!!m.userId} maxLength={100} onChange={e=>changeMember(i,{name:e.target.value})}/></label>{!m.userId && <button type="button" className="text-button" aria-label={`Удалить участника ${i+1}`} onClick={()=>setMembers(ms=>ms.filter(x=>x.key!==m.key))}>×</button>}</div>
            <div className="interests">{categories.map(([id,label])=><label key={id}>{label}<output>{Math.round((m.interests[id] || 0)*100)}%</output><input type="range" aria-label={`${label}, участник ${i+1}`} min="0" max="1" step="0.1" value={m.interests[id] || 0} onChange={e=>changeMember(i,{interests:{...m.interests,[id]:Number(e.target.value)}})} /></label>)}</div>
            <TravelDocuments profile={m.travel_profile} index={i} onChange={travel_profile=>changeMember(i,{travel_profile})}/>
          </div>)}
          <div className="actions"><button type="button" className="secondary" disabled={members.length>=6} onClick={()=>setMembers(ms=>[...ms,{key:key.current++,name:"",interests:{history:0.5,nature:0.5,food:0.5,art:0.5}}])}>＋ Участник</button><button className="text-button" onClick={()=>task("Сохраняем группу…",async()=>{const id=await saveGroup();setNotice(`Группа №${id} и предпочтения сохранены`);})}>Сохранить группу</button><small>{members.length} / 6</small></div>
          <details className="load"><summary>Продолжить с существующей группой</summary><div className="actions"><label>ID группы<input type="number" min="1" value={loadId} onChange={e=>setLoadId(e.target.value)}/></label><button className="secondary" onClick={()=>task("Загружаем группу…",loadGroup)}>Загрузить</button></div></details>
        </fieldset>
        <fieldset disabled={!!busy} className="card"><legend><span className="step">2</span> Параметры поездки</legend>
          <div className="two"><label>Куда отправимся<select value={destination} onChange={e=>{setDestination(e.target.value);setSemanticQuery("");setCatalogSnapshot(undefined);setChoices({});setPois(e.target.value==="Demo City"?demoPois:[]);setCatalogKey(e.target.value==="Demo City"?"Demo City":"");}}>{destinations.map(city=><option key={city.id} value={city.id}>{city.name} · {city.synthetic?"без сети":city.country_name || "Португалия"}</option>)}</select></label><label>Начало поездки<input type="date" value={date} onChange={e=>changeDate(e.target.value)} onInput={e=>changeDate(e.currentTarget.value)}/></label></div>
          <div className="two"><label>Конец поездки<input type="date" value={endDate} min={date || undefined} onChange={e=>changeEndDate(e.target.value)} onInput={e=>changeEndDate(e.currentTarget.value)}/></label><p className="hint">До 14 дней включительно. Без даты окончания — один день. Бюджет общий на весь период.</p></div>
          <div className="two"><label>Бюджет посещений на группу, EUR<input type="number" min="0" max="10000000" step="0.01" value={budget} onChange={e=>setBudget(e.target.value)}/></label><label>Алгоритм<select value={algorithm} onChange={e=>setAlgorithm(e.target.value)}><option value="beam">Beam search</option><option value="greedy">Greedy</option><option value="cp-sat">CP-SAT · OR-Tools</option></select></label></div>
          <label>Темп поездки<select value={pace} onChange={e=>setPace(e.target.value as PaceMode)}>{(["relaxed","balanced","intensive","unrestricted"] as PaceMode[]).map(mode=><option key={mode} value={mode}>{paceNames[mode]}</option>)}</select></label>
          {paceProfiles[pace] && <p className="hint">До {paceProfiles[pace]!.max_visits} мест и {paceProfiles[pace]!.max_active_minutes} мин нагрузки в день; между посещениями минимум {paceProfiles[pace]!.break_minutes} мин перерыва. Ожидание открытия считается отдельно.</p>}
          {algorithm==="cp-sat" && <><label>Лимит CP-SAT на набор, с<input type="number" min="0.05" max="30" step="0.05" value={solverLimit} onChange={e=>setSolverLimit(e.target.value)}/></label><p className="hint">Общий бюджет времени поиска вариантов. Загрузка данных и подготовка могут добавить время. На сложной задаче оптимум может остаться недоказанным.</p></>}
          <div className="two"><label>Начало дня<input type="time" value={start} onChange={e=>setStart(e.target.value)}/></label><label>Окончание дня<input type="time" value={end} onChange={e=>setEnd(e.target.value)}/></label></div>
          {destination!=="Demo City" && <div className="visa-preview"><label>Цель въезда<select value={purpose} onChange={e=>setPurpose(e.target.value)}><option value="tourism">Туризм</option><option value="other">Иная цель</option><option value="unknown">Не уточнена</option></select></label><p className="hint">Визовый справочник: семь гражданств, краткосрочный туризм в Португалии. Документы заполняются у каждого участника.</p><button className="secondary" disabled={!members.length} onClick={()=>task("Проверяем визовые условия…",async()=>{if(members.some(m=>!m.name.trim())) throw new Error("Введите имена участников.");const report=await api<VisaData>("/visa-check","POST",visaInput);setVisaPreview({signature:visaSignature,report});})}>Проверить визовые условия</button>{visaPreview?.signature===visaSignature && <VisaReport report={visaPreview.report}/>}</div>}
          <p className="hint">{destination!=="Demo City"?`Португалия · ${destinationName(destination)}. Старт — ${destinationInfo?.origin?.name || "точка города"}. Автомобильные переезды по доступным дорогам. Один город на поездку. Учитываются обычные взрослые билеты и указанные заказы в кафе. Остальное питание, дополнения, скидки и транспортные расходы не включены. Бронирование и доступность билетов не подтверждаются. Время — местное.`:"Синтетические музей, рынок и парк для проверки сценария. Бюджет учитывает входные билеты на всех участников."}</p>
          {destination!=="Demo City" && <button className="secondary" disabled={!date} onClick={()=>task("Загружаем каталог…",async()=>{const c=await api<CatalogResponse>(`/destinations/${destination}/pois?travel_date=${date}${endDate?`&end_date=${endDate}`:""}`);setPois(c.pois);setCatalogSnapshot(c.catalog_snapshot);setCatalogKey(`${destination}:${date}:${endDate || date}`);setNotice(`Найдено ${c.pois.length} мест, подходят для расчёта: ${c.pois.filter(p=>p.eligible).length}`);})}>Загрузить места на период</button>}
          {destination!=="Demo City" && <CatalogStatus snapshot={catalogSnapshot}/>}
          {!!pois.length && <details className="places"><summary>Места и ограничения <span>{pois.filter(p=>p.eligible).length} доступны</span></summary>{pois.map(p=><div className="poi" key={p.id}><div><b>{p.name}</b><PoiFacts poi={p} sources/>{p.eligible && p.available_dates && <small>Открыто: {p.available_dates.join(", ")}</small>}{!p.eligible && <small>{p.issues.map(x=>issueNames[x]||x).join(" · ")}</small>}</div><select aria-label={`Ограничение: ${p.name}`} value={choices[p.id]||"optional"} onChange={e=>setChoices(c=>({...c,[p.id]:e.target.value}))}><option value="optional">По желанию</option><option value="mandatory" disabled={!p.eligible}>Обязательно</option><option value="excluded">Исключить</option></select></div>)}</details>}
          {!!pendingConstraints.length && <div className="message"><b>Уточните места из текста</b>{pendingConstraints.map(item=><div className="pending-place" key={item.key}><label>{item.kind==="mandatory"?"Обязательно":"Исключить"}: {item.name}<select aria-label={`Сопоставить: ${item.name}`} value="" onChange={e=>{const id=e.target.value;if (!id) return;if(choices[id] && choices[id]!==item.kind){setError("Место уже имеет противоположное ограничение. Сначала измените его в каталоге.");return;}setChoices(c=>({...c,[id]:item.kind}));setPendingConstraints(old=>old.filter(p=>p.key!==item.key));}}><option value="">Выберите место из каталога</option>{pois.filter(p=>item.kind==="excluded" || p.eligible).map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label><button className="text-button" type="button" onClick={()=>setPendingConstraints(old=>old.filter(p=>p.key!==item.key))}>Убрать условие</button></div>)}</div>}
        </fieldset>
        {destination!=="Demo City" && <SemanticInterests key={destination} destination={destination} date={date} endDate={endDate} disabled={!!busy} members={members} selected={semanticQuery} onBusy={setBusy} onClear={()=>setSemanticQuery("")} onApply={(query,category,target)=>{setSemanticQuery(query);setMembers(ms=>ms.map(m=>target==="all" || String(m.key)===target?{...m,interests:{...m.interests,[category]:1}}:m));setNotice("Интерес применён. Проверьте веса участников перед расчётом.");}}/>}
        <div aria-live="polite">{error && <div role="alert" className="message error">{error}</div>}{notice && <div className="message">{notice}</div>}</div>
        <button className="primary" disabled={!!busy || !members.length} onClick={()=>task("Собираем маршрут…",calculate)}>{busy||"Построить маршрут"}<span aria-hidden="true">↗</span></button>
        <p className="under-button">До трёх разных вариантов. Запрос и все результаты сохраняются в группе.</p>
      </div>
      <div className="result-column">{comparison && <RouteComparison data={comparison.data} trip={comparison.trip} selectedId={shown?.route.id} names={Object.fromEntries(demoPois.map(p=>[p.id,p.name]))} reasonLabels={reasonNames} disabled={!!busy} onSelect={route=>setShown({route,trip:comparison.trip})}/>}
      {(!comparison || !!shown) && <section className="result card" aria-live="polite"><div className="result-header"><span className="eyebrow">ВАШ ПЛАН</span><span className="badge">{shown?`Маршрут №${shown.route.id}`:"Всё впереди"}</span></div>
        {!shown ? <div className="empty"><div className="journey-art" aria-hidden="true"><span>А</span><i/><span>Б</span><i/><span>В</span></div><h2>Место для новых<br/>впечатлений</h2><p>Здесь появится маршрут с временем<br/>посещений, переездами и стоимостью.</p><div className="empty-note">Сначала — ваша компания и пожелания.<br/>Затем — план, который можно проверить.</div></div> : <>
          <h2>{`Поездка: ${destinationName(shown.trip.destination)}`}</h2><p className="muted">{shown.trip.travel_date || "Дата не задана"}{shown.trip.end_date && shown.trip.end_date!==shown.trip.travel_date ? ` — ${shown.trip.end_date}` : ""} · {shown.route.algorithm}</p>
          <div className={`verdict ${result!.is_feasible?"valid":"invalid"}`}>{result!.is_feasible?"✓ Бюджет и расписание проверены":result!.solver?.status==="UNKNOWN"?"! За доступное время маршрут не найден; невозможность не доказана":"! Найдены ограничения — маршрут не выполним"}</div>
          <div className="stats"><div><small>Расходы на посещения</small><strong>{money(result!.total_cost)}</strong></div><div><small>Бюджет группы</small><strong>{money(shown.trip.budget)}</strong></div><div><small>Мест в плане</small><strong>{result!.visits.length}</strong></div></div>
          {result!.is_feasible && <RouteObjective report={result!.objective} trip={shown.trip}/>}
          <SolverStatus report={result!.solver}/>
          <RoutePace summary={result!.planning_summary}/>
          <CatalogStatus snapshot={result!.data_context?.catalog_snapshot} saved/>
          <RouteMap key={shown.route.id} route={shown.route} trip={shown.trip} names={resultPois}/>
          <RouteDays route={shown.route} trip={shown.trip} names={resultPois}/>
          {!!result!.reasons.length && <div className="message error">{result!.reasons.map(r=><p key={r}>{reasonNames[r.split(":")[0]] || r}</p>)}</div>}
          <h3 className="subheading">Интересы участников</h3>{shown.trip.participants_snapshot.map(m=><div className="satisfaction" key={m.user_id}><span>{m.name}</span><meter min="0" max="1" value={result!.satisfaction[String(m.user_id)]||0}/><b>{Math.round((result!.satisfaction[String(m.user_id)]||0)*100)}%</b></div>)}
          <p className="hint">Средний интерес к категориям посещённых мест. Это оценка предпочтений, а не гарантия впечатлений.</p>
          {shown.trip.destination!=="Demo City" && <VisaReport report={shown.route.visa_report}/>}
          {result!.is_feasible && result!.validation?.is_valid && <RouteExplanation route={shown.route} disabled={!!busy} onBusy={setBusy} onGenerated={value=>{setShown(old=>old?{...old,route:{...old.route,explanation:value}}:old);setComparison(old=>old?{...old,data:{...old.data,options:old.data.options.map(r=>r.id===shown.route.id?{...r,explanation:value}:r)}}:old);}}/>}
          <details className="checks"><summary>Проверки и происхождение данных</summary><p>{result!.validation?`${result!.validation.is_valid?"Пройдены":"Есть нарушения"} · пересчитанная стоимость ${money(result!.validation.computed_cost)}`:"Для старого результата отчёт отсутствует"}</p>{result!.validation?.violations.map((v,i)=><p key={i}>{reasonNames[v.code] || v.message}{v.place_id?` · ${resultPois[v.place_id]||v.place_id}`:""}</p>)}<p>{shown.route.data_source}</p><p>{result!.data_context?"Автомобильные переезды OSRM. Данные OSM могут быть неполными. Длительность посещений — правило планирования. Стоимость включает обычные взрослые билеты и явно описанные заказы в кафе. Остальное питание, дополнительные заказы, скидки, сборы, транспорт, парковка, жильё и обратный путь не включены.":"Демонстрационные места и время переездов."}</p>{result!.data_context && <p>{result!.data_context.attribution} · Данные получены {new Date(result!.data_context.fetched_at).toLocaleString("ru-RU")}</p>}{result!.data_context?.pois.filter(p=>p.provenance).map(p=><div key={p.id}><b>{p.name}</b><PoiFacts poi={p} sources/></div>)}</details>
        </>}
      </section>}
      {!!trips.length && <section className="card history"><h3>Сохранённые запросы</h3><p className="hint">Открывается последний расчёт со всеми вариантами. Для старых запросов — одиночный маршрут.</p>{[...trips].reverse().map(t=><button key={t.id} disabled={!!busy} onClick={()=>task("Открываем маршрут…",()=>showTrip(t))}><span>№{t.id} · {destinationName(t.destination)}<small>{t.travel_date||"Без даты"}{t.end_date && t.end_date!==t.travel_date ? ` — ${t.end_date}` : ""} · {money(t.budget)}</small></span><span>↗</span></button>)}</section>}
      </div></div><footer>попутно · Система группового планирования путешествий</footer>
    </section>
  </main>;
}
