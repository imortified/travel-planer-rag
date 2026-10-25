"use client";

import { useEffect, useState, type ReactNode, type FormEvent } from "react";
import { api, setAuthSession, type Account, type AuthSession } from "../../lib/api";
import { ThemeSwitcher } from "./theme-switcher";

export function AccountGate({children}:{children:(account:Account,onLogout:()=>Promise<void>)=>ReactNode}) {
  const [session,setSession]=useState<AuthSession|null>();
  const [mode,setMode]=useState<"login"|"register">("login");
  const [name,setName]=useState("");const [email,setEmail]=useState("");const [password,setPassword]=useState("");
  const [busy,setBusy]=useState(false);const [error,setError]=useState("");
  function accept(value:AuthSession|null){setAuthSession(value);setSession(value);setPassword("");setMode("login");}
  useEffect(()=>{
    let active=true;
    api<AuthSession>("/auth/me").then(value=>{if(active)accept(value);}).catch(()=>{if(active)accept(null);});
    const expired=()=>{accept(null);setError("Сессия завершилась. Войдите снова.");};
    window.addEventListener("travel-auth-expired",expired);
    return ()=>{active=false;window.removeEventListener("travel-auth-expired",expired);};
  },[]);
  async function submit(event:FormEvent){
    event.preventDefault();if(busy)return;setBusy(true);setError("");
    try{accept(await api<AuthSession>(`/auth/${mode}`,"POST",{email,password,...(mode==="register"?{name}: {})}));}
    catch(e){setError(e instanceof Error?e.message:"Не удалось войти");}
    finally{setBusy(false);}
  }
  async function logout(){await api("/auth/logout","POST");accept(null);}
  if(session===undefined)return <main className="auth-shell"><ThemeSwitcher corner/><p role="status">Проверяем вход…</p></main>;
  if(session)return children(session.account,logout);
  return <main className="auth-shell"><ThemeSwitcher corner/><section className="card auth-card"><a className="brand" href="/">попутно<span>↗</span></a>
    <p className="eyebrow">ВАШИ ПУТЕШЕСТВИЯ В ОДНОМ МЕСТЕ</p><h1>{mode==="login"?"С возвращением":"Начнём путешествие"}</h1>
    <p>Сохраняйте маршруты, возвращайтесь к поездкам и выбирайте участников из своей записной книжки.</p>
    <form onSubmit={submit}>
      <fieldset disabled={busy}>
      {mode==="register"&&<label>Ваше имя<input value={name} onChange={e=>setName(e.target.value)} autoComplete="name" required maxLength={100}/></label>}
      <label>Электронная почта<input type="email" value={email} onChange={e=>setEmail(e.target.value)} autoComplete="email" required maxLength={254}/></label>
      <label>Пароль<input type="password" value={password} onChange={e=>setPassword(e.target.value)} autoComplete={mode==="login"?"current-password":"new-password"} required minLength={10} maxLength={128}/></label>
      <p className="hint">От 10 до 128 символов. Участникам поездки отдельный аккаунт не нужен.</p>
      {error&&<p className="message error" role="alert">{error}</p>}
      <button className="primary" type="submit">{busy?"Подождите…":mode==="login"?"Войти":"Зарегистрироваться"}</button>
      </fieldset>
    </form>
    <button className="text-button" disabled={busy} onClick={()=>{setMode(mode==="login"?"register":"login");setError("");setPassword("");}}>{mode==="login"?"Нет аккаунта? Зарегистрироваться":"Уже есть аккаунт? Войти"}</button>
  </section></main>;
}
