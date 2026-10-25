import type { LodgingSelection } from "./api";

export const lodgingKinds:Record<string,string>={hotel:"Отель",hostel:"Хостел",guest_house:"Гостевой дом",apartment:"Апартаменты",motel:"Мотель"};
export function nextDate(value:string):string {
  if(!/^\d{4}-\d{2}-\d{2}$/.test(value))return "";
  const date=new Date(`${value}T12:00:00Z`);
  if(!Number.isFinite(date.getTime())||date.toISOString().slice(0,10)!==value)return "";
  date.setUTCDate(date.getUTCDate()+1);
  return date.toISOString().slice(0,10);
}
export function lodgingNights(checkin:string,checkout:string):number|null {
  if(!nextDate(checkin)||!nextDate(checkout))return null;
  const nights=(Date.parse(`${checkout}T12:00:00Z`)-Date.parse(`${checkin}T12:00:00Z`))/86400000;
  return Number.isInteger(nights)&&nights>=1&&nights<=30?nights:null;
}
export function lodgingSelection(propertyId:string,version:number,checkin:string,checkout:string,rooms:string,first:string,last:string):LodgingSelection {
  if(!lodgingNights(checkin,checkout))throw new Error("Укажите от 1 до 30 ночей проживания.");
  if(!nextDate(first)||!nextDate(last)||checkin>first||checkout<last)throw new Error("Даты проживания должны покрывать период поездки.");
  if(!/^\d+$/.test(rooms)||Number(rooms)<1||Number(rooms)>6)throw new Error("Укажите от 1 до 6 номеров.");
  if(!/^osm:(node|way):[1-9]\d*$/.test(propertyId)||!Number.isSafeInteger(version)||version<1)throw new Error("Повторно выберите объект из каталога.");
  return {property_id:propertyId,catalog_version_id:version,checkin,checkout,rooms:Number(rooms)};
}
