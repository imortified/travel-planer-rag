import type { XoteloChoice, XoteloObservation, XoteloRate } from "./api";
export type PickedQuote={observation:XoteloObservation;rate:XoteloRate;choice:XoteloChoice};
export function baseEstimate(rate:XoteloRate,nights:number,rooms:number):number|null {
  if(rooms!==1||!Number.isInteger(nights)||nights<1||nights>30||!Number.isSafeInteger(rate.nightly_base)||rate.nightly_base<0)return null;
  const total=rate.nightly_base*nights;
  return Number.isSafeInteger(total)&&total<=1_000_000_000&&rate.estimated_base_total===total?total:null;
}
