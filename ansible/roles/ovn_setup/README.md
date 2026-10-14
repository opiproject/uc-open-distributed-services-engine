# ovn_setup

Deploys and configures OVN central (northbound/southbound databases,
`ovn-northd`) and chassis (`ovn-controller`) components.

Expected responsibilities:

- OVN central cluster deployment and TLS/RAFT configuration
- Chassis registration and encapsulation (Geneve/STT) setup
- Logical switch/router provisioning hooks for OpenStack and
  OVN-Kubernetes integration
- Integration with `ovs_setup` for chassis-side OVS configuration
