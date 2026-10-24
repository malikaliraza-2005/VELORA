#!/usr/bin/env bash
# Prepare the OpenWA trial on Linux or macOS (P0-E1): writes .env with fresh random secrets and
# creates the evidence folder. Run once, in this folder: ./setup.sh
# Refuses to overwrite an existing .env: OpenWA's admin key and pepper are fixed at first boot.
set -euo pipefail
cd "$(dirname "$0")"

if [ -e .env ]; then
	echo ".env already exists; keeping it. Delete it only together with the volumes (docker compose down -v)." >&2
	exit 1
fi
secret() { openssl rand -hex "$1"; }
umask 077
cat >.env <<EOF
# Written by setup.sh. Secret: never share, screenshot or commit this file.
ENGINE_TYPE=baileys
OPENWA_DB_PASSWORD=$(secret 24)
API_MASTER_KEY=$(secret 32)
API_KEY_PEPPER=$(secret 32)
WEBHOOK_SECRET=$(secret 32)
TRIAL_UID=$(id -u)
TRIAL_GID=$(id -g)
EOF
umask 022
mkdir -p evidence
[ -e evidence/evidence-log.csv ] || cp templates/evidence-log.csv evidence/evidence-log.csv
echo "Ready. Next: docker compose up -d   (then see README.md, step 3)"
