import type { CatalogSnapshot } from "../../lib/api";

const timestamp = (value:string) => new Date(value).toLocaleString("ru-RU", {timeZone:"Europe/Lisbon"});

export function CatalogStatus({snapshot, saved=false}:{snapshot?:CatalogSnapshot;saved?:boolean}) {
  if (!snapshot) return null;
  const expired = snapshot.status === "expired" || (!saved && Date.parse(snapshot.fresh_until)<=Date.now());
  return <div className={`message${expired?" error":""}`}>
    <b>{saved?"Каталог при расчёте":"Опубликованный каталог"} · версия №{snapshot.version_id}</b>
    <p>Загружен {timestamp(snapshot.fetched_at)}. Срок свежести — до {timestamp(snapshot.fresh_until)} (Лиссабон).</p>
    <p>Ручная проверка цен и расписаний действует до {snapshot.curated_review_until}; применимость каждого места зависит от даты поездки.</p>
    {saved?<p>Это сохранённый снимок. Обновление каталога не меняет этот маршрут.</p>
      :expired?<p>Срок свежести истёк. Для нового расчёта требуется обновление каталога.</p>
      :<p>Часы работы проверяются отдельно для каждого дня поездки.</p>}
  </div>;
}
