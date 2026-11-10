FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./

# Installed from the package, not a requirements file: the dependency list has
# one source of truth. `cryptography` is a hard dependency because `crypto.py`
# imports it at module scope.
RUN pip install .

EXPOSE 8000

# Migrations run at start, not at build: the volume with the database does not
# exist during a build, and a failed migration should fail the deploy loudly.
#
# The port comes from $PORT. Render forwards traffic to it (default 10000) and
# fails the deploy if it cannot detect the bound port, so a hard-coded 8000 is a
# coin flip. The fallback keeps `docker run -p 8000:8000` working locally.
CMD ["sh", "-c", "python -m alembic -c alembic.ini upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
