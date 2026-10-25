import assert from "node:assert/strict";
import test from "node:test";
import { baseEstimate } from "../lib/xotelo.ts";

test("only an explicit single-room nightly estimate can be copied to budget",()=>{
  const rate={nightly_base:10050,tax_reported:null,estimated_base_total:20100};
  assert.equal(baseEstimate(rate,2,1),20100);
  assert.equal(baseEstimate(rate,2,2),null);
  assert.equal(baseEstimate({...rate,estimated_base_total:10050},2,1),null);
  assert.equal(baseEstimate({...rate,nightly_base:-1},2,1),null);
  assert.equal(baseEstimate({...rate,nightly_base:1e9,estimated_base_total:2e9},2,1),null);
  assert.equal(rate.tax_reported,null);
});
