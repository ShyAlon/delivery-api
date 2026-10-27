# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src/delivery_api/ src/delivery_api/
COPY vendor/vmodel_contracts-0.28.0-py3-none-any.whl vendor/requi_child_contract-0.2.0-py3-none-any.whl /wheels/
RUN echo "bc8cb30c63719e106451d514532b28a30d8e69fbcce8a771121eab2369f0391e  /wheels/vmodel_contracts-0.28.0-py3-none-any.whl" | sha256sum --check -
RUN echo "5c87f7e510ea1575196830a9be78966f4d7733637d3e403f693111c57fce1257  /wheels/requi_child_contract-0.2.0-py3-none-any.whl" | sha256sum --check -
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
    DELIVERY_CONTROL_PLANE_ONLY=1 DELIVERY_HOST=0.0.0.0 DELIVERY_PORT=8000 \
    DELIVERY_DATABASE_URL=sqlite:////tmp/control-plane/delivery.db \
    XDG_DATA_HOME=/tmp/control-plane HOME=/tmp/control-plane
WORKDIR /app
COPY --from=build /wheels/ /wheels/
RUN pip install --no-cache-dir '/wheels/'*.whl 'psycopg[binary]>=3.2,<4' && pip check \
    && rm -rf /wheels && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin requi
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
 CMD python -c "import urllib.request,json; v=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)); assert v.get('ok') is True and v.get('runtime_profile')=='control_plane_only'"
CMD ["python", "-m", "delivery_api.control_plane"]
