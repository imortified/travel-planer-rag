import re

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.schemas.ages import AgeProfile
from app.schemas.groups import Name, PreferencesInput
from app.schemas.visa import TravelProfile


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=254)
    password: SecretStr

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(
            r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", value
        ):
            raise ValueError("Проверьте адрес электронной почты")
        return value

    @field_validator("password")
    @classmethod
    def password_length(cls, value):
        if not 10 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Пароль должен содержать от 10 до 128 символов")
        return value


class RegisterInput(LoginInput):
    name: Name


class AccountOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str


class SessionOutput(BaseModel):
    account: AccountOutput
    csrf_token: str


class TravellerInput(PreferencesInput):
    name: Name
    travel_profile: TravelProfile = Field(default_factory=TravelProfile)
    age_profile: AgeProfile = Field(default_factory=AgeProfile)


class TravellerOutput(TravellerInput):
    model_config = ConfigDict(from_attributes=True)
    id: int


class SavedProfileLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: int = Field(gt=0)
