#!/usr/bin/env bash
# The stack rehearsal (P12): the real deployment, run in CI on one machine, through the same
# `velora` tool the founder uses on the server. It is a rehearsal, not staging: TLS comes from
# Caddy's own authority, alerts go to a local HTTPS sink, and there is no WhatsApp.
#
#   1. a first release from nothing (settings, provisioning, migrations, every container)
#   2. the edge and the secrets: only 443 published, TLS and HSTS, owner-only settings, no
#      secret in any container log, proxy headers trusted from Caddy alone, staging guard
#   3. failure drills: the worker killed (restarted, recovery mode runs), the API down (alert
#      fires and clears), a backup and a verified restore
#   4. a second release, a rollback to the first, and a roll forward
#
# Every result is written to $VELORA_DRILL_RECORD (default: drill-record.md in the work dir).
# Needs Docker with compose, git, curl and openssl. Run from anywhere in the repository.

set -euo pipefail

ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
WORK="$(mktemp -d)"
export VELORA_REPO="$WORK/repo" VELORA_CONFIG_DIR="$WORK/etc" VELORA_DRILL_DIR="$WORK/drill"
export VELORA_COMPOSE_EXTRA="$VELORA_REPO/infra/tests/compose.drill.yaml"
V="$VELORA_REPO/infra/bin/velora"
DOMAIN="velora.test"
RECORD="${VELORA_DRILL_RECORD:-$WORK/drill-record.md}"
SINK="https://alert-sink:8443/alerts"

note() { printf '%s\n' "$*" | tee -a "$RECORD"; }
pass() { note "- PASS: $*"; }
fail() { note "- FAIL: $*"; exit 1; }
step() { printf '\n=== %s\n' "$*"; note ""; note "### $*"; }
seconds_since() { echo $(($(date +%s) - $1)); }

cleanup() {
	local status=$?
	if [ "$status" -ne 0 ] && [ -x "$V" ]; then
		echo "--- last log lines" >&2
		"$V" compose --profile jobs logs --no-color --tail 60 >&2 2>/dev/null || true
	fi
	if [ -x "$V" ]; then
		"$V" compose --profile jobs down -v --remove-orphans >/dev/null 2>&1 || true
	fi
	git -C "$ROOT" worktree remove --force "$VELORA_REPO" >/dev/null 2>&1 || true
	exit "$status"
}
trap cleanup EXIT

wait_for() { # wait_for SECONDS DESCRIPTION COMMAND...
	local limit="$1" what="$2" start
	shift 2
	start="$(date +%s)"
	until "$@" >/dev/null 2>&1; do
		[ "$(seconds_since "$start")" -lt "$limit" ] || fail "$what (waited ${limit}s)"
		sleep 3
	done
	seconds_since "$start"
}

: >"$RECORD"
note "# Stack rehearsal $(date -u +%FT%TZ)"
note ""
note "Revision $(git -C "$ROOT" rev-parse HEAD); Docker $(docker version --format '{{.Server.Version}}')."

# Its own checkout, so releases can switch revisions without touching this one.
git -C "$ROOT" worktree add -q --detach "$VELORA_REPO" HEAD

step "1. First release, from nothing"
started="$(date +%s)"
TAG_A="$("$V" build HEAD)"
"$V" env-init --env staging --domain "$DOMAIN" --tls internal --alert-webhook "$SINK" >/dev/null
install -d -m 700 "$WORK/keys"
docker run --rm --network none --user "$(id -u):$(id -g)" -v "$WORK/keys:/k" \
	"velora-app:$TAG_A" python -m velora.backup keygen --public-out /k/backup.pub \
	--secret-out /k/backup.key >/dev/null
sed -i "s|^VELORA_BACKUP_PUBLIC_KEY=.*|VELORA_BACKUP_PUBLIC_KEY=$(cat "$WORK/keys/backup.pub")|;
	s|^VELORA_BACKUP_STORAGE=.*|VELORA_BACKUP_STORAGE=file:///backups|" "$VELORA_CONFIG_DIR/backup.env"
sed -i "s|^VELORA_OPERATOR=.*|VELORA_OPERATOR=stack-drill|" "$VELORA_CONFIG_DIR/ops.env"
# The alert sink's certificate (rehearsal only; readable by the sink's container user).
install -d -m 755 "$VELORA_DRILL_DIR/certs"
cp "$VELORA_REPO/infra/tests/alert_sink.py" "$VELORA_DRILL_DIR/"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj "/CN=alert-sink" \
	-addext "subjectAltName=DNS:alert-sink" -keyout "$VELORA_DRILL_DIR/certs/sink.key" \
	-out "$VELORA_DRILL_DIR/certs/sink.crt" 2>/dev/null
