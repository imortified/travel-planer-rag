const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function validRequestId(value:string|null):string|null {
  return value && uuid.test(value) ? value.toLowerCase():null;
}

export function correlationId(value:string|null):string {
  return validRequestId(value) || crypto.randomUUID();
}
