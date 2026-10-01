# Beeroo web app (API + frontend). The Dan Murphy's scraper is NOT part of this
# image: it must run on your own machine (see docs/SCHEDULED_SCRAPE.md).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BEEROO_ENV=production \
    BEEROO_DB=/data/beeroo.sqlite3

WORKDIR /srv
RUN useradd --system --uid 10001 --no-create-home beeroo \
 && mkdir /data && chown beeroo:beeroo /data

COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

COPY app app
COPY common common
COPY scrapers scrapers
COPY scripts/promote_contributions.py scripts/backup_db.py scripts/

USER beeroo
EXPOSE 8080
VOLUME /data

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"

# One worker: SQLite has a single writer. --no-access-log so client IPs are not logged.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
