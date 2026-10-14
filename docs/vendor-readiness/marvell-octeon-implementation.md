# Marvell OCTEON implementation flow (ODSE)

How to enable **OCTEON + DAO/DPDK** in this repo. This is **not** the
NVIDIA kernel OVS-TC / OVS-DOCA sequence and **not** Intel OPI-first.

Companion: [`marvell-octeon.md`](marvell-octeon.md),
`plugins/marvell_octeon/`. Destructive DAO actions stay behind
**`apply=True`** (CLI `--apply`); placeholders **refuse to invent** a
vendor argv even then.

---

## 1. DPDK representor model

On OCTEON, a VF’s “representor” is often a **DPDK poll-mode port**
inside `<DAO_OVS_APP>`, not a kernel netdev with `devlink flavour
pcivf`.

| | Kernel switchdev | DPDK / DAO |
|---|---|---|
| Visible as | `/sys/class/net` | port id in the DPDK primary |
| Discovery | `vf_to_representor_map.py` | `dpdk-devbind.py -s` + DAO docs (`<DPDK_REPR>`) |
| Attach | `ovs-vsctl add-port` type=system | DAO-specific or `type=dpdk` — **not guessed** |

`plugins.marvell_octeon.discovery` collects PCI (Marvell / OCTEON /
cn10k / cnxk), `ip -d link`, optional **devbind** status, optional
`ovs-vsctl show`, and **DAO env/binaries** (`DAO_*`, `OCTEON_SDK`,
`RTE_SDK`, `dpdk-devbind.py`, `dao-ovs`) **without executing** DAO.

Empty `representor_netdev` is **correct** until a vendor plugin maps
`<DPDK_REPR>`. `configure_representor_placeholder()` records that
intent only.

---

## 2. DAO OVS offload model

**Generic distro OVS** (`hw-offload=true`, TC `in_hw`) is a different
datapath. **DAO OVS** is a Marvell process (or OVS built with DAO)
that programs **hardware** via **`rte_flow`** (or a DAO wrapper).

`check_dao_available()` looks for env/config, not a frozen binary
name. `start_dao_ovs_offload_placeholder()` does nothing unless DAO
was discovered **and** `apply=True`. If apply is set but the argv is
still a placeholder, the call **errors** rather than guessing
`<DAO_OVS_APP>`.

Hugepages, CPU mask, and firmware load stay in
`<MARVELL_DAO_DOC_URL>` for `<DAO_VERSION>`.

---

## 3. First-packet software path

Before a flow is offloaded:

- Packets hit the **DAO/OVS userspace classifier** (slow path /
  upcall analog).
- `ovs-appctl dpctl/dump-flows` may show software-only or nothing
  useful; **do not** treat missing `offloaded:yes` as failure yet.
- Counters on the classifier thread / PMD should increase.

This is **expected**. A lab that never starts DAO and only pings
through kernel OVS is **not** this track.

---

## 4. Post-offload hardware path

After `rte_flow` (or DAO equivalent) installs:

- Subsequent packets should **not** hairpin through the userspace
  classifier at line rate.
- Proof is **DAO/DPDK/HW counters** (`<RTE_FLOW_DUMP_CMD>`), not
  kernel TC `in_hw`.
- `collect_dao_telemetry_placeholder()` returns **empty** samples
  until that dump is wired — no fake zeros (OPI-6).

Mismatch between OVN/OVS intent and `rte_flow` match is a control
bug, not “OVS offload yes”.

---

## 5. Validation plan

| Step | What | Pass |
|---|---|---|
| FN-01 | `discover()` | OCTEON PCI or honest skip |
| Devbind | `list_dpdk_ports()` | Parsed PCI/drv; no bind |
| DAO present | `check_dao_available()` | skip if no env/app |
| Representors | DPDK ids documented | null netdev OK |
| First packet | ping + classifier stats | software path recorded |
| Post-offload | `<RTE_FLOW_DUMP_CMD>` + ping | HW hits; userspace PPS drop **or** skip with reason |
| Checker | Marvell plugin later | `unknown` valid; no fake `dp:tc` |
| Cert | L2 | Not from Kind; not from kernel-only OVS |

`MarvellOcteonBackend.configure_switchdev` / `configure_ovs_bridge`
**skip** NVIDIA-style eswitch/OVS-TC. OVN-K8s DPU and OpenStack
off-path remain **future**.
