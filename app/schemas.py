"""Pydantic schemas for request/response validation."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Admin / auth schemas
# ---------------------------------------------------------------------------

class AdminBase(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)

    @field_validator("username")
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username must be alphanumeric (underscores and hyphens allowed)")
        return v


class AdminCreate(AdminBase):
    password: str = Field(..., min_length=8, max_length=128)


class AdminUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=64)
    password: Optional[str] = Field(None, min_length=8, max_length=128)


class AdminOut(AdminBase):
    id: int
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# Generic response schemas
# ---------------------------------------------------------------------------

class Message(BaseModel):
    detail: str
