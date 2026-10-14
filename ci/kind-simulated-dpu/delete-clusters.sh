#!/usr/bin/env bash
# delete-clusters.sh
#
# Skeleton: tear down ONLY the ODSE simulated host + DPU Kind clusters.
#
# Will not:
#   - kind delete clusters --all
#   - delete names other than ODSE_HOST_CLUSTER / ODSE_DPU_CLUSTER
#   - touch Docker resources unrelated to those cluster names
#
# Usage: ./delete-clusters.sh
set -euo pipefail

ODSE_HOST_CLUSTER="${ODSE_HOST_CLUSTER:-odse-host}"
ODSE_DPU_CLUSTER="${ODSE_DPU_CLUSTER:-odse-dpu}"

log() { printf 'odse-kind-delete: %s\n' "$*"; }
die() { printf 'odse-kind-delete: error: %s\n' "$*" >&2; exit 1; }

command -v kind >/dev/null 2>&1 || die "'kind' not found on PATH"

[[ -n "${ODSE_HOST_CLUSTER}" && -n "${ODSE_DPU_CLUSTER}" ]] \
  || die "cluster names must be non-empty"
[[ "${ODSE_HOST_CLUSTER}" != "all" && "${ODSE_DPU_CLUSTER}" != "all" ]] \
  || die "refusing to delete cluster name 'all'"

if [[ -t 0 && "${ODSE_CONFIRM_DELETE:-}" != "yes" ]]; then
  printf 'Delete Kind clusters "%s" and "%s"? [y/N] ' \
    "${ODSE_HOST_CLUSTER}" "${ODSE_DPU_CLUSTER}"
  read -r answer
  case "${answer}" in
    y|Y|yes|YES) ;;
    *) log "aborted"; exit 0 ;;
  esac
fi

existing="$(kind get clusters 2>/dev/null || true)"

delete_one() {
  local name="$1"
  if printf '%s\n' "${existing}" | grep -qx "${name}"; then
    log "Deleting Kind cluster '${name}'"
    kind delete cluster --name "${name}"
  else
    log "Kind cluster '${name}' not present; skipping"
  fi
}

delete_one "${ODSE_DPU_CLUSTER}"
delete_one "${ODSE_HOST_CLUSTER}"

# TODO: remove leftover veth/netdevsim devices created on the CI runner
# if/when create-*.sh starts creating them outside Kind node containers.

log "Done."
