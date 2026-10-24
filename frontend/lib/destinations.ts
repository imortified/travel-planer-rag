export type Destination = "Demo City"|"Lisbon"|"Porto"|"Sintra";
export type DestinationInfo = {id:Destination;name:string;synthetic:boolean;country?:string;country_name?:string;timezone?:string;origin?:{name:string}};
export const destinationName = (id:string) => ({"Demo City":"Демо-город",Lisbon:"Лиссабон",Porto:"Порту",Sintra:"Синтра"}[id] || id);
