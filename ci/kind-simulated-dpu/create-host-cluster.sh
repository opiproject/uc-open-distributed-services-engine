#!/usr/bin/env bash
# create-host-cluster.sh
#
# Skeleton: create the Kind cluster that plays the HOST (workload) role
# in simulated OVN-Kubernetes DPU mode.
#
# Safe by design:
#   - Requires `kind` and `kubectl`
#   - Creates only ODSE_HOST_CLUSTER (default odse-host)
#   - Does not delete other Kind clusters
#   - Does not helm-install OVN-Kubernetes (values not pinned)
#
# Usage: ./create-host-cluster.sh
set -euo pipefail

ODSE_HOST_CLUSTER="${ODSE_HOST_CLUSTER:-odse-host}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

log() { printf 'odse-kind-host: %s\n' "$*"; }
die() { printf 'odse-kind-host: error: %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' not found on PATH"
}

# Refuse names that look like a wipe-all or empty.
[[ -n "${ODSE_HOST_CLUSTER}" ]] || die "ODSE_HOST_CLUSTER is empty"
[[ "${ODSE_HOST_CLUSTER}" != "all" ]] || die "refusing cluster name 'all'"

need_cmd kind
need_cmd kubectl

if kind get clusters 2>/dev/null | grep -qx "${ODSE_HOST_CLUSTER}"; then
  log "Kind cluster '${ODSE_HOST_CLUSTER}' already exists; leaving it unchanged."
  log "kubectl context: kind-${ODSE_HOST_CLUSTER}"
  kubectl --context "kind-${ODSE_HOST_CLUSTER}" get nodes || true
  exit 0
fi

# Minimal Kind config: one control-plane. Extra workers are a TODO once
# we simulate multiple DPU-capable hosts.
HOST_CONFIG="$(mktemp)"
trap 'rm -f "${HOST_CONFIG}"' EXIT
cat > "${HOST_CONFIG}" <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    labels:
      odse.example/role: host
      # TODO: replace with the real host-to-DPU pairing label once
      # kubernetes/ovn-kubernetes Helm keys are pinned.
      # odse.example/dpu-peer: <DPU_NODE_NAME>
networking:
  # TODO: disable default CNI only after OVN-Kubernetes is actually
  # installed; leaving Kind's default CNI avoids a bricked stub cluster.
  disableDefaultCNI: false
EOF

log "Creating Kind cluster '${ODSE_HOST_CLUSTER}' (host / workload role)"
CREATE_ARGS=(create cluster --name "${ODSE_HOST_CLUSTER}" --config "${HOST_CONFIG}")
if [[ -n "${ODSE_KIND_IMAGE:-}" ]]; then
  CREATE_ARGS+=(--image "${ODSE_KIND_IMAGE}")
fi
kind "${CREATE_ARGS[@]}"

log "Waiting for node Ready"
kubectl --context "kind-${ODSE_HOST_CLUSTER}" wait --for=condition=Ready node --all --timeout=120s

# ---------------------------------------------------------------------------
# TODO: fake VF end of a veth/netdevsim pair inside this cluster's node
# container. Do not assume SR-IOV sysfs exists in Kind.
#   docker exec <host-node> bash -c '...'
# ---------------------------------------------------------------------------
log "TODO skipped: netdevsim/veth fake-VF on host node"

# ---------------------------------------------------------------------------
# TODO: helm upgrade --install ... \
#   -f "${REPO_ROOT}/kubernetes/ovn-kubernetes/host-cluster/values.yaml" \
#   -f "${REPO_ROOT}/kubernetes/ovn-kubernetes/simulated/values.yaml"
# Do NOT use hardware/values.yaml here.
# Chart name/version: <OVN_KUBERNETES_CHART> / <OVN_KUBERNETES_CHART_VERSION>
# ---------------------------------------------------------------------------
log "TODO skipped: OVN-Kubernetes host-personality Helm install"
log "Placeholder values: ${REPO_ROOT}/kubernetes/ovn-kubernetes/host-cluster/values.yaml"
log "Simulated overlay: ${REPO_ROOT}/kubernetes/ovn-kubernetes/simulated/values.yaml"

log "Host cluster is up. Context: kind-${ODSE_HOST_CLUSTER}"
kubectl --context "kind-${ODSE_HOST_CLUSTER}" get nodes -o wide
