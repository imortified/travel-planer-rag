import assert from "node:assert/strict";
import test from "node:test";
import { coordinates, routeMapDays, visibleDays } from "../lib/route-map.ts";

const visit=id=>({place_id:id,arrival:550,start:560,end:620});
const poi=(id,longitude=-9.14,latitude=38.72)=>({id,name:`Место ${id}`,longitude,latitude});
const context=pois=>({pois,origin:{id:"start",name:"Rossio",longitude:-9.1395,latitude:38.7135}});
const trip={destination:"Lisbon",travel_date:"2026-10-24"};
const route=(visits,ctx=null,days=null)=>({result:{visits,data_context:ctx,days}});

test("coordinates use longitude first, preserve zero and reject invalid values",()=>{
  assert.deepEqual(coordinates(poi("a")),[-9.14,38.72]);
  assert.deepEqual(coordinates(poi("zero",0,0)),[0,0]);
  for(const p of [undefined,null,{},poi("a",NaN),poi("a",Infinity),poi("a",181),poi("a",-181),poi("a",1,90),{longitude:"1",latitude:2}]) assert.equal(coordinates(p),null);
});
test("saved order, names, time and start drive the map without changing the route",()=>{
  const input=route([visit("b"),visit("a")],context([poi("a"),poi("b")]));
  const before=JSON.stringify(input);const [day]=routeMapDays(input,trip);
  assert.deepEqual(day.stops.map(s=>s.id),["b","a"]);
  assert.equal(day.stops[0].start,560);assert.equal(day.stops[0].name,"Место b");
  assert.deepEqual(day.segments[0][0],[-9.1395,38.7135]);
  assert.equal(day.segments[0].length,3); // No invented return leg.
  assert.equal(JSON.stringify(input),before);
});
test("missing coordinates break the segment instead of connecting across unknown places",()=>{
  const [day]=routeMapDays(route([visit("a"),visit("missing"),visit("c"),visit("d")],context([poi("a"),poi("c"),poi("d")])),trip);
  assert.deepEqual(day.segments.map(s=>s.length),[2,2]);
  assert.equal(day.stops[1].coordinates,null);assert.equal(day.stops[3].order,4);
});
test("daily coordinates override overview; missing POI is not borrowed from another day",()=>{
  const input=route([visit("a")],context([poi("a",-9.9)]),[
    {travel_date:"2026-10-24",visits:[visit("a")],data_context:context([poi("a",-9.1)])},
    {travel_date:"2026-10-25",visits:[visit("a")],data_context:context([])},
  ]);
  const days=routeMapDays(input,trip);
  assert.deepEqual(days[0].stops[0].coordinates,[-9.1,38.72]);
  assert.equal(days[1].stops[0].coordinates,null);
  assert.notEqual(days[0].stops[0].key,days[1].stops[0].key);
});
test("empty days remain selectable; filtering preserves original day numbering",()=>{
  const days=routeMapDays(route([],context([]),[
    {travel_date:"2026-10-24",visits:[]},{travel_date:"2026-10-25",visits:[visit("a")],data_context:context([poi("a")])},
  ]),trip);
  assert.equal(visibleDays(days,1)[0].stops.length,0);
  assert.equal(visibleDays(days,2)[0].stops[0].day,2);
  assert.equal(visibleDays(days,null).length,2);
});
test("old/demo results keep a nongeographic list and names without invented coordinates",()=>{
  const [day]=routeMapDays(route([visit("museum")]),{destination:"Demo City",travel_date:null},{museum:"Музей"});
  assert.equal(day.origin,null);assert.equal(day.stops[0].coordinates,null);
  assert.equal(day.stops[0].name,"Музей");assert.deepEqual(day.segments,[]);
});
test("an alternative contains only its own saved visits",()=>{
  const ctx=context([poi("a"),poi("b")]);
  const primary=routeMapDays(route([visit("a")],ctx),trip);
  const alternative=routeMapDays(route([visit("b")],ctx),trip);
  assert.deepEqual(primary[0].stops.map(s=>s.id),["a"]);
  assert.deepEqual(alternative[0].stops.map(s=>s.id),["b"]);
});
test("missing origin creates no extra leg and a single POI needs no line",()=>{
  const [day]=routeMapDays(route([visit("a")],{pois:[poi("a")]}),trip);
  assert.equal(day.origin,null);assert.deepEqual(day.segments,[]);
  assert.equal(day.stops.length,1);
});
