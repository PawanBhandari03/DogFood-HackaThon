FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Linux wheels for x86_64 and arm64 are vendored in vendor/wheels, so the
# image builds with the network off. If they ever do not match, fall back
# to PyPI.
COPY requirements.txt ./
COPY vendor/ ./vendor/
RUN pip install --no-index --find-links vendor/wheels -r requirements.txt     || pip install -r requirements.txt

COPY alembic.ini pytest.ini ./
COPY src/ ./src/
COPY data/ ./data/
COPY tests/ ./tests/
COPY scripts/ ./scripts/

RUN useradd --create-home --uid 10001 dogfood && chown -R dogfood /app
USER dogfood

EXPOSE 8080
CMD ["sh", "scripts/start.sh"]
