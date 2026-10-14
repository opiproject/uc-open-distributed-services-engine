# vendor_amd_pensando

Vendor-specific Ansible role for **AMD Pensando DSC**. Companion:

[`docs/vendor-readiness/amd-pensando.md`](../../../docs/vendor-readiness/amd-pensando.md)

This track is **discovery-first**. The role must **not** assume SR-IOV,
kernel switchdev, representors, or **OVS hardware offload**. Those are
plan AMD-4…6 *verify whether* steps. Offload is **forbidden** in
automation until a fact says representors (or a documented vendor
offload API) exist.

`ionic` on the host is necessary and **not sufficient**.

## Intended responsibilities (TODO)

1. **Read-only:** `modinfo ionic`, PCI `1dd8`, `ethtool -i`,
   `sriov_totalvfs` presence, `devlink port show`, management probe.
2. Record `<PENSANDO_SRIOV>` and `<PENSANDO_REPR>` as
   `unknown` / `not-exposed` / `kernel-switchdev` / `vendor-api`.
3. Mutate SR-IOV only if `amd_pensando_allow_sriov` (default **false**)
   and sysfs/vendor docs support it.
4. Call `ovs_setup` hw-offload only if
   `amd_pensando_try_ovs_offload` (default **false**) **and**
   representor discovery passed.
5. If no representors: do not fake bridges; point operators at OPI
   Network API contingency (AMD-7) and `vendor_amd.py` (still
   `UnsupportedOperation`).

Do **not** depend on `switchdev_setup` / `ovs_setup` by default. Do
**not** reuse NVIDIA eswitch hooks.

## Safety

- Default: facts and debug only.
- No firmware/PSM writes unless an operator-supplied command var is
  set (not hardcoded).
- Do not use `<PF_HOST>` as the only SSH path if you later enable
  SR-IOV.

## Alignment with other vendors

| This role must not copy | From |
|---|---|
| Arm switchdev-by-default / host legacy PF | NVIDIA DPU mode |
| Redfish IMC as given | Intel IPU |
| DAO / DPDK representors as given | Marvell |

See gap table in `amd-pensando.md` §14.
