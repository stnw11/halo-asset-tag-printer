FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY tools/ ./tools/
COPY assets/ ./assets/

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

# src/main.py (the poll loop) lands in a later build step -- until then,
# this image is useful via `docker run ... python -m tools.print_test_tag`.
CMD ["python", "-m", "src.main"]
