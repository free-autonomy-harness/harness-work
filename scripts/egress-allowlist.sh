#!/usr/bin/env bash
# Egress allowlist wrapper for Hermes container.
# Runs as entrypoint, configures iptables to restrict outbound traffic,
# then execs the original command.
#
# Usage: egress-allowlist.sh <provider> <model> -- <original-command...>
# Providers: nvidia, groq

set -euo pipefail

PROVIDER="${1:-}"
MODEL="${2:-}"
shift 2

if [[ "$1" != "--" ]]; then
    echo "Usage: $0 <provider> <model> -- <command...>" >&2
    exit 1
fi
shift

ORIGINAL_CMD=("$@")

# Allowed domains per provider (resolved to IPs at runtime)
declare -A ALLOWED_DOMAINS=(
    ["nvidia"]="integrate.api.nvidia.com"
    ["groq"]="api.groq.com"
)

# Additional always-allowed domains
ALWAYS_ALLOWED=(
    "registry.npmjs.org"  # for npm if needed
    "pypi.org"            # for pip if needed
    "files.pythonhosted.org"
)

log() { echo "[egress-allowlist] $*" >&2; }

# Resolve domain to IPs (IPv4 only for simplicity)
resolve_domain() {
    local domain="$1"
    getent ahostsv4 "$domain" 2>/dev/null | awk '{print $1}' | sort -u || true
}

# Build allowed IP list
ALLOWED_IPS=()
DOMAINS=()

# Add provider-specific domain
if [[ -n "${ALLOWED_DOMAINS[$PROVIDER]:-}" ]]; then
    DOMAINS+=("${ALLOWED_DOMAINS[$PROVIDER]}")
fi

# Add always-allowed domains
DOMAINS+=("${ALWAYS_ALLOWED[@]}")

log "Provider: $PROVIDER, Model: $MODEL"
log "Allowed domains: ${DOMAINS[*]}"

for domain in "${DOMAINS[@]}"; do
    ips=$(resolve_domain "$domain")
    if [[ -z "$ips" ]]; then
        log "WARNING: Could not resolve $domain"
        continue
    fi
    for ip in $ips; do
        ALLOWED_IPS+=("$ip")
        log "Allowing $domain -> $ip"
    done
done

if [[ ${#ALLOWED_IPS[@]} -eq 0 ]]; then
    log "ERROR: No IPs resolved, refusing to start"
    exit 1
fi

# Setup iptables
log "Configuring iptables..."

# Flush existing rules (container starts fresh)
iptables -F OUTPUT 2>/dev/null || true
iptables -X 2>/dev/null || true

# Default policy: DROP
iptables -P OUTPUT DROP

# Allow loopback
iptables -A OUTPUT -o lo -j ACCEPT

# Allow established/related connections
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Allow DNS (UDP 53) - needed for resolution inside container
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT

# Allow specific IPs (HTTPS only)
for ip in "${ALLOWED_IPS[@]}"; do
    iptables -A OUTPUT -d "$ip" -p tcp --dport 443 -j ACCEPT
    # Also allow HTTP for some registries
    iptables -A OUTPUT -d "$ip" -p tcp --dport 80 -j ACCEPT
done

# Log dropped packets (for debugging)
iptables -A OUTPUT -j LOG --log-prefix "[EGRESS-DROP] " --log-level 4

log "Iptables configured. Starting Hermes..."
log "Command: ${ORIGINAL_CMD[*]}"

# Exec the original command
exec "${ORIGINAL_CMD[@]}"