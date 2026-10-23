#!/usr/bin/env bash
# Harden a fresh Ubuntu 24.04 server and install VELORA's tools (P12-T1).
# Run once, as root, from the repository checkout at /opt/velora:
#
#   sudo /opt/velora/infra/host/install.sh --admin-user ubuntu --ssh-from 203.0.113.7/32
#
# --admin-user  the account you log in with; it must already have your SSH key
# --ssh-from    an address or network allowed to reach SSH (repeat for more). Everything
#               else on the internet reaches port 443 only.
#
# It sets up:
#   - SSH: keys only, no root login, only the admin user;
#   - firewall (ufw): incoming 443 from anywhere and 22 from --ssh-from; nothing else;
#   - automatic security updates, with a reboot at 00:00 UTC (05:00 PKT) when one needs it;
#   - Docker from Ubuntu's own packages (so the security updates cover it), with log rotation;
#   - swap on small machines;
#   - /usr/local/bin/velora, /etc/velora (owner-only) and the hourly backup timer.
# Safe to re-run. Check the result from outside with the port scan (deployment guide).

set -euo pipefail

ADMIN_USER=""
SSH_FROM=()
while [ $# -gt 0 ]; do
	case "$1" in
	--admin-user) ADMIN_USER="$2"; shift 2 ;;
	--ssh-from) SSH_FROM+=("$2"); shift 2 ;;
	*) echo "unknown option: $1" >&2; exit 2 ;;
	esac
done

REPO="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
say() { printf '== %s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root (sudo)"
[ -n "$ADMIN_USER" ] || die "--admin-user is required"
[ "${#SSH_FROM[@]}" -gt 0 ] || die "--ssh-from is required: without it SSH would be closed"
# shellcheck source=/dev/null
. /etc/os-release
if [ "${ID:-}" != "ubuntu" ] || [ "${VERSION_ID:-}" != "24.04" ]; then
	die "written for Ubuntu 24.04; found ${PRETTY_NAME:-unknown}"
fi
id "$ADMIN_USER" >/dev/null 2>&1 || die "no such user: $ADMIN_USER"
home="$(getent passwd "$ADMIN_USER" | cut -d: -f6)"
grep -qE '^(ssh-|ecdsa-|sk-)' "$home/.ssh/authorized_keys" 2>/dev/null ||
	die "$ADMIN_USER has no SSH key in $home/.ssh/authorized_keys: passwords are about to be switched off"
# Every SSH session open now must come from an allowed address, or the firewall locks it out.
ss -Htn state established '( sport = :22 )' | awk '{print $4}' |
	python3 -c '
import ipaddress, sys
allowed = [ipaddress.ip_network(n, strict=False) for n in sys.argv[1:]]
for peer in sys.stdin.read().split():
    ip = ipaddress.ip_address(peer.rsplit(":", 1)[0].strip("[]").removeprefix("::ffff:"))
    if not any(ip in net for net in allowed):
        sys.exit(f"you are connected from {ip}, which no --ssh-from covers")
' "${SSH_FROM[@]}" || die "refusing to lock you out"

say "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q docker.io docker-compose-v2 ufw unattended-upgrades curl git

say "SSH: keys only"
# 00- so it wins over the cloud image's own files (sshd keeps the first value it reads).
cat >/etc/ssh/sshd_config.d/00-velora.conf <<EOF
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
AuthenticationMethods publickey
AllowUsers $ADMIN_USER
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
MaxAuthTries 3
LoginGraceTime 30
EOF
sshd -t
systemctl reload ssh

say "firewall: 443 from anywhere, 22 from ${SSH_FROM[*]}"
# Some cloud images (Oracle's among them) ship their own iptables rules that reject
# everything but SSH; they would sit in front of ufw. Remove them, leaving ufw in charge.
if dpkg -s netfilter-persistent >/dev/null 2>&1; then
	apt-get purge -y -q iptables-persistent netfilter-persistent
	iptables -P INPUT ACCEPT
	iptables -F INPUT
fi
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow 443/tcp
for source in "${SSH_FROM[@]}"; do
	ufw allow from "$source" to any port 22 proto tcp
done
ufw --force enable
# Docker publishes ports past ufw. compose.yaml publishes 443 alone (and a test enforces it);
# the external scan is the proof.

say "automatic security updates"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
cat >/etc/apt/apt.conf.d/52velora-unattended-upgrades <<'EOF'
Unattended-Upgrade::Allowed-Origins {
	"${distro_id}:${distro_codename}-security";
	"${distro_id}ESMApps:${distro_codename}-apps-security";
	"${distro_id}ESM:${distro_codename}-infra-security";
};
// The worker's recovery mode (P11) handles the short outage; 00:00 UTC is 05:00 PKT.
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "00:00";
EOF
systemctl enable --now unattended-upgrades

say "Docker"
install -d -m 755 /etc/docker
cat >/etc/docker/daemon.json <<'EOF'
{
  "log-driver": "local",
  "log-opts": {"max-size": "10m", "max-file": "5"},
  "live-restore": true,
  "no-new-privileges": true
}
EOF
systemctl enable docker
systemctl restart docker

memory_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
if [ "$memory_kb" -lt 3000000 ] && ! swapon --show | grep -q .; then
	say "swap: 2 GB (this machine has under 3 GB of memory)"
	fallocate -l 2G /swapfile
	chmod 600 /swapfile
	mkswap /swapfile >/dev/null
	swapon /swapfile
	grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
fi

say "VELORA tools"
install -d -m 700 -o root -g root /etc/velora
ln -sf "$REPO/infra/bin/velora" /usr/local/bin/velora
install -m 644 "$REPO/infra/host/systemd/velora-backup.service" /etc/systemd/system/
install -m 644 "$REPO/infra/host/systemd/velora-backup.timer" /etc/systemd/system/
systemctl daemon-reload

say "done. Next (docs/operations/deployment.md):"
cat <<'EOF'
  sudo velora build
  sudo velora env-init --env staging --domain <your hostname> --alert-email <you>
  (fill in the CHANGE_ME values in /etc/velora/*.env)
  sudo velora env-check
  sudo velora release HEAD --first
  sudo systemctl enable --now velora-backup.timer
EOF
