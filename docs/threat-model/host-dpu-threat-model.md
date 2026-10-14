# Host / DPU/IPU threat model (ODSE)

Status: Draft — design-intent analysis, **not** a security certification,
**not** an empirical test report, **not** NSX/vDSE security-model parity.

This document models threats around **separating tenant compute (host)
from infrastructure datapath and policy (DPU/IPU)**. Companion:

- [`docs/architecture/roles-and-trust-boundary.md`](../architecture/roles-and-trust-boundary.md)
  — intended roles; §5 lists what is **not** validated
- [`docs/architecture/overview.md`](../architecture/overview.md) §4–6
- Vendor tracks under `docs/vendor-readiness/` (isolation is
  **discovery-required** for every vendor in the capability matrix)
- Intended tests: `tests/security/` (suite not complete)

**Precise claim this model supports:** ODSE *aims* to keep DPU
management, OVN policy compilation, and representor binding **off the
host workload’s attack path**, so a compromised VM/pod (or even host
root) should not have a *supported* API to reprogram fleet policy.

**Claims this model does not support:**

- That a DPU “makes the system secure.”
- That PCIe VF isolation is equivalent to a certified TEE or NSX DFW.
- That firmware/silicon is trustworthy (it is an **assumption**, §13).
- That fail-closed behaviour holds during DPU crash/upgrade (vendor-
  dependent, unverified).
- That vhost-vDPA has the same isolation as VFIO (often a **host kernel**
  control path).

Where a sentence says “must” or “should not,” that is **design intent**.
Where it says “unverified” or “vendor-dependent,” do not treat it as a
control that is in place.

```text
  [ Control plane ] --mgmt net--> [ DPU OS: OVS/OVN/OPI ]
         |                              ^
         | kube/libvirt                 | representor (DPU netdev)
         v                              | PCIe VF (data plane only, intent)
  [ Host OS / hypervisor ] ----VF----> [ eswitch / silicon ]
         |
         v
  [ Tenant VM / pod ]
```

---

## 1. Assets

| Asset | Why it matters | Typical location |
|---|---|---|
| Tenant workload (VM/pod) confidentiality/integrity | Primary business asset | Host |
| Tenant data-plane packets | Can be inspected/modified on eswitch | Host VF, DPU representor, uplink |
| OVN NB/SB and compiled ACL/NAT state | Fleet policy source of truth | Control plane; copy on DPU `ovn-controller` |
| OPI / Redfish / vendor CLI credentials | Can reimage DPU, create VFs, drain | Control plane, DPU OS |
| Representor ↔ VF mapping | Wrong map = wrong tenant on a port | DPU OS, inventory tools |
| Firmware / BFB / IPU images | Silicon trust root | Vendor supply chain, IMC/rshim |
| Telemetry (flow counts, offload flags) | Operators and automated response | DPU, host exporters |
| Management network reachability | Path to OPI/OVN/Redfish | Dedicated segment (intent) |

Assets **not** covered: storage, physical chassis, supply chain beyond
“firmware is in scope as an assumption.”

---

## 2. Trust boundaries

| Boundary | Intended property | Strength today |
|---|---|---|
| Tenant ↔ host kernel | KVM / namespace isolation ODSE does not add | Standard Linux; independent of DPU |
| Host OS ↔ DPU OS | No host SSH/API to DPU; only PCIe VF data | **Intent**; PCIe extra functions **unverified** per vendor |
| Tenant network ↔ management network | OPI/Redfish/OVN SB not routed to VFs | **Intent**; mis-cabling is an ops failure |
| Control plane ↔ DPU | Authenticated OPI/OVN only | **Intent**; TLS/auth often skeleton (`opi_services`) |
| Tenant A VF ↔ tenant B VF | Eswitch + OVN ACL | Depends on firmware + policy; **not** proven here |
| vDPA control ↔ DPU | May traverse **host** `vhost_vdpa` | Weaker / different than VFIO; vendor-specific |

The host is a **potentially compromised domain** relative to DPU
management. The DPU is **not** a second host; it is a **higher-impact
failure domain** (one card, many workloads).

On-path SmartNIC (representors on hypervisor) **shrinks** the host↔DPU
OS boundary: the “DPU OS” may be the host kernel. Call that out in the
lab; this model’s strongest split is **off-path** DPU OS.

---

## 3. Attack surfaces

