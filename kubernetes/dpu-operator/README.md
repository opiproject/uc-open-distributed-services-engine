# dpu-operator

A Kubernetes operator responsible for DPU/IPU lifecycle management:
discovery, firmware/driver reconciliation, and exposing DPU capabilities to
the cluster.

Expected contents:

- CRDs describing DPU inventory, capabilities, and desired configuration
- Controller/reconciler logic driving `ansible/roles/dpu_base` and
  vendor roles (or their containerized equivalents)
- Integration with the SR-IOV device plugin and `kubernetes/opi` services
