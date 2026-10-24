import { validRequestId } from "./request-id";

export type Interests = Record<string, number>;
export type SolverReport = {backend:string;version:string;status:string;time_limit_seconds:number;search_time_limit_seconds:number;
  elapsed_seconds:number;solver_seconds:number;seed:number;workers:number;scale:number;objective_error_bound:number;
  integer_model_optimal:boolean;model_objective:number|null;model_best_bound:number|null;original_upper_bound:number|null;
  absolute_gap_bound:number|null;warm_start:boolean};
export type ObjectiveReport = {version:string;weights:Record<string,number>;value:number;contributions:Record<string,number>;
  participant_utility:Record<string,number>;participant_coverage:Record<string,number>;utility_bounds:Record<string,number>;
  excluded_from_fairness:string[];mean_utility:number;reward_bound:number;reward:number;cost_ratio:number;travel_minutes:number;
  travel_ratio:number;fairness_gap:number;candidate_count:number;budget:number;available_minutes:number};
export type Explanation = {mode:"llm"|"template";model:string|null;verified:boolean;prompt_version:string;
  facts:Record<string,string>;selected_ids:string[];paragraphs:string[];fallback_reason:string|null;
  sources?:Record<string,SemanticMatch>};
export type SemanticMatch = {place_id:string;name:string;description:string;score:number;source_url:string;checked_on:string|null;description_kind:string};
export type SemanticSearch = {query:string;model:string;knowledge_version:string;category:string|null;document_threshold:number;
  category_scores:Record<string,number>;needs_clarification:boolean;reason:string;matches:SemanticMatch[]};
export type ParsedMember = {name:string;interests:Interests};
export type ParseOutput = {model:string;prompt_version:string;reference_date:string;group_budget:number|null;
  questions:{field:string;text:string}[];warnings:string[];
  draft:{destination:"Lisbon"|"Demo City"|null;travel_date:string|null;end_date:string|null;
    start:number|null;end:number|null;participant_count:number|null;participants:ParsedMember[];
    common_interests:Interests;mandatory_names:string[];excluded_names:string[]}};
export type TravelProfile = {citizenship:string|null;passport_kind:"unknown"|"ordinary"|"special";biometric:boolean|null;special_entry_basis:boolean|null;visa_status:"unknown"|"held"|"not_held"};
export type VisaReport = {version:string;assessed_on:string;checked_on:string;review_by:string;status:string;travel_date:string|null;end_date:string|null;limitations:string[];participants:{name:string;citizenship_name:string|null;requirement:string;status:string;summary:string;reasons:string[];sources:{title:string;url:string}[];timing:null|{days_until_trip:number;application_notice_days:number;recommended_apply_by:string;processing_days:number|null;extended_processing_days:number|null;source_url:string}}[]};
export type Member = {user_id: number; name: string; interests: Interests;travel_profile?:TravelProfile};
export type Group = {id: number; name: string; members: Member[]};
export type Trip = {id: number; group_id: number; destination: string; travel_date: string | null; end_date?: string | null;
  budget: number; start: number; end: number; participants_snapshot: Member[];semantic_query?:string|null;purpose?:string};
export type Poi = {id: string; name: string; eligible: boolean; issues: string[]; source_url?: string; available_dates?: string[];
  latitude?:number|null; longitude?:number|null;
  cost_per_person?: number | null; category?: string; duration?: number;
  provenance?: {checked_on: string; valid_until: string; price_source: string; hours_source: string; notes: string;price_policy?:string}};
export type CatalogSnapshot = {version_id:number;content_sha256:string;published_at:string;fetched_at:string;fresh_until:string;curated_version:string;curated_review_until:string;status:"ready"|"expired"};
export type CatalogResponse = {pois:Poi[];catalog_snapshot:CatalogSnapshot};
export type DataContext = {pois:Poi[]; origin?:{id:string;name:string;latitude:number;longitude:number}; transport:string; fetched_at:string; attribution:string;catalog_snapshot?:CatalogSnapshot};
export type Day = {travel_date: string; total_cost: number; visits: {place_id: string; arrival: number; start: number; end: number}[];data_context?:DataContext|null};
export type Route = {id: number; trip_request_id: number; algorithm: string; data_source: string; created_at: string;
  route_set_id?: number | null;
  explanation?: Explanation | null;
  visa_report?: VisaReport | null;
  comparison?: {visit_count: number; participant_utility: Record<string, number>; mean_utility: number; min_utility: number; utility_gap: number} | null;
  result: {is_feasible: boolean; total_cost: number; satisfaction: Record<string, number>;
    days?: Day[] | null;
    objective?: ObjectiveReport | null;
    solver?: SolverReport | null;
    reasons: string[]; visits: {place_id: string; arrival: number; start: number; end: number}[];
    validation: {is_valid: boolean; computed_cost: number; violations: {code: string; message: string; place_id: string | null}[]} | null;
    data_context: DataContext | null}};

export type RouteSet = {id: number; trip_request_id: number; algorithm: string; created_at: string;
  options: Route[]; diagnostic: Route["result"] | null;
  visa_report?: VisaReport | null;
  search_info: {method: string; attempts: number; attempt_limit: number; min_jaccard_distance: number; note_codes: string[];objective_version?:string|null}};

export async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {method, headers: {"Content-Type": "application/json"},
    body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store"});
  const data = await response.json();
  if (!response.ok) {
    const detail = data.detail;
    const message=Array.isArray(detail) ? detail.map((d: {msg: string}) => d.msg).join("; ")
      : typeof detail === "string" ? detail : `Ошибка API (${response.status})`;
    const requestId=validRequestId(response.headers.get("X-Request-ID"));
    throw new Error(message+(requestId?` Код запроса: ${requestId}.`:""));
  }
  return data as T;
}
export const clock = (minutes: number) => `${Math.floor(minutes / 60).toString().padStart(2,"0")}:${(minutes % 60).toString().padStart(2,"0")}`;
export const money = (cents: number) => new Intl.NumberFormat("ru-RU", {style:"currency",currency:"EUR"}).format(cents / 100);
export const minutes = (value: string) => {const [h,m] = value.split(":").map(Number); return h*60+m;};
