# External dependency only. The accepted application image is never changed.
FROM postgres:16-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-psycopg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --no-create-home requi_app \
    && mkdir -p /owned/socket && chown -R postgres:postgres /owned
COPY scripts/local-pg/service.py /controller/service.py
COPY tests/pg_harness.py /controller/pg_contract.py
COPY src/delivery_api/runtime_roles.py /controller/runtime_roles.py
COPY supabase/migrations /controller/migrations
USER postgres
CMD ["python3", "/controller/service.py", "serve"]
