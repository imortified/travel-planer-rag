import assert from "node:assert/strict";
import test from "node:test";
import { reuseTraveller } from "../lib/travellers.ts";

test("reusing a profile copies preferences and documents without reusing a past trip age",()=>{
  const profile={id:7,name:"Анна",interests:{history:1},travel_profile:{citizenship:"DE"},age_profile:{birth_date:null,age_on_start:12}};
  const draft=reuseTraveller(profile);
  assert.equal(draft.age_profile.age_on_start,null);
  assert.equal(draft.profileId,7);
  assert.equal(draft.userId,undefined);
  draft.interests.history=0;draft.travel_profile.citizenship="BY";
  assert.equal(profile.interests.history,1);assert.equal(profile.travel_profile.citizenship,"DE");
});
test("birth date is preserved for age calculation on each visit",()=>{
  const draft=reuseTraveller({id:2,name:"Максим",interests:{},travel_profile:{},age_profile:{birth_date:"2015-12-31",age_on_start:null}});
  assert.deepEqual(draft.age_profile,{birth_date:"2015-12-31",age_on_start:null});
});
