from pydantic import BaseModel, Field


class RegisterIn(BaseModel):
    organisation: str = Field(min_length=1, max_length=200)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(min_length=10, max_length=72)
    currency: str = "GBP"


class LoginIn(BaseModel):
    email: str
    password: str


class SourceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ApproveIn(BaseModel):
    mapping: dict[str, str]  # canonical field -> source column


class QuestionIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
