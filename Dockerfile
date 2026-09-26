# syntax=docker/dockerfile:1
FROM node:22-slim AS web
WORKDIR /src
COPY src/delivery_api/web /out

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PORT=8000 DELIVERY_HOST=0.0.0.0 DELIVERY_DATABASE_URL=sqlite:////data/delivery.db
WORKDIR /app
COPY . /app
RUN --mount=type=secret,id=gitconfig,target=/root/.gitconfig,required=false pip install --no-cache-dir .
EXPOSE 8000
VOLUME ["/data"]
CMD ["python", "-m", "delivery_api"]
