FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# Wheels are installed from vendor/wheels when present, so a build can run
# with the network off (see README, "Offline build").
COPY requirements.txt ./
COPY vendor/ ./vendor/
RUN if ls vendor/wheels/*.whl >/dev/null 2>&1; then \
        pip install --no-index --find-links vendor/wheels -r requirements.txt; \
    else \
        pip install -r requirements.txt; \
    fi

COPY alembic.ini pytest.ini ./
COPY src/ ./src/
COPY data/ ./data/
COPY tests/ ./tests/
COPY scripts/ ./scripts/

RUN useradd --create-home --uid 10001 dogfood && chown -R dogfood /app
USER dogfood

EXPOSE 8080
CMD ["sh", "scripts/start.sh"]
