import type { DataContext, Route, Trip } from "./api";

export type Coordinates = [number, number]; // GeoJSON: longitude, latitude.
export type MapStop = {key:string;id:string;name:string;order:number;day:number;start:number;end:number;coordinates:Coordinates|null};
export type MapDay = {day:number;date:string;color:string;origin:{name:string;coordinates:Coordinates}|null;stops:MapStop[];segments:Coordinates[][]};
const colors=["#315c50","#a45232","#455c9b","#86623d","#795080","#357c86","#636b31"];

export function coordinates(point?:{longitude?:number|null;latitude?:number|null}|null):Coordinates|null {
  if(!point || typeof point.longitude!=="number" || typeof point.latitude!=="number" || !Number.isFinite(point.longitude) || !Number.isFinite(point.latitude) || Math.abs(point.longitude)>180 || Math.abs(point.latitude)>85.051129) return null;
  return [point.longitude,point.latitude];
}

export function routeMapDays(route:Route,trip:Trip,names:Record<string,string>={}):MapDay[] {
  const days=route.result.days ?? [{travel_date:trip.travel_date || "",visits:route.result.visits,data_context:route.result.data_context}];
  return days.map((day,index)=>{
    // A day's own snapshot is authoritative. Never fill its missing POI from another day.
    const context:DataContext|null|undefined=day.data_context ?? route.result.data_context;
    const pois=new Map((context?.pois || []).map(p=>[p.id,p]));
    const originCoordinates=coordinates(context?.origin);
    const origin=originCoordinates ? {name:context!.origin!.name,coordinates:originCoordinates}:null;
    const stops=day.visits.map((v,i)=>({key:`${index}:${i}:${v.place_id}`,id:v.place_id,name:pois.get(v.place_id)?.name || names[v.place_id] || v.place_id,order:i+1,day:index+1,start:v.start,end:v.end,coordinates:coordinates(pois.get(v.place_id))}));
    const segments:Coordinates[][]=[];
    let segment:Coordinates[]=origin ? [origin.coordinates]:[];
    for(const stop of stops) {
      if(stop.coordinates) segment.push(stop.coordinates);
      else {if(segment.length>1) segments.push(segment);segment=[];}
    }
    if(segment.length>1) segments.push(segment);
    return {day:index+1,date:day.travel_date,color:colors[index%colors.length],origin,stops,segments};
  });
}

export function visibleDays(days:MapDay[],selected:number|null):MapDay[] {
  return selected===null ? days:days.filter(d=>d.day===selected);
}
