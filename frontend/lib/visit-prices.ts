import type { DataContext, ParticipantPrice, Poi, Visit } from "./api";

type Price = {perPerson:number|null;group:number;individuals?:ParticipantPrice[]};
const isPrice = (value:unknown):value is number => typeof value==="number" && Number.isSafeInteger(value) && value>=0;

export function poiPriceRange(poi:Poi):{min:number;max:number}|null {
  const prices=Object.values(poi.prices_by_date || {}).filter(isPrice);
  if (prices.length) return {min:Math.min(...prices),max:Math.max(...prices)};
  return isPrice(poi.cost_per_person)?{min:poi.cost_per_person,max:poi.cost_per_person}:null;
}

export function visitPrice(visit:Visit, context:DataContext|null|undefined, participantCount:number,participantIds?:string[]):Price|null {
  if (!Number.isSafeInteger(participantCount) || participantCount<1) return null;
  if (visit.participant_prices!=null) {
    const people=visit.participant_prices;
    if(!Array.isArray(people)||people.length!==participantCount||people.some(p=>typeof p.participant_id!=="string"||!p.participant_id||!isPrice(p.cost)))return null;
    const ids=new Set(people.map(p=>p.participant_id));
    if(ids.size!==participantCount || (participantIds && (new Set(participantIds).size!==participantCount||participantIds.some(id=>!ids.has(id)))))return null;
    const total=people.reduce((sum,p)=>sum+p.cost,0);
    if(!isPrice(total)||!isPrice(visit.group_cost)||total!==visit.group_cost)return null;
    const prices=new Set(people.map(p=>p.cost));
    const uniform=prices.size===1?people[0].cost:null;
    if(visit.cost_per_person!=null && (uniform===null||visit.cost_per_person!==uniform))return null;
    return {perPerson:uniform,group:total,individuals:people};
  }
  // Saved per-visit prices take precedence over catalog metadata.
  if (visit.cost_per_person!=null || visit.group_cost!=null) {
    if (!isPrice(visit.cost_per_person)) return null;
    const group=visit.cost_per_person*participantCount;
    if (!isPrice(group) || (visit.group_cost!=null && visit.group_cost!==group)) return null;
    return {perPerson:visit.cost_per_person,group};
  }
  // Compatibility with historical routes: only the snapshot for this date is used.
  const place=context?.places_snapshot?.find(p=>p.id===visit.place_id)
    ?? context?.pois?.find(p=>p.id===visit.place_id);
  if (!isPrice(place?.cost_per_person)) return null;
  const group=place.cost_per_person*participantCount;
  return isPrice(group) ? {perPerson:place.cost_per_person,group}:null;
}
