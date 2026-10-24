import assert from "node:assert/strict";
import test from "node:test";
import { visitPrice } from "../lib/visit-prices.ts";

const visit={place_id:"museum",arrival:600,start:600,end:660};
const context=cost=>({pois:[{id:"museum",cost_per_person:cost}]});

test("saved visit price takes precedence and uses frozen group size",()=>{
  assert.deepEqual(visitPrice({...visit,cost_per_person:800,group_cost:2400},context(1200),3),{perPerson:800,group:2400});
  assert.equal(visitPrice({...visit,cost_per_person:800,group_cost:2400},context(1200),2),null);
});
test("historical snapshot prices are date-specific and override POI metadata",()=>{
  const first={...context(999),places_snapshot:[{id:"museum",cost_per_person:600}]};
  const second={...context(999),places_snapshot:[{id:"museum",cost_per_person:900}]};
  assert.deepEqual(visitPrice(visit,first,2),{perPerson:600,group:1200});
  assert.deepEqual(visitPrice(visit,second,2),{perPerson:900,group:1800});
  assert.deepEqual(visitPrice(visit,context(850),2),{perPerson:850,group:1700});
});
test("zero is a free visit, while missing data is never treated as free",()=>{
  assert.deepEqual(visitPrice(visit,context(0),2),{perPerson:0,group:0});
  assert.deepEqual(visitPrice({...visit,cost_per_person:0,group_cost:0},null,1),{perPerson:0,group:0});
  assert.equal(visitPrice(visit,null,2),null);
  assert.equal(visitPrice(visit,{pois:[]},2),null);
  assert.equal(visitPrice(visit,{...context(900),places_snapshot:[{id:"museum",cost_per_person:null}]},2),null);
});
test("invalid or inconsistent amounts and counts do not produce a price",()=>{
  for (const cost of [null,undefined,-1,NaN,Infinity,1.5,"800",Number.MAX_SAFE_INTEGER+1]) {
    assert.equal(visitPrice(visit,context(cost),2),null);
  }
  for (const count of [0,-1,NaN,1.5]) assert.equal(visitPrice(visit,context(800),count),null);
  assert.equal(visitPrice({...visit,cost_per_person:-1,group_cost:-2},context(800),2),null);
  assert.equal(visitPrice({...visit,group_cost:900},context(800),2),null);
});