| Surface | Attackers | Notes |
|---|---|---|
| Guest VF / virtio-net | Tenant | Malicious driver, DMA if IOMMU wrong, spoof MAC/VLAN |
| Host kernel SR-IOV / vfio-pci / vhost_vdpa | Host root, buggy driver | IOMMU groups, shared groups |
| Representor netdev / OVS on DPU | Whoever runs DPU OS | Mis-attach, promiscuous, wrong bridge |
| `ovn-controller` / OVSDB | Control plane, DPU root | Reprogram all flows on chassis |
| OPI adapter, Redfish, rshim, DAO CLI | Control plane, anyone who can route to it | Skeleton servers must stay loopback+TLS |
| Telemetry exporters | Local root, MITM on scrape path | Integrity of “offload is working” |
| Firmware update | Operator, supply chain | Residual: we do not audit silicon |

STRIDE is used informally below (Spoof, Tamper, Repudiate, Info
disclose, DoS, Elevation) without pretending a complete matrix.

---

## 4. Host workload compromise scenario

**Story:** Tenant VM/pod achieves guest root, then (optional) host
escape to host root.

**Intended non-effects:** cannot call OPI, cannot edit OVN NB, cannot
bind another tenant’s representor, cannot read DPU credentials.

**Realistic effects even without escape:** send any packets the VF is
allowed; attempt MAC/VLAN spoof if PF anti-spoof is off; DoS the VF /
fill eswitch tables if rate limits are absent.

**If host root is achieved:**

- Full control of **that host’s** VMs, kubelet, VF assignment,
  vhost-vdPA nodes.
- Can **not**, by *design*, open a documented management channel to
  DPU OS — unless operators placed Redfish/OPI on a host-reachable
  network (misconfiguration) or the VF/PCIe channel exposes an
  undocumented control primitive (**unverified**, matrix security
  isolation = discovery-required).
- Can still **harm** others via already-allowed data-plane policy
  (lateral movement the ACLs permit) and via **DoS** of shared PF/DPU.

**DoS / fail-open:** killing host ovnkube should **not** clear DPU
ACLs (K8S-DPU-10 intent). Unverified on hardware.

---

## 5. DPU management plane compromise scenario

**Story:** Attacker gains DPU OS root, OPI admin, Redfish admin, or
DAO/DOCA equivalent.

**Impact is larger than host root on one node:** all VFs on that card,
all representors, firmware, possibly fail-open forwarding.

Effects: rewrite OVS/OVN flows; mirror tenant traffic; re-pair
representors; flash firmware; disable ACL NFs; fake telemetry
(§9); use uplink as a pivot if routing allows.

This is **not** “contained by DPU isolation.” Isolation is meant to
stop the **host** from doing this. Compromise **of** the DPU **is**
the high-value target.

Mitigation intent: management only on isolated net; no tenant route;
authn/z; no default `opi_services` non-loopback without TLS.

---

## 6. Control plane compromise scenario

**Story:** Kubernetes API, Nova/Neutron, OVN NB/SB, or Ansible vault
is compromised.

**Impact:** fleet-wide policy rewrite — bind any VF, drain DPUs,
push NF images, read telemetry. ODSE **concentrates** this risk; it
does **not** reduce the blast radius of a control-plane breach.

A DPU does not protect tenants from a malicious scheduler/CNI that
attaches the wrong VF. Isolation host↔DPU is orthogonal.

---

## 7. PCIe and VF exposure risks

| Risk | Intent | Residual |
|---|---|---|
| DMA from VF to host memory | IOMMU (`host_base` check) | Mis-grouped IOMMU; firmware ATS bugs — unaudited |
| Shared IOMMU group | Refuse passthrough / bind all | Hardware topology |
| VF as management backdoor | VF is data plane only | **Unverified** per vendor |
| VF reset / `sriov_numvfs` | Disruptive; host-local | Availability, not confidentiality |
| vDPA vs VFIO | virtio ABI in guest | Control in **host** kernel possible |

VFIO guests load **vendor NIC drivers** — a larger guest attack
surface than virtio-net. That is a trade-off, not a security win.

---

## 8. Representor misuse risks

Representors are **DPU- (or eswitch-) local netdevs** that correspond
to VFs. Misuse:

- Attach tenant A’s representor to a bridge/port used for tenant B
  (mapping error, `vf_to_representor_map.py` vs guessed names).
- Promiscuous capture of many VFs on the DPU OS (DPU admin is
  already in the high-trust domain).
- Host process mistakenly given a representor (on-path NIC: representor
  **is** on the host — boundary weaker).
- Stale representor after VF destroy (wrong tenant packets or drops).

Guessing `eth0v1` style names is an **operational security** bug, not
just fragility.

---

## 9. Telemetry tampering risks

`TelemetrySample` / `ethtool -S` / DAO counters / offload-checker
output are **observational**. Threats:

