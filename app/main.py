"""Beeroo web app: JSON API + static single-page frontend.

    uvicorn app.main:app --reload            # uses $BEEROO_DB or data/beeroo.sqlite3

The free setup doesn't run this at all (it publishes a static export instead,
see docs/FREE_HOSTING.md); this server is for local use and the optional paid
deployment.
"""
import hmac
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app import ingest, queries
from common import db
from common.states import normalise_state, state_list

STATIC = Path(__file__).parent / "static"


def create_app(db_path=None):
    db_path = db_path or os.environ.get("BEEROO_DB", "data/beeroo.sqlite3")
    production = os.environ.get("BEEROO_ENV") == "production"

    if production:
        token = os.environ.get("BEEROO_ADMIN_TOKEN")
        if not token:
            raise RuntimeError("production needs BEEROO_ADMIN_TOKEN to be set")
        if len(token) < 24:
            raise RuntimeError("BEEROO_ADMIN_TOKEN must be at least 24 characters")

    app = FastAPI(
        title="Beeroo",
        docs_url=None if production else "/api/docs",
        openapi_url=None if production else "/api/openapi.json",
    )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        if production:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    def require_admin(token):
        expected = os.environ.get("BEEROO_ADMIN_TOKEN")
        if not expected or not token or not hmac.compare_digest(expected, token):
            raise HTTPException(403, "forbidden")

    def connection():
        return db.connect(db_path)

    def checked_state(value):
        code = normalise_state(value)
        if code is None:
            raise HTTPException(400, "Choose a valid state or territory.")
        return code

    @app.get("/api/states")
    def states():
        """The state/territory dropdown, with the postcode each is priced from."""
        return {"states": state_list()}

    @app.get("/api/locations")
    def locations(state: str):
        conn = connection()
        try:
            return queries.resolve_locations(conn, checked_state(state))
        finally:
            conn.close()

    @app.get("/api/compare")
    def compare(
        state: str,
        q: str = "",
        sort: str = "value",
        reverse: bool = False,
        include_member: bool = True,
        retailer: list[str] = Query(default=[]),
        type: list[str] = Query(default=[]),
        min_units: Optional[int] = Query(default=None, ge=1, le=100),
        max_units: Optional[int] = Query(default=None, ge=1, le=100),
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
                conn, checked_state(state), q=q, sort=sort, reverse=reverse,
                include_member=include_member, retailers=retailer or None, types=type or None,
                min_abv=min_abv, max_abv=max_abv, min_retailers=min_retailers,
                min_units=min_units, max_units=max_units,
                limit=limit, offset=offset,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            conn.close()

    @app.get("/api/products/{product_id}")
    def product(product_id: int, state: str):
        conn = connection()
        try:
            detail = queries.product_detail(conn, product_id, checked_state(state))
        finally:
            conn.close()

        if detail is None:
            raise HTTPException(404, "Product not found for this state.")

        return detail

    @app.post("/api/admin/ingest")
    async def admin_ingest(request: Request, x_admin_token: Optional[str] = Header(default=None)):
        """Trusted first-party ingest (our own scraper). Admin token required."""
        require_admin(x_admin_token)
        raw = await request.body()
        if len(raw) > ingest.MAX_BODY_BYTES:
            raise HTTPException(413, "payload too large")
        try:
            body = ingest.IngestIn.model_validate(json.loads(raw))
        except (ValueError, ValidationError) as e:
            detail = "invalid ingest request"
            if isinstance(e, ValidationError):
                first = e.errors()[0]
                detail += f": {'.'.join(str(x) for x in first['loc'])}: {first['msg']}"
            raise HTTPException(422, detail)

        conn = connection()
        try:
            return ingest.ingest_trusted(conn, body)
        except ingest.IngestError as e:
            raise HTTPException(e.status, e.message)
        finally:
            conn.close()

    @app.get("/healthz")
    def healthz():
        conn = connection()
        try:
            products = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
            latest = conn.execute("SELECT MAX(observed_at) m FROM price_observations").fetchone()["m"]
        finally:
            conn.close()
        return {"status": "ok", "products": products, "latest_data": latest}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
