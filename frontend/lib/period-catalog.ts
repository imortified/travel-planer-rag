import type { CatalogResponse, CatalogSnapshot, Poi } from "./api";

export type CatalogContext = {destination:string;date:string;endDate:string};
export type LoadedCatalog = {key:string;pois:Poi[];snapshot?:CatalogSnapshot};

function calendarDate(value:string):number {
  if(!/^\d{4}-\d{2}-\d{2}$/.test(value))throw new Error("Укажите полную дату поездки с годом.");
  const stamp=Date.parse(`${value}T00:00:00Z`);
  if(!Number.isFinite(stamp)||new Date(stamp).toISOString().slice(0,10)!==value)throw new Error("Проверьте календарную дату поездки.");
  return stamp;
}

export function periodCatalogKey(context:CatalogContext):string {
  if(context.destination==="Demo City")return "Demo City";
  if(!context.date)throw new Error("Укажите дату начала поездки в разделе «Параметры поездки».");
  const first=calendarDate(context.date),last=calendarDate(context.endDate||context.date);
  if(last<first||(last-first)/86400000>13)throw new Error("Период должен содержать от 1 до 14 дней.");
  return `${context.destination}:${context.date}:${context.endDate||context.date}`;
}

export async function ensurePeriodCatalog(
  context:CatalogContext,
  loaded:LoadedCatalog,
  fetchCatalog:(path:string)=>Promise<CatalogResponse>,
  force=false,
  now=Date.now(),
):Promise<LoadedCatalog> {
  const key=periodCatalogKey(context);
  if(key==="Demo City")return loaded;
  if(!force&&loaded.key===key&&loaded.snapshot?.status==="ready"&&Date.parse(loaded.snapshot.fresh_until)>now)return loaded;
  const params=new URLSearchParams({travel_date:context.date});
  if(context.endDate)params.set("end_date",context.endDate);
  const response=await fetchCatalog(`/destinations/${encodeURIComponent(context.destination)}/pois?${params}`);
  const snapshot=response.catalog_snapshot;
  if(snapshot?.status!=="ready"||!Number.isFinite(Date.parse(snapshot.fresh_until))||Date.parse(snapshot.fresh_until)<=now) {
    throw new Error("Каталог получен, но его исходные данные устарели. Нужно обновить каталог на сервере.");
  }
  return {key,pois:response.pois,snapshot};
}
