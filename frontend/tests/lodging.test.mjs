import assert from "node:assert/strict";
import test from "node:test";
import { lodgingNights,lodgingSelection,nextDate } from "../lib/lodging.ts";

test("stay nights use date differences, including leap dates and a checkout on the last trip day",()=>{
  assert.equal(nextDate("2028-02-28"),"2028-02-29");
  assert.equal(nextDate("2026-02-29"),"");
  assert.equal(lodgingNights("2026-10-25","2026-10-26"),1);
  assert.equal(lodgingNights("2026-10-25","2026-10-25"),null);
  assert.equal(lodgingNights("2026-10-25","2026-11-25"),null);
  const stay=lodgingSelection("osm:node:1001",2,"2026-10-26","2026-10-28","2","2026-10-26","2026-10-28");
  assert.equal(stay.rooms,2);
  assert.equal(stay.checkout,"2026-10-28");
});
test("invalid lodging dates, source identities and room counts never become a selection",()=>{
  const args=["osm:way:1001",1,"2026-10-26","2026-10-28","1","2026-10-26","2026-10-28"];
  for(const [index,value] of [[0,"fake"],[1,0],[2,"2026-10-27"],[3,"2026-10-27"],[4,"0"],[4,"1.5"],[4,"7"]]) {
    const changed=[...args];changed[index]=value;assert.throws(()=>lodgingSelection(...changed));
  }
});
