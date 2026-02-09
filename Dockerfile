FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY shared/ shared/
COPY agents/ agents/
COPY src/ src/

# Run as non-root
RUN useradd --create-home sentinel
USER sentinel

ENTRYPOINT ["python", "-m", "src.runner"]
