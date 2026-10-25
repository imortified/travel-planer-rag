import test from "node:test";
import assert from "node:assert/strict";
import { airportFor,pickFlight,validateFlightSearch } from "../lib/flights.ts";
import { costPayload,defaultCostDraft } from "../lib/trip-costs.ts";

test("flight observations never become an automatic total for the group",()=>{
  const option={option_id:"a".repeat(32),observed_price:8150};
  const observation={observation_id:"id",options:[option],expires_at:"2026-11-01T12:00:00Z"};
  const picked=pickFlight(observation,option,Date.parse("2026-11-01T11:00:00Z"));
  assert.deepEqual(picked.choice,{observation_id:"id",option_id:option.option_id});
  assert.throws(()=>pickFlight(observation,option,Date.parse(observation.expires_at)));
  assert.throws(()=>pickFlight(observation,{option_id:"bad"},0));
  const draft=defaultCostDraft();draft.budgetEnabled=true;draft.flights="300";draft.transport="50";
  const budget=costPayload(draft,"Lisbon").budget_settings;
  assert.equal(budget.flights,30000);assert.equal(budget.transport,5000);
  assert.equal(budget.transport_scope,"ground_only");
  draft.flights="";assert.equal(costPayload(draft,"Lisbon").budget_settings.flights,null);
  draft.flights="0";assert.equal(costPayload(draft,"Lisbon").budget_settings.flights,0);
});
test("airport and flight dates cover the trip; Sintra uses Lisbon airport",()=>{
  const input={destination:"Sintra",origin:"WAW",departure:"2026-11-15",return_date:"2026-11-22"};
  assert.equal(airportFor("Sintra"),"LIS");assert.equal(airportFor("Porto"),"OPO");
  assert.equal(validateFlightSearch(input,"2026-11-15","2026-11-22"),null);
  assert.ok(validateFlightSearch({...input,origin:"LIS"},"2026-11-15","2026-11-22"));
  assert.ok(validateFlightSearch({...input,departure:"2026-11-16"},"2026-11-15","2026-11-22"));
  assert.ok(validateFlightSearch({...input,return_date:"2026-11-21"},"2026-11-15","2026-11-22"));
});
