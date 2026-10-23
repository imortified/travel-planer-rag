export type Interests = Record<string, number>;
export type Member = {user_id: number; name: string; interests: Interests};
export type Group = {id: number; name: string; members: Member[]};
export type Trip = {id: number; group_id: number; destination: string; travel_date: string | null; end_date?: string | null;
  budget: number; start: number; end: number; participants_snapshot: Member[]};
export type Poi = {id: string; name: string; eligible: boolean; issues: string[]; source_url?: string; available_dates?: string[];
  cost_per_person?: number | null; category?: string; duration?: number;
  provenance?: {checked_on: string; valid_until: string; price_source: string; hours_source: string; notes: string}};
export type Day = {travel_date: string; total_cost: number; visits: {place_id: string; arrival: number; start: number; end: number}[]};
export type Route = {id: number; trip_request_id: number; algorithm: string; data_source: string;
  result: {is_feasible: boolean; total_cost: number; satisfaction: Record<string, number>;
    days?: Day[] | null;
    reasons: string[]; visits: {place_id: string; arrival: number; start: number; end: number}[];
    validation: {is_valid: boolean; computed_cost: number; violations: {code: string; message: string; place_id: string | null}[]} | null;
    data_context: {pois: Poi[]; transport: string; fetched_at: string; attribution: string} | null}};

export async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {method, headers: {"Content-Type": "application/json"},
    body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store"});
  const data = await response.json();
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(Array.isArray(detail) ? detail.map((d: {msg: string}) => d.msg).join("; ")
      : typeof detail === "string" ? detail : `Ошибка API (${response.status})`);
  }
  return data as T;
}
export const clock = (minutes: number) => `${Math.floor(minutes / 60).toString().padStart(2,"0")}:${(minutes % 60).toString().padStart(2,"0")}`;
export const money = (cents: number) => new Intl.NumberFormat("ru-RU", {style:"currency",currency:"EUR"}).format(cents / 100);
export const minutes = (value: string) => {const [h,m] = value.split(":").map(Number); return h*60+m;};
