import type { DataContext, Visit } from "./api";

type Price = {perPerson:number;group:number};
const isPrice = (value:unknown):value is number => typeof value==="number" && Number.isSafeInteger(value) && value>=0;

export function visitPrice(visit:Visit, context:DataContext|null|undefined, participantCount:number):Price|null {
  if (!Number.isSafeInteger(participantCount) || participantCount<1) return null;
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
