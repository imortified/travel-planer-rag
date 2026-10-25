"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { themes, validTheme, themeStorageKey, type Theme } from "../../lib/themes";

const ThemeContext=createContext<{theme:Theme;choose:(theme:Theme)=>void}>({theme:"warm",choose:()=>{}});

export function ThemeProvider({children}:{children:ReactNode}) {
  const [theme,setTheme]=useState<Theme>("warm");
  useEffect(()=>{
    setTheme(validTheme(document.documentElement.dataset.theme));
    const sync=(event:StorageEvent)=>{
      if(event.key!==themeStorageKey&&event.key!==null)return;
      const value=validTheme(event.newValue);document.documentElement.dataset.theme=value;setTheme(value);
    };
    window.addEventListener("storage",sync);return ()=>window.removeEventListener("storage",sync);
  },[]);
  function choose(value:Theme) {
    document.documentElement.dataset.theme=value;setTheme(value);
    try{localStorage.setItem(themeStorageKey,value);}catch{/* The current tab still works when storage is unavailable. */}
  }
  return <ThemeContext.Provider value={{theme,choose}}>{children}</ThemeContext.Provider>;
}

export function ThemeSwitcher({corner=false}:{corner?:boolean}) {
  const {theme,choose}=useContext(ThemeContext);
  return <div className={`theme-switcher${corner?" theme-corner":""}`} role="group" aria-label="Цветовая тема">
    {themes.map(option=><button key={option.id} type="button" className="theme-choice" data-choice={option.id}
      aria-label={`${option.name} тема`} aria-pressed={theme===option.id}
      title={`${option.name} · ${option.description}`} onClick={()=>choose(option.id)}>
      <span className="theme-swatch" aria-hidden="true"/>
    </button>)}
  </div>;
}
