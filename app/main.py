"""Beeroo web app: JSON API + static single-page frontend.

    uvicorn app.main:app --reload            # uses $BEEROO_DB or data/beeroo.sqlite3
"""
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import queries
from common import db

STATIC = Path(__file__).parent / "static"
POSTCODE_RE = re.compile(r"^\d{4}$")


def create_app(db_path=None):
    db_path = db_path or os.environ.get("BEEROO_DB", "data/beeroo.sqlite3")
    app = FastAPI(title="Beeroo", docs_url="/api/docs", openapi_url="/api/openapi.json")

    def connection():
        return db.connect(db_path)

    def checked_postcode(postcode):
        if not POSTCODE_RE.match(postcode):
            raise HTTPException(400, "Enter a valid 4-digit Australian postcode.")
        return postcode

    @app.get("/api/locations")
    def locations(postcode: str):
        conn = connection()
        try:
            return queries.resolve_locations(conn, checked_postcode(postcode))
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            conn.close()

    @app.get("/api/compare")
    def compare(
        postcode: str,
        q: str = "",
        sort: str = "value",
        pack: Optional[str] = None,
        include_member: bool = False,
        retailer: list[str] = Query(default=[]),
        min_abv: Optional[float] = Query(default=None, ge=0, le=100),
        max_abv: Optional[float] = Query(default=None, ge=0, le=100),
        min_retailers: int = Query(default=1, ge=1, le=3),
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ):
        unknown = set(retailer) - set(queries.RETAILERS)

        if unknown:
            raise HTTPException(400, f"unknown retailer(s): {sorted(unknown)}")

        conn = connection()
        try:
            return queries.compare(
                conn, checked_postcode(postcode), q=q, sort=sort, pack_type=pack,
                include_member=include_member, retailers=retailer or None,
                min_abv=min_abv, max_abv=max_abv, min_retailers=min_retailers,
                limit=limit, offset=offset,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            conn.close()

    @app.get("/api/products/{product_id}")
    def product(product_id: int, postcode: str):
        conn = connection()
        try:
            detail = queries.product_detail(conn, product_id, checked_postcode(postcode))
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            conn.close()

        if detail is None:
            raise HTTPException(404, "Product not found for this postcode.")

        return detail

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
