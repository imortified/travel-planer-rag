import assert from "node:assert/strict";
import test from "node:test";
import { ageProfile } from "../lib/ages.ts";
import { visitPrice } from "../lib/visit-prices.ts";

test("age form preserves zero, unknown ages and valid optional birth dates",()=>{
  assert.deepEqual(ageProfile(),{age_on_start:null,birth_date:null});
  assert.deepEqual(ageProfile({age_on_start:0}),{age_on_start:0,birth_date:null});
  assert.deepEqual(ageProfile({birth_date:"2024-02-29"}),{age_on_start:null,birth_date:"2024-02-29"});
  for(const age of [-1,121,1.5,NaN,"8",true])assert.throws(()=>ageProfile({age_on_start:age}));
  for(const birth of ["2025-02-29","2024-02-30","bad"])assert.throws(()=>ageProfile({birth_date:birth}));
  assert.throws(()=>ageProfile({age_on_start:8,birth_date:"2018-01-01"}));
});

const people=[{participant_id:"a",age:30,cost:3100,label:"Взрослый",reason:"verified_age_tariff"},{participant_id:"b",age:8,cost:1900,label:"Детский",reason:"verified_age_tariff"}];
const visit={place_id:"zoo",arrival:600,start:600,end:720,cost_per_person:null,group_cost:5000,participant_prices:people};
test("mixed tariffs sum the frozen named participants without inventing an average",()=>{
  assert.deepEqual(visitPrice(visit,null,2,["a","b"]),{perPerson:null,group:5000,individuals:people});
  assert.equal(visitPrice(visit,null,2,["a","c"]),null);
  assert.equal(visitPrice(visit,null,3),null);
  assert.equal(visitPrice({...visit,cost_per_person:2500},null,2),null);
});
test("invalid individual prices never silently fall back to the ordinary catalog fare",()=>{
  const context={pois:[{id:"zoo",cost_per_person:3100}]};
  for(const patch of [{group_cost:6200},{participant_prices:[people[0],people[0]]},{participant_prices:[]},{participant_prices:[people[0],{...people[1],cost:-1}]}])assert.equal(visitPrice({...visit,...patch},context,2),null);
});
