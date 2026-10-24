import assert from "node:assert/strict";
import test from "node:test";
import { semanticSelection } from "../lib/semantic-search.ts";

const match = {place_id:"cafe",score:0.99};
const result = {category:null,reason:"uncertain",document_threshold:0.4,matches:[match]};

test("server prohibition suppresses matches and manual category together",()=>{
  for(const reason of ["unsupported_condition","negative_or_complex","multiple_interests","no_evidence"]) {
    const state=semanticSelection({...result,reason,can_apply:false},"food");
    assert.equal(state.blocked,true);
    assert.equal(state.category,"");
    assert.deepEqual(state.matches,[]);
  }
});

test("old negative responses cannot be applied even without new policy fields",()=>{
  assert.equal(semanticSelection({...result,category:"food",reason:"negative_or_complex"},"art").category,"");
});

test("clarification keeps explicit manual choice but never invents one",()=>{
  assert.equal(semanticSelection({...result,can_apply:true},"").category,"");
  assert.equal(semanticSelection({...result,can_apply:true},"nature").category,"nature");
  assert.equal(semanticSelection({...result,category:"art",can_apply:true},"food").category,"art");
  assert.equal(semanticSelection(undefined,"food").blocked,true);
});
