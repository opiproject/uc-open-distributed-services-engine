# network-functions

Sample containerized network functions (CNFs) used to validate offloaded
datapaths end-to-end in a Kubernetes environment.

Expected contents:

- Example CNF manifests (e.g. simple firewall/NAT/load-balancer workloads)
  deployed on SR-IOV/vDPA-backed pod interfaces
- Scenarios used by `tests/offload-validation` and `tests/performance`
