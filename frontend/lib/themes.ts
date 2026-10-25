export const themes = [
  {id:"warm",name:"Тёплая",description:"Молочный и терракотовый"},
  {id:"cool",name:"Холодная",description:"Белый и светло-голубой"},
  {id:"dark",name:"Тёмная",description:"Графитовый и голубой"},
] as const;
export type Theme = typeof themes[number]["id"];
export const themeStorageKey = "travel-theme";

export function validTheme(value:unknown):Theme {
  return themes.some(theme=>theme.id===value)?value as Theme:"warm";
}

// Runs before paint; only known IDs reach the DOM, never raw storage content.
export const themeBootstrap = `(()=>{let theme="warm";try{const saved=localStorage.getItem(${JSON.stringify(themeStorageKey)});if(${JSON.stringify(themes.map(t=>t.id))}.includes(saved))theme=saved;}catch{}document.documentElement.dataset.theme=theme;})();`;
