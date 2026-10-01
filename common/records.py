"""Validated, typed records shared by every retailer scraper."""
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from common.units import parse_abv, parse_volume_ml


class PackType(str, Enum):
    SINGLE = "single"
    PACK = "pack"
    CASE = "case"


class Retailer(str, Enum):
    DAN_MURPHYS = "dan_murphys"
    BWS = "bws"
    LIQUORLAND = "liquorland"


def utcnow():
    return datetime.now(timezone.utc)


class Listing(BaseModel):
    """A product as one retailer lists it."""

    retailer: Retailer
    retailer_sku: str
    url: str
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    abv: Optional[float] = Field(default=None, ge=0, le=100)
    unit_volume_ml: Optional[float] = Field(default=None, gt=0, le=20000)

    @field_validator("name", "retailer_sku", "url")
    @classmethod
    def not_blank(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @model_validator(mode="after")
    def fill_from_name(self):
        if self.unit_volume_ml is None:
            self.unit_volume_ml = parse_volume_ml(self.name)
        if self.abv is None:
            self.abv = parse_abv(self.name)
        return self


class PriceObservation(BaseModel):
    """One price option (single / pack / case) for a listing at a location."""

    pack_type: PackType
    units: int = Field(ge=1, le=100)
    price: float = Field(gt=0, lt=5000)
    member_only: bool = False
    location_key: str = "national"
    observed_at: datetime = Field(default_factory=utcnow)


class ScrapedProduct(BaseModel):
    listing: Listing
    prices: list[PriceObservation]


class Location(BaseModel):
    """The store/area a set of prices applies to."""

    retailer: Retailer
    store_id: str
    store_name: Optional[str] = None
    suburb: Optional[str] = None
    state: Optional[str] = None
    postcode: Optional[str] = None

    @property
    def location_key(self):
        return f"{self.retailer.value}:{self.store_id}"