chmod 644 "$VELORA_DRILL_DIR/certs/sink.key" "$VELORA_DRILL_DIR/alert_sink.py"
"$V" env-check >/dev/null || fail "env-check refused the settings"
"$V" release HEAD --first
pass "first release $TAG_A: built, settings checked, provisioned, migrated, all containers up, https://$DOMAIN/readyz ready in $(seconds_since "$started") s"

step "2. Edge and secrets"
ps_json="$("$V" compose ps --format json)"
python3 - "$ps_json" <<'EOF' || fail "a container other than caddy publishes a port"
import json, sys
text = sys.argv[1].strip()
rows = json.loads(text) if text.startswith("[") else [json.loads(l) for l in text.splitlines()]
published = {(r["Service"], p["PublishedPort"]) for r in rows for p in r.get("Publishers") or [] if p.get("PublishedPort")}
assert published == {("caddy", 443)}, published
EOF
pass "only caddy publishes a port, and only 443"
docker run --rm --network host "velora-app:$TAG_A" python -m velora.deploy scan 127.0.0.1 \
	--ports 80,443,2019,5432,8000,8080,8443 --expect 443 >"$WORK/scan.txt" ||
	{ cat "$WORK/scan.txt"; fail "port scan of the host"; }
pass "port scan of the host: $(grep '^open:' "$WORK/scan.txt") (PostgreSQL, the API, Caddy's admin and port 80 closed)"

ca="$VELORA_CONFIG_DIR/.caddy-root.crt"
curl_edge=(curl -sS --cacert "$ca" --resolve "$DOMAIN:443:127.0.0.1")
tls="$(openssl s_client -connect 127.0.0.1:443 -servername "$DOMAIN" -CAfile "$ca" \
	-verify_return_error </dev/null 2>/dev/null)"
grep -q "Verify return code: 0 (ok)" <<<"$tls" || fail "TLS certificate chain"
protocol="$(sed -n 's/^ *Protocol *: *//p' <<<"$tls" | head -n 1)"
case "$protocol" in TLSv1.2 | TLSv1.3) ;; *) fail "TLS protocol $protocol" ;; esac
headers="$("${curl_edge[@]}" -D - -o /dev/null "https://$DOMAIN/")"
grep -qi '^strict-transport-security: max-age=31536000' <<<"$headers" || fail "HSTS header"
grep -qi '^content-security-policy:' <<<"$headers" || fail "CSP header"
if grep -qiE '^(server|via):' <<<"$headers"; then fail "server software is named"; fi
version="$("${curl_edge[@]}" -o /dev/null -w '%{http_version}' "https://$DOMAIN/readyz")"
pass "TLS: chain verifies, $protocol, HTTP/$version; HSTS and CSP sent; no Server or Via header"

chmod 644 "$VELORA_CONFIG_DIR/worker.env"
if "$V" env-check >/dev/null 2>&1; then fail "env-check accepted a world-readable file"; fi
chmod 600 "$VELORA_CONFIG_DIR/worker.env"
"$V" env-check >/dev/null || fail "env-check after restoring the permissions"
modes="$(stat -c '%a' "$VELORA_CONFIG_DIR" "$VELORA_CONFIG_DIR"/*.env | sort -u | tr '\n' ' ')"
pass "settings files owner-only (modes: $modes); env-check refuses a readable one"

codes=""
for i in $(seq 1 11); do
	codes+="$("${curl_edge[@]}" -o /dev/null -w '%{http_code} ' -H "X-Forwarded-For: 10.9.$i.1" \
		-H "Idempotency-Key: drill-$i" -H 'Content-Type: application/json' \
		-d '{"token":"not-a-token"}' "https://$DOMAIN/api/auth/login")"
done
[ "${codes% }" = "401 401 401 401 401 401 401 401 401 401 429" ] || fail "login rate limit: $codes"
pass "login rate limit holds through the proxy with 11 spoofed X-Forwarded-For addresses: $codes"

if "$V" admin tenant-create --slug karachi-grill --name "Karachi Grill" --prefix KG >/dev/null 2>&1; then
	fail "staging accepted a restaurant that isn't a test one"
fi
"$V" admin tenant-create --slug test-karachi-grill --name "Test Grill" --prefix TG >/dev/null
pass "staging refuses a non-test restaurant and accepts test-karachi-grill"

step "3. Failure drills"
worker="$("$V" compose ps -q worker)"
restarts="$(docker inspect -f '{{.RestartCount}}' "$worker")"
recoveries="$("$V" compose logs --no-color worker | grep -c 'recovery done')"
"$V" compose exec -T worker python -c '
import os, signal
for pid in os.listdir("/proc"):
    if pid.isdigit() and int(pid) not in (1, os.getpid()):
        if b"velora.workers" in open(f"/proc/{pid}/cmdline", "rb").read():
            os.kill(int(pid), signal.SIGKILL)
'
took="$(wait_for 90 "the worker was not restarted" bash -c "
	[ \$(docker inspect -f '{{.RestartCount}}' $worker) -gt $restarts ] &&
	[ \$('$V' compose logs --no-color worker | grep -c 'recovery done') -gt $recoveries ]")"
