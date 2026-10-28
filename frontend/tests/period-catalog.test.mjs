import assert from "node:assert/strict";
import test from "node:test";
import { ensurePeriodCatalog,periodCatalogKey } from "../lib/period-catalog.ts";

const context={destination:"Lisbon",date:"2026-11-05",endDate:"2026-11-10"};
const now=Date.parse("2026-10-28T16:00:00Z");
const snapshot={status:"ready",fresh_until:"2026-10-30T00:00:00Z"};
const response={pois:[{id:"museum"}],catalog_snapshot:snapshot};
const empty={key:"",pois:[]};

test("calculating after a date change loads the exact period without a separate button",async()=>{
  const calls=[];
  const loaded=await ensurePeriodCatalog(context,empty,async path=>{calls.push(path);return response;},false,now);
  assert.deepEqual(calls,["/destinations/Lisbon/pois?travel_date=2026-11-05&end_date=2026-11-10"]);
  assert.equal(loaded.key,"Lisbon:2026-11-05:2026-11-10");
  assert.equal(loaded.pois,response.pois);
});

test("a matching fresh catalog can be reused, but changed dates and manual reload refetch",async()=>{
  let calls=0;
  const fetch=async()=>{calls++;return response;};
  const loaded={...empty,key:periodCatalogKey(context),snapshot};
  assert.equal(await ensurePeriodCatalog(context,loaded,fetch,false,now),loaded);
  await ensurePeriodCatalog({...context,date:"2026-11-06"},loaded,fetch,false,now);
  await ensurePeriodCatalog(context,loaded,fetch,true,now);
  assert.equal(calls,2);
});

test("missing and invalid dates give an actionable error before any request",async()=>{
  let calls=0;
  for(const patch of [{date:""},{date:"2026-02-30"},{endDate:"2026-11-04"},{endDate:"2026-11-19"}]) {
    await assert.rejects(ensurePeriodCatalog({...context,...patch},empty,async()=>{calls++;return response;},false,now));
  }
  assert.equal(calls,0);
});

test("a failed or stale source never becomes a matching loaded catalog",async()=>{
  await assert.rejects(ensurePeriodCatalog(context,empty,async()=>{throw new Error("Network failed");},false,now),/Network failed/);
  await assert.rejects(ensurePeriodCatalog(context,empty,async()=>({...response,catalog_snapshot:{...snapshot,status:"expired"}}),false,now),/устарели/);
  assert.deepEqual(empty,{key:"",pois:[]});
});

test("demo mode needs neither dates nor a network request",async()=>{
  assert.equal(await ensurePeriodCatalog({...context,destination:"Demo City",date:""},empty,async()=>{throw new Error("Unexpected request");}),empty);
});
