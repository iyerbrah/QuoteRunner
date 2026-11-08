#!/usr/bin/env bash
# Redeploy QuoteRunner on the VPS:  cd /opt/quoterunner && ./deploy/deploy.sh
# Stops at the first failing command (set -e), so a broken pull or install
# never restarts the app onto bad code.
set -euo pipefail

cd /opt/quoterunner

echo "==> Pulling the latest code"
git pull --ff-only

echo "==> Installing requirements"
venv/bin/pip install --quiet -r requirements.txt

echo "==> Restarting the service"
sudo systemctl restart quoterunner

echo "==> Checking /healthz"
# The app needs a moment to start; retry for up to ~10 seconds.
curl --fail --silent --show-error --retry 10 --retry-delay 1 --retry-connrefused \
    http://127.0.0.1:8000/healthz
echo
echo "==> Deployed $(git rev-parse --short HEAD)"