pass "worker killed (SIGKILL): Docker restarted it and recovery mode ran again within ${took} s"

sink_has() { "$V" compose logs --no-color alert-sink | grep '^.*ALERT ' | grep -q "$1"; }
"$V" compose stop api >/dev/null
took="$(wait_for 300 "no api_down alert" sink_has '"key": "api_down", "message": .*"state": "firing"')"
pass "API down: the founder's webhook received the api_down alert after ${took} s"
"$V" compose start api >/dev/null
took="$(wait_for 300 "the api_down alert did not clear" sink_has '"key": "api_down", .*"state": "resolved"')"
pass "API back: the alert cleared after ${took} s"

started="$(date +%s)"
"$V" backup >"$WORK/backup.txt" 2>&1 || { cat "$WORK/backup.txt"; fail "backup"; }
"$V" restore --secret-key "$WORK/keys/backup.key" --target-db velora_restored >"$WORK/restore.txt" 2>&1 ||
	{ cat "$WORK/restore.txt"; fail "restore"; }
grep -q "into velora_restored: OK" "$WORK/restore.txt" || { cat "$WORK/restore.txt"; fail "restore checks"; }
pass "backup and verified restore in $(seconds_since "$started") s: $(grep -c '\[ok\]' "$WORK/restore.txt") checks ok ($(grep -o '[0-9]* tables, [0-9]* rows' "$WORK/restore.txt" | head -n 1))"
# Runbook R4: the verified copy replaces the live database.
started="$(date +%s)"
"$V" compose stop api worker monitor >/dev/null 2>&1
"$V" compose exec -T db psql -v ON_ERROR_STOP=1 -U postgres 	-c 'ALTER DATABASE velora RENAME TO velora_damaged' 	-c 'ALTER DATABASE velora_restored RENAME TO velora' >/dev/null
"$V" db provision >/dev/null
"$V" db migrate up >/dev/null
"$V" compose up -d --wait --wait-timeout 180 >/dev/null 2>&1
"${curl_edge[@]}" -f -o /dev/null "https://$DOMAIN/readyz" || fail "the restored database is not serving"
"$V" admin tenants | grep -q test-karachi-grill || fail "the restored database lost the test restaurant"
pass "runbook R4 on the stack: the copy renamed into place, passwords set again, services up in $(seconds_since "$started") s; the test restaurant is there"
note "- GATED: kill OpenWA, force a WhatsApp disconnect (the OpenWA Go, V-01)"

step "4. Release, rollback, roll forward"
api_image() { docker inspect -f '{{.Config.Image}}' "$("$V" compose ps -q api)"; }
git -C "$VELORA_REPO" -c user.name=drill -c user.email=drill@example.invalid \
	commit -q --allow-empty -m "stack drill: release B"
started="$(date +%s)"
"$V" release HEAD
TAG_B="$(sed -n 's/^VELORA_IMAGE_TAG=//p' "$VELORA_CONFIG_DIR/release.env")"
if [ "$TAG_B" = "$TAG_A" ] || [ "$(api_image)" != "velora-app:$TAG_B" ]; then fail "release B"; fi
pass "release $TAG_B (backup first, migrations, switch) in $(seconds_since "$started") s"
started="$(date +%s)"
"$V" rollback
[ "$(api_image)" = "velora-app:$TAG_A" ] || fail "rollback: api runs $(api_image)"
[ "$(git -C "$VELORA_REPO" rev-parse --short=12 HEAD)" = "$TAG_A" ] || fail "rollback: checkout"
pass "rollback to $TAG_A in $(seconds_since "$started") s; the edge answers"
"$V" rollback
[ "$(api_image)" = "velora-app:$TAG_B" ] || fail "roll forward"
pass "rollback again returns to $TAG_B"

secrets=()
while IFS= read -r url; do
	secrets+=("$(python3 -c 'import sys, urllib.parse as u; print(u.unquote(u.urlsplit(sys.argv[1]).password or ""))' "$url")")
done < <(sed -n 's/^VELORA_[A-Z_]*DATABASE_URL=//p' "$VELORA_CONFIG_DIR/ops.env")
secrets+=("$(sed -n 's/^POSTGRES_PASSWORD=//p' "$VELORA_CONFIG_DIR/db.env")")
"$V" compose --profile jobs logs --no-color >"$WORK/all.log" 2>&1
for secret in "${secrets[@]}"; do
	[ -n "$secret" ] || fail "a password was empty"
	if grep -qF -- "$secret" "$WORK/all.log"; then fail "a password appears in a container log"; fi
done
pass "none of the ${#secrets[@]} passwords appears in $(wc -l <"$WORK/all.log") lines of container logs"

note ""
note "All rehearsal checks passed."
cat "$RECORD"
