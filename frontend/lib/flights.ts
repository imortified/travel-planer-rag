export type FlightSearchInput = {destination:string;origin:string;departure:string;return_date:string|null;market:"ru"|"us";direct:boolean};
export type FlightChoice = {observation_id:string;option_id:string};
export type FlightOption = {option_id:string;origin:string;destination:string;origin_airport:string;destination_airport:string;departure_at:string;return_at:string|null;observed_price:number;currency:"EUR";airline:string;flight_number:string;transfers:number;return_transfers:number|null;search_url:string|null;source_found_at:null;segments_complete:false;duration_to:number|null;duration_back:number|null};
export type FlightObservation = {observation_id:string;search:FlightSearchInput;options:FlightOption[];status:"observations"|"empty";fetched_at:string;expires_at:string;source_found_at:null;group_total_confirmed:false;cache:{read:string};passengers:{participant_id:string;age_at_departure:number|null}[]};
export type PickedFlight = {observation:FlightObservation;option:FlightOption;choice:FlightChoice};
export type FlightSnapshot = {version:string;choice:FlightChoice;search:FlightSearchInput;option:FlightOption;passengers:FlightObservation["passengers"];fetched_at:string;expires_at:string;source_found_at:null;group_total_estimate:number|null;price_status:"user_estimate"|"unknown";group_total_confirmed:false;booking_status:"not_booked";schedule_adjusted:false;segments_complete:false};
export const airportFor=(destination:string)=>destination==="Porto"?"OPO":"LIS";
export type FlightPlace={type:"city"|"airport";code:string;name:string;country_name:string;city_code:string|null;city_name:string|null};
export function flightPlaceLabel(place:FlightPlace):string {
  return place.type==="city"?`${place.name} · все аэропорты (${place.code})`:`${place.name} (${place.code})${place.city_name?` · ${place.city_name}`:""}`;
}
export function validateFlightSearch(input:FlightSearchInput,first:string,last:string):string|null {
  if(!/^[A-Z]{3}$/.test(input.origin))return "Выберите город или аэропорт вылета из подсказок либо введите трёхбуквенный IATA-код вручную.";
  if(input.origin===airportFor(input.destination))return "Города или аэропорты вылета и назначения должны различаться.";
  if(!input.departure||!first||input.departure>first)return "Вылет должен быть не позже первого календарного дня поездки.";
  if(input.return_date && (input.return_date<input.departure||input.return_date<last))return "Обратный вылет должен быть не раньше последнего календарного дня поездки.";
  return null;
}
export function pickFlight(observation:FlightObservation,option:FlightOption,now=Date.now()):PickedFlight {
  if(!Number.isFinite(Date.parse(observation.expires_at))||Date.parse(observation.expires_at)<=now)throw new Error("Срок выбора перелёта истёк. Повторите поиск.");
  if(!observation.options.some(row=>row.option_id===option.option_id))throw new Error("Перелёта нет в результатах поиска.");
  return {observation,option,choice:{observation_id:observation.observation_id,option_id:option.option_id}};
}
export const departureLabel=(value:string)=>value.replace("T"," ").replace(/:00(?=[+-]|Z$)/,"");
