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


CASE_MIN_UNITS = 12


def pack_type_for_units(units):
    """One rule for every retailer so the pack filter means the same thing
    everywhere: 1 = single, 2-11 = pack, 12 or more = case."""
    if units <= 1:
        return PackType.SINGLE
    return PackType.CASE if units >= CASE_MIN_UNITS else PackType.PACK


def clean_rating(rating, count):
    """(rating, review_count) as a retailer reports them -> what we store.
    No reviews (or a zero/garbage rating) means no rating, not 0 stars; an
    unknown count stays unknown."""
    try:
        count = None if count is None else int(count)
    except (TypeError, ValueError):
        count = None
    try:
        rating = None if rating is None else round(float(rating), 2)
    except (TypeError, ValueError):
        rating = None
    if count is not None and count < 0:
        count = None
    if rating is not None and not (0 < rating <= 5):
        rating = None
    if count == 0:
        rating = None
    return rating, count


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
    rating: Optional[float] = Field(default=None, gt=0, le=5)         # average stars; None = no reviews / unknown
    review_count: Optional[int] = Field(default=None, ge=0)          # None = this source doesn't say

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
