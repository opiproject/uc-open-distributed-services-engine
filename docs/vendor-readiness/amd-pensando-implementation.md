# AMD Pensando implementation flow (ODSE)

This track is **discovery-first**. Do **not** start with NVIDIA switchdev
or OVS-DOCA. `ionic` on the host proves **Ethernet**, not DPU offload.

Companion: [`amd-pensando.md`](amd-pensando.md), `plugins/amd_pensando/`.

---

## Sequence

1. **PCI / hints** — `lspci -nn` matching Pensando, ionic, or DSC (word).
   An AMD CPU or a random NIC is not a DSC.
2. **ionic netdevs** — `ethtool -i` driver `ionic`; then `ethtool -S`.
   Stats are **not** hardware-offload evidence.
3. **Optional RDMA** — `rdma link show`, `lsmod` `ionic_rdma`. Missing
   RDMA is **not** an Ethernet failure.
4. **Optional** `devlink dev show` — record only; do not set eswitch.
5. **Stop** unless a later lab step proves SR-IOV, representors, OVS
   offload, or vDPA. Defaults: those fields **null** / skipped;
   `ovs_hw_offload` stays **false** (not assumed).

Code: `discovery.discover()`, `ionic.list_ionic_interfaces()`,
`get_ionic_driver_info()`, `get_ionic_stats()`,
`rdma.list_rdma_devices()`, `detect_ionic_rdma()`.

If no representors: **AMD-7** OPI Network contingency — do not fake
`br-int` ports.

---

## Gap analysis template

Copy into the lab log. Use `true` / `false` / `null` + evidence path.
Never default `true`.

| ID | Question | Result | Evidence |
|---|---|---|---|
| G-01 | `<DSC_SKU>` identified? | | lspci / vendor inventory |
| G-02 | `ionic` loaded? | | lsmod / `/proc/modules` |
| G-03 | ionic netdev + `ethtool -i`? | | interface names |
| G-04 | Firmware/driver versions? | | ethtool -i |
| G-05 | `ionic_rdma` present? | | lsmod / `rdma link` |
| G-06 | Management/SSH/PSM/OPI reachable? | | AMD-7 notes |
| G-07 | `sriov_totalvfs` > 0? | | sysfs — **discovery-required** |
| G-08 | Kernel representors (`pcivf` / portname)? | | **discovery-required** |
| G-09 | OVS `hw-offload` + `in_hw` / vendor hits? | | FN-08/17 — **do not assume** |
| G-10 | vDPA `vdpa mgmtdev`? | | **discovery-required** |
| G-11 | VFIO guest path? | | FN-09 if VFs exist |
| G-12 | If G-08 false: OPI Network mapping drafted? | | contingency, not a pass |

**Compared to NVIDIA:** no default eswitch + OVS-TC. **Intel:** no
OPI-first requirement, but OPI remains a fallback. **Marvell:** no DAO
assumption.

Cert: Kind cannot award AMD datapath levels. ionic stats ≠ L2.
