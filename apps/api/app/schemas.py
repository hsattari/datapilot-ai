from typing import Literal

from pydantic import BaseModel, Field


class RegisterIn(BaseModel):
    organisation: str = Field(min_length=1, max_length=200)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=10, max_length=72)
    currency: str = "GBP"


class LoginIn(BaseModel):
    email: str
    password: str
    code: str | None = Field(default=None, max_length=32)  # authenticator code or recovery code, if MFA is on


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ApproveIn(BaseModel):
    mapping: dict[str, str]  # canonical field -> source column
    role: Literal["fact_sales", "dim_product", "dim_customer"] = "fact_sales"
    replace_existing: bool = False   # sales only: discard every other file's rows instead of adding to them


class QuestionIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
