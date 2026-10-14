#!/usr/bin/env bash
# create-dpu-cluster.sh
#
# Skeleton: create the Kind cluster that plays the DPU (datapath) role
# in simulated OVN-Kubernetes DPU mode.
#
# Safe by design:
#   - Requires `kind` and `kubectl`
#   - Creates only ODSE_DPU_CLUSTER (default odse-dpu)
#   - Does not delete other Kind clusters
#   - Does not helm-install OVN-Kubernetes (values not pinned)
#   - Does not taint/remove the default CNI yet (avoids an unusable cluster)
#
# Usage: ./create-dpu-cluster.sh
set -euo pipefail

ODSE_DPU_CLUSTER="${ODSE_DPU_CLUSTER:-odse-dpu}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

log() { printf 'odse-kind-dpu: %s\n' "$*"; }
die() { printf 'odse-kind-dpu: error: %s\n' "$*" >&2; exit 1; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' not found on PATH"
}

[[ -n "${ODSE_DPU_CLUSTER}" ]] || die "ODSE_DPU_CLUSTER is empty"
[[ "${ODSE_DPU_CLUSTER}" != "all" ]] || die "refusing cluster name 'all'"
[[ "${ODSE_DPU_CLUSTER}" != "${ODSE_HOST_CLUSTER:-odse-host}" ]] \
  || die "DPU cluster name must differ from host cluster name"

need_cmd kind
need_cmd kubectl

if kind get clusters 2>/dev/null | grep -qx "${ODSE_DPU_CLUSTER}"; then
  log "Kind cluster '${ODSE_DPU_CLUSTER}' already exists; leaving it unchanged."
  log "kubectl context: kind-${ODSE_DPU_CLUSTER}"
  kubectl --context "kind-${ODSE_DPU_CLUSTER}" get nodes || true
  exit 0
fi

DPU_CONFIG="$(mktemp)"
trap 'rm -f "${DPU_CONFIG}"' EXIT
cat > "${DPU_CONFIG}" <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    labels:
      odse.example/role: dpu
      # TODO: real DPU-to-host pairing annotation/label from pinned chart.
      # odse.example/host-peer: <HOST_NODE_NAME>
    # TODO: taint DPU nodes so tenant pods cannot schedule here, once
    # ovnkube DaemonSets have the matching toleration.
    # taints:
    #   - key: <DPU_TAINT_KEY>
    #     effect: NoSchedule
networking:
  disableDefaultCNI: false
EOF

log "Creating Kind cluster '${ODSE_DPU_CLUSTER}' (DPU / datapath role)"
CREATE_ARGS=(create cluster --name "${ODSE_DPU_CLUSTER}" --config "${DPU_CONFIG}")
if [[ -n "${ODSE_KIND_IMAGE:-}" ]]; then
  CREATE_ARGS+=(--image "${ODSE_KIND_IMAGE}")
fi
kind "${CREATE_ARGS[@]}"

log "Waiting for node Ready"
kubectl --context "kind-${ODSE_DPU_CLUSTER}" wait --for=condition=Ready node --all --timeout=120s

# ---------------------------------------------------------------------------
# TODO: fake representor end of the veth/netdevsim pair in this node.
# Hardware analog: switchdev representor on the DPU OS (not the host).
# ---------------------------------------------------------------------------
log "TODO skipped: netdevsim/veth fake-representor on DPU node"

# ---------------------------------------------------------------------------
# TODO: shared OVN northbound/southbound between the two Kind clusters.
# Hardware analog: one logical OVN DB; two kube-apiservers.
# Options to evaluate (none selected):
#   - OVN DB pods in the host cluster, DPU ovn-controller reaches them
#   - Dedicated third namespace/cluster for DB
# Do not silently point both charts at in-cluster DB services that cannot
# see each other across Kind networks.
# ---------------------------------------------------------------------------
log "TODO skipped: cross-cluster OVN NB/SB connectivity"

# ---------------------------------------------------------------------------
# TODO: helm upgrade --install ... \
#   -f "${REPO_ROOT}/kubernetes/ovn-kubernetes/dpu-cluster/values.yaml" \
#   -f "${REPO_ROOT}/kubernetes/ovn-kubernetes/simulated/values.yaml"
# Do NOT use hardware/values.yaml. hwOffload must remain false in CI.
# ---------------------------------------------------------------------------
log "TODO skipped: OVN-Kubernetes DPU-personality Helm install"
log "Placeholder values: ${REPO_ROOT}/kubernetes/ovn-kubernetes/dpu-cluster/values.yaml"
log "Simulated overlay: ${REPO_ROOT}/kubernetes/ovn-kubernetes/simulated/values.yaml"

log "DPU cluster is up. Context: kind-${ODSE_DPU_CLUSTER}"
kubectl --context "kind-${ODSE_DPU_CLUSTER}" get nodes -o wide