- **Spoof “offloaded:yes”** to hide a software path (or the reverse)
  if the exporter runs on a compromised DPU or host.
- **Omit** metrics (OPI says no fake zeros — absence can also hide
  incidents).
- **Scrape path MITM** if telemetry is unauthenticated HTTP.
- Using telemetry as **enforcement** (it is not).

Operators must not treat checker JSON as an integrity proof.

---

## 10. Policy bypass risks

| Bypass | Mechanism | Status |
|---|---|---|
| Traffic never hits eswitch | Wrong bind (veth instead of VF) | Config error |
| First packets on slow path | Expected; not a bypass if later ACL matches | — |
| Fail-open on DPU restart | Vendor-dependent | Unverified |
| Host-installed iptables vs DPU OVN | Split brain | Design: DPU/OVN is intended enforcement for offloaded ports |
| NF not in path | NetworkFunction not attached | Discovery |
| Encrypted overlay the DPU cannot parse | Inner policy not applied | Out of scope unless OVN terminates it |
| Spoofcheck off | Guest MAC/VLAN | Vendor PF knobs |

ODSE does **not** claim L7/identity firewall. Bypass of “NSX-like DFW”
is out of scope because that capability is **not claimed**.

---

## 11. Mitigations

These are **controls to implement and test**, not a statement they
are complete in the repo.

| Control | Against | Notes |
|---|---|---|
| Isolated management network | §5, §6, OPI-10 | Required for the model to mean anything |
| OPI TLS; loopback default in `opi_services` | Accidental exposure | Skeleton still must not bind `0.0.0.0` without TLS |
| IOMMU / ACS | §7 DMA | `host_base` checks cmdline; does not prove groups |
| Explicit VF↔representor map | §8 | No name heuristics in core mapper |
| OVN as policy SoT; DPU ovn-controller | Host rewriting policy | Host root still can abuse allowed flows |
| Reject `hwOffload` / dryRun if unsupported | False sense of enforcement | OPI-5, OPI-7 |
| Do not place Redfish on VF network | §5 | Intel/NVIDIA docs |
| Audit VF assignment | Incident response | Host logs without DPU secrets |
| Least privilege for DAO/DOCA/rshim | §5 | Vendor-specific |

**Non-mitigations:** “we use a DPU”; Kind simulated DPU; matrix
`supported` for SR-IOV (not a security rating).

---

## 12. Validation tests

Map to `tests/security/` and related IDs as they are filled. Until
they pass on **hardware**, treat the property as **unvalidated**.

| ID | Test | Relates |
|---|---|---|
| TM-1 | From tenant VF, TCP/UDP to OPI/Redfish/OVN SB fails | §3, §5, OPI-10 |
| TM-2 | Host kubelet/ovnkube stop: DPU ACLs still drop forbidden flows | §4, K8S-DPU-10 |
| TM-3 | Cannot `ovs-vsctl` / OPI from host namespace to DPU OVS | §2 |
| TM-4 | Wrong-representor attach is refused or detected | §8 |
| TM-5 | Two VFs: tenant A cannot receive B’s unicast (OVN ACL) | §10 |
| TM-6 | Expired OPI credential rejected | api-model security |
| TM-7 | vDPA vs VFIO: document actual control-path process | §7 residual |
| TM-8 | DPU restart: record fail-open vs fail-closed | §10, vendor-readiness |
| TM-9 | Telemetry scrape unauthenticated from tenant net fails | §9 |
| TM-10 | IOMMU group for VF does not include unrelated devices | §7 |

Kind (`ci/kind-simulated-dpu`) can test **routing/policy YAML**, not
PCIe/firmware. Do not file TM-1 as passed from Kind alone.

---

## 13. Residual risks

1. **Firmware and silicon** — unaudited; a VF DMA or management
   backdoor would void host↔DPU intent.
2. **Physical / supply chain / side channels** — out of scope.
3. **No encryption of intra-chassis VF traffic** by default.
4. **Control plane god mode** — by design.
5. **vDPA host-kernel path** — isolation ≠ VFIO until measured.
6. **Fail-open windows** on DPU lifecycle — vendor-specific.
7. **On-path NIC** — representors on the host; DPU OS split weaker.
8. **Incomplete tests** — this file is the target, not a green CI
   report. Matrix **security isolation = discovery-required** for AMD,
   NVIDIA, Intel, and Marvell.
9. **Telemetry is not a control.**
10. **No Common Criteria / FIPS / “DPU equals secure” claim.**

Operators should describe ODSE as: *architectural separation of
datapath policy from tenant hosts, with residual PCIe, firmware, and
control-plane risk*. They should not describe it as: *secure because
the NIC is a DPU*.
