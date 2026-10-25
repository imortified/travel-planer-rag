import test from "node:test";
import assert from "node:assert/strict";
import { amount,costPayload,defaultCostDraft } from "../lib/trip-costs.ts";

test("estimates preserve unknown, explicit zero and integer cents",()=>{
  assert.equal(amount("","lodging",true),null);
  assert.equal(amount("0","lodging",true),0);
  assert.equal(amount("18,50","meal"),1850);
  for(const value of ["-1","1.234","NaN","Infinity","10000000.01",""])assert.throws(()=>amount(value,"money"));
});
test("disabled extras keep legacy budget scope and never add phantom meals",()=>{
  const draft=defaultCostDraft();
  assert.deepEqual(costPayload(draft,"Porto"),{budget_settings:null,meal_settings:null});
  draft.mealsEnabled=true;
  assert.equal(costPayload(draft,"Demo City").meal_settings,null);
});
test("serialize total-group estimates separately from per-person meal limits",()=>{
  const draft=defaultCostDraft();draft.budgetEnabled=true;draft.lodging="500";draft.reserve="100";draft.mealsEnabled=true;
  const result=costPayload(draft,"Porto");
  assert.deepEqual(result.budget_settings,{lodging:50000,transport:null,flights:null,transport_scope:"ground_only",reserve:10000});
  assert.deepEqual(result.meal_settings,{lunch:{start:720,end:900,duration:60,max_cost_per_person:2500}});
  assert.equal(draft.meals.lunch.maxCost,"25");
});
test("invalid meal windows and no selected meal cannot be submitted",()=>{
  const draft=defaultCostDraft();draft.mealsEnabled=true;draft.meals.lunch.end="12:30";
  assert.throws(()=>costPayload(draft,"Porto"));
  draft.meals.lunch.enabled=false;
  assert.throws(()=>costPayload(draft,"Porto"));
});
