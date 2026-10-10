# Isolated interactive acceptance image, not delivery production image.
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-15 && rm -rf /var/lib/apt/lists/* && useradd -m -u 1000 requi
WORKDIR /app
COPY wheels/ /wheels/
COPY constraints.txt wheel-sha256.txt /app/
RUN sha256sum --check /app/wheel-sha256.txt && pip install --no-cache-dir -c /app/constraints.txt /wheels/*.whl 'psycopg[binary]' && pip check
COPY item6/ /app/item6/
COPY migrations/ /app/migrations/
COPY source-manifest.json /app/
ENV PYTHONUNBUFFERED=1 DELIVERY_CONTROL_PLANE_ONLY=0
USER requi
ENTRYPOINT ["python", "/app/item6/container_entry.py"]
