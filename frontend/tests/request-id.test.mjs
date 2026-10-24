import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import test from "node:test";
import { correlationId, validRequestId } from "../lib/request-id.ts";

test("valid canonical UUID is preserved through proxy",()=>{
  const id=randomUUID();
  assert.equal(correlationId(id.toUpperCase()),id);
  assert.equal(validRequestId(id),id);
});
test("invalid, combined and private request IDs are replaced",()=>{
  for(const id of [null,"", "private-token", "\r\nInjected:value", "x".repeat(2000), `${randomUUID()}, ${randomUUID()}`]) {
    assert.equal(validRequestId(id),null);
    const replacement=correlationId(id);
    assert.equal(validRequestId(replacement),replacement);
  }
});
