"""Beeroo web app: JSON API + static single-page frontend.

    uvicorn app.main:app --reload            # uses $BEEROO_DB or data/beeroo.sqlite3
"""
import os
import re
from pathlib import Path
from typing import Optional

import hmac
import json

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import ValidationError
from fastapi.staticfiles import StaticFiles

from app import contrib, queries
from common import db

STATIC = Path(__file__).parent / "static"
POSTCODE_RE = re.compile(r"^\d{4}$")


def create_app(db_path=None):
    db_path = db_path or os.environ.get("BEEROO_DB", "data/beeroo.sqlite3")
    production = os.environ.get("BEEROO_ENV") == "production"

    if production:
        missing = [k for k in ("BEEROO_SALT", "BEEROO_ADMIN_TOKEN") if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"production needs {', '.join(missing)} to be set")
        if len(os.environ["BEEROO_ADMIN_TOKEN"]) < 24:
            raise RuntimeError("BEEROO_ADMIN_TOKEN must be at least 24 characters")

    # Public contributions stay closed in production until explicitly enabled
    # (retailer terms of use haven't been reviewed yet).
    contrib_open = os.environ.get("BEEROO_CONTRIB_ENABLED", "0" if production else "1") == "1"

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

    @app.post("/api/contrib")
    async def contribute(request: Request):
        """Accept a consented price contribution. See docs/CONTRIBUTION_API.md."""
        if not contrib_open:
            raise HTTPException(503, "contributions are not open yet")
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > contrib.MAX_BODY_BYTES:
            raise HTTPException(413, "payload too large")

        raw = await request.body()
        if len(raw) > contrib.MAX_BODY_BYTES:
            raise HTTPException(413, "payload too large")

        try:
            body = contrib.ContributionIn.model_validate(json.loads(raw))
        except (ValueError, ValidationError) as e:
            detail = "invalid contribution"
            if isinstance(e, ValidationError):
                first = e.errors()[0]
                detail = f"invalid contribution: {'.'.join(str(x) for x in first['loc'])}: {first['msg']}"
            raise HTTPException(422, detail)

        conn = connection()
        try:
            result = contrib.accept_contribution(conn, body)
        except contrib.ContribError as e:
            raise HTTPException(e.status, e.message)
        finally:
            conn.close()

        return JSONResponse(result, status_code=200 if result["status"] == "duplicate" else 202)

    @app.get("/api/contrib/health")
    def contrib_health():
        conn = connection()
        try:
            return contrib.health(conn)
        finally:
            conn.close()

    @app.post("/api/contrib/promote")
    def contrib_promote(x_admin_token: Optional[str] = Header(default=None)):
        """Run a full promotion + expiry pass (for a scheduled job)."""
        require_admin(x_admin_token)

        conn = connection()
        try:
            stats = contrib.promote(conn)
            stats["expired"] = contrib.expire(conn)
            return stats
        finally:
            conn.close()

    @app.post("/api/admin/ingest")
    async def admin_ingest(request: Request, x_admin_token: Optional[str] = Header(default=None)):
        """Trusted first-party ingest (our own scraper or captures). Admin token required."""
        require_admin(x_admin_token)
        raw = await request.body()
        if len(raw) > 12_000_000:
            raise HTTPException(413, "payload too large")
        try:
            body = contrib.IngestIn.model_validate(json.loads(raw))
        except (ValueError, ValidationError) as e:
            detail = "invalid ingest request"
            if isinstance(e, ValidationError):
                first = e.errors()[0]
                detail += f": {'.'.join(str(x) for x in first['loc'])}: {first['msg']}"
            raise HTTPException(422, detail)

        conn = connection()
        try:
            return contrib.ingest_trusted(conn, body)
        except contrib.ContribError as e:
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
