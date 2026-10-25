import type { SavedTraveller } from "./api";

export function reuseTraveller(profile:SavedTraveller) {
  return {name:profile.name,profileId:profile.id,interests:{...profile.interests},
    travel_profile:{...profile.travel_profile},
    age_profile:{birth_date:profile.age_profile.birth_date,age_on_start:null}};
}
