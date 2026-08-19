#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x backend/.venv/bin/python ]; then
  echo "backend/.venv is missing — create it first (see backend/README or DEMO_CHECKLIST.md)"
  exit 1
fi

echo "==> Infra containers (postgres :5435, minio :9000, go2rtc :1984)"
docker compose up -d postgres minio go2rtc

echo "==> Waiting for Postgres…"
until docker compose exec -T postgres pg_isready -U vision24 -d vision24 >/dev/null 2>&1; do
  sleep 1
done

echo "==> Schema + bucket"
(cd backend && .venv/bin/python -m scripts.bootstrap)

if [ ! -f media/sample.mp4 ]; then
  echo "==> Downloading sample footage"
  ./media/download_sample.sh
fi

echo "==> Demo seed (tenant, owner, zones, analysis, POS feed)"
SEED_ARGS=""
if [ "${SKIP_ANALYSIS:-}" = "1" ]; then
  SEED_ARGS="--skip-analysis"
fi
(cd backend && .venv/bin/python -m scripts.demo_seed $SEED_ARGS)

cat <<'EOF'

==> Now start the app processes (each in its own terminal):

  cd backend && .venv/bin/python -m uvicorn app.main:app --port 8020

  cd backend && bash run_worker.sh

  cd frontend && npm run dev

Then open http://localhost:3001 and sign in as demo@vision24.local.
Full choreography and fallbacks: DEMO_CHECKLIST.md
EOF
