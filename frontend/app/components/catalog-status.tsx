import type { CatalogSnapshot } from "../../lib/api";

const timestamp = (value:string) => new Date(value).toLocaleString("ru-RU", {timeZone:"Europe/Lisbon"});

export function CatalogStatus({snapshot, saved=false}:{snapshot?:CatalogSnapshot;saved?:boolean}) {
  if (!snapshot) return null;
  const expired = snapshot.status === "expired" || (!saved && Date.parse(snapshot.fresh_until)<=Date.now());
  return <div className={`message${expired?" error":""}`}>
    <b>{saved?"Каталог при расчёте":"Опубликованный каталог"} · версия №{snapshot.version_id}</b>
    <p>Загружен {timestamp(snapshot.fetched_at)}. Срок свежести — до {timestamp(snapshot.fresh_until)} (местное время Португалии).</p>
    {snapshot.source_upstream_asof && <p>Картографические данные на {timestamp(snapshot.source_upstream_asof)}. Цены и расписания проверяются по отдельным источникам.</p>}
    <p>{snapshot.curated_validity_kind==="review_window"?`Повторная проверка цен и расписаний нужна до ${snapshot.curated_review_until}. Это срок свежести справочника, а не последняя дата поездки. Будущие цены и особые часы требуют подтверждения.`:`Ручная проверка цен и расписаний действует до ${snapshot.curated_review_until}; применимость каждого места зависит от даты поездки.`}</p>
    {saved?<p>Это сохранённый снимок. Обновление каталога не меняет этот маршрут.</p>
      :expired?<p>Срок свежести истёк. Для нового расчёта требуется обновление каталога.</p>
      :<p>Часы работы проверяются отдельно для каждого дня поездки.</p>}
  </div>;
}
