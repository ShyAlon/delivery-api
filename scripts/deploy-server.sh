#!/usr/bin/env bash
set -eu
IMAGE=${1:?usage: deploy-server.sh ghcr.io/owner/delivery-api:sha-<commit>}
NAME=vmodel-delivery
PREVIOUS=$(docker inspect --format '{{.Config.Image}}' "$NAME" 2>/dev/null || true)

echo "[deploy] pulling $IMAGE"
docker pull "$IMAGE"
echo "[deploy] replacing $NAME (SQLite remains in /data)"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped \
  -p 127.0.0.1:8000:8000 -v /data:/data \
  -e DELIVERY_DATABASE_URL=sqlite:////data/delivery.db \
  "$IMAGE" >/dev/null

for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null; then
    echo "[deploy] healthy: $IMAGE"
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 3
done

echo "FAILED: the new container did not pass /health within 30 seconds."
echo "Cause: inspect with: docker logs $NAME"
if [ -n "$PREVIOUS" ]; then
  echo "Next: rollback exactly with: /tmp/deploy-server.sh '$PREVIOUS'"
else
  echo "Next: inspect docker logs $NAME, fix the image, then rerun this deployment."
fi
exit 1
