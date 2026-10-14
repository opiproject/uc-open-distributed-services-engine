#!/usr/bin/env bash
# ODSE VM-to-VM lab: validate a running guest has everything the lab
# workflow expects (see qemu/images/README.md required-packages table).
#
# Read-only: this script only runs `command -v` / `systemctl is-active`
# / `test -x` checks over SSH. It never modifies the guest.
#
# Usage:
#   validate-vm-image.sh --host <mgmt-ip> --user <ssh_user> \
#     [--identity-file <path>] [--port 22]
#
# Exit code is 0 only if every check passes.

set -euo pipefail

HOST=""
USER_NAME=""
IDENTITY_FILE=""
PORT="22"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --user) USER_NAME="$2"; shift 2 ;;
    --identity-file) IDENTITY_FILE="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^#//; s/^ //' | sed -n '2,15p'
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "${HOST}" || -z "${USER_NAME}" ]]; then
  echo "error: --host and --user are required" >&2
  exit 2
fi

SSH_ARGS=(-p "${PORT}" -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new)
if [[ -n "${IDENTITY_FILE}" ]]; then
  SSH_ARGS+=(-i "${IDENTITY_FILE}")
fi

ssh_run() {
  ssh "${SSH_ARGS[@]}" "${USER_NAME}@${HOST}" "$@"
}

FAILURES=0
check() {
  local description="$1"
  local remote_command="$2"
  if ssh_run "${remote_command}" >/dev/null 2>&1; then
    echo "PASS  ${description}"
  else
    echo "FAIL  ${description}"
    FAILURES=$((FAILURES + 1))
  fi
}

echo "== validating guest at ${USER_NAME}@${HOST}:${PORT} =="

check "SSH reachable"                       "true"
check "iproute2 (ip)"                       "command -v ip"
check "iputils-ping (ping)"                 "command -v ping"
check "ethtool"                             "command -v ethtool"
check "iperf3"                              "command -v iperf3"
check "tcpdump"                             "command -v tcpdump"
check "sshd running"                        "systemctl is-active --quiet ssh || systemctl is-active --quiet sshd"
check "guest-network-setup.sh present"      "test -x /usr/local/bin/guest-network-setup.sh"
check "at least one non-loopback interface" "ip -o link show | awk -F': ' '{print \$2}' | grep -vq '^lo\$'"

echo
if [[ "${FAILURES}" -eq 0 ]]; then
  echo "all checks passed"
  exit 0
else
  echo "${FAILURES} check(s) failed"
  exit 1
fi
