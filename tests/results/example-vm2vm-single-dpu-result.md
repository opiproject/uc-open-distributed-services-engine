> **This is a fake, illustrative report.** Every value below is
> placeholder data (documentation-range IPs, invented PCI addresses,
> made-up counters) generated for
> `tests/results/nvidia-bluefield/example-fake-run/` and rendered by
> `tools/report_generator.py`. It is checked into git as a reference
> for what a real `result-summary.md` looks like — do not treat any
> fact in it as a real lab result. See
> [`docs/test-plan/result-report-template.md`](../../docs/test-plan/result-report-template.md)
> for what each section means and where its data would come from on a
> real run.

---

# VM-to-VM single-DPU lab result — nvidia-bluefield / example-fake-run

Generated: 2026-08-14T09:35:00+00:00 by `tools/report_generator.py`.

This report separates *forwarding configured*, *traffic works*, *offload requested*, *offload probably active*, and *offload confirmed* as five independent claims (see `docs/test-plan/forwarding-validation.md`). Do not read a PASS verdict below as hardware-offload proof unless section 16 says so.

## 1. Test metadata

- **run_id**: example-fake-run
- **vendor**: nvidia-bluefield
- **input_dir**: tests/results/nvidia-bluefield/example-fake-run
- **report_generated_at**: 2026-08-14T09:35:00+00:00
- **discover_timestamp**: 2026-08-14T09:00:00+00:00
- **stages_present**:
  - attach_vfs
  - cleanup
  - collect_evidence
  - configure_forwarding
  - configure_vm_ips
  - create_vms
  - discover
  - discover_representors
  - prepare_host
  - prepare_ovs
  - prepare_switchdev
  - prepare_vfs
  - run_traffic
  - validate_config
  - validate_offload
- **stages_missing**: _none_

## 2. Server details

- **host_name**: lab-host-01.example.invalid
- **management_interface**: eno1
- **hostname**: lab-host-01.example.invalid
- **platform**: Linux-5.15.0-x86_64-with-glibc2.35

## 3. Vendor and DPU/IPU details

- **vendor_type**: nvidia-bluefield
- **pf_name**: ens1f0np0
- **pf_pci**: 0000:03:00.0
- **num_vfs**: 2
- **preferred_datapath**: system
- **vendor_hints_from_inventory**:
  - confidence=high, reason=PCI vendor 15b3 device a2d6 matches BlueField-3, vendor_key=nvidia_mellanox_bluefield

## 4. Firmware and driver details

- **pf_name**: ens1f0np0
- **driver**: mlx5_core
- **firmware_version**: 32.41.1000
- **bus_info**: 0000:03:00.0

## 5. Kernel and OS details

- **system**: Linux
- **kernel_release**: 5.15.0-105-generic
- **machine**: x86_64
- **platform**: Linux-5.15.0-x86_64-with-glibc2.35

## 6. PF/VF/representor mapping

- **mapping_method_seen**:
  - devlink_pcivf
- **mappings**:
  - vf_index=0, vf_pci=0000:03:00.2, representor_netdev=pf0vf0, mapping_method=devlink_pcivf, no_representor_reason=_not available_
  - vf_index=1, vf_pci=0000:03:00.3, representor_netdev=pf0vf1, mapping_method=devlink_pcivf, no_representor_reason=_not available_
- **error**: _not available_

## 7. OVS bridge configuration

- **bridge**: br-int
- **configured_ports**:
  - pf0vf0
  - pf0vf1
- **prepare_ovs_backend_result**:
  - **details**:
    - **bridge**: br-int
    - **hw_offload**: yes
  - **message**: br-int created with hw-offload enabled
  - **operation**: configure_ovs_bridge
  - **status**: ok
  - **vendor**: nvidia-bluefield

## 8. VM definitions

- **vms**:
  - name=vm-a, vf_index=0, vf_pci=0000:03:00.2, representor=pf0vf0, mac=52:54:00:aa:00:01, created=yes, error=_not available_
  - name=vm-b, vf_index=1, vf_pci=0000:03:00.3, representor=pf0vf1, mac=52:54:00:aa:00:02, created=yes, error=_not available_

## 9. VM interface configuration

- **vms**:
  - name=vm-a, vf_pci=0000:03:00.2, vf_attach_skipped=_not available_, vf_attach_message=_not available_, ip_cidr=198.51.100.10/24, management_host=192.0.2.101, ip_configure_error=_not available_
  - name=vm-b, vf_pci=0000:03:00.3, vf_attach_skipped=_not available_, vf_attach_message=_not available_, ip_cidr=198.51.100.11/24, management_host=192.0.2.102, ip_configure_error=_not available_

## 10. Forwarding Configuration

- **user_intent**:
  - **name**: vm-a-to-vm-b
  - **type**: l2-vm-to-vm
  - **mode**: bridge
- **backend_selected**: nvidia-bluefield
- **control_entity**: ovs-vsctl
- **forwarding_entity**: br-int
- **configured_ports**:
  - pf0vf0
  - pf0vf1
- **configured_flows**: _none_
- **offload_requested**: yes
- **offload_configured**: yes
- **offload_confirmed_or_probable**: probable (probable_offloaded, confidence=0.83) -- not confirmed
- **result_success**: yes
- **result_message**: added pf0vf0/pf0vf1 to br-int with other_config:hw-offload=true

## 11. Traffic test results

- **error**: _not available_
- **dry_run**: no
- **commands**:
  - kind=ping, executed=yes, returncode=0, dry_run=no, stdout_tail=5 packets transmitted, 5 received, 0% packet loss, stderr_tail=_not available_
    - **argv**:
      - ssh
      - labuser@192.0.2.101
      - ping
      - -c
      - 5
      - 198.51.100.11
  - kind=iperf3-server, executed=yes, returncode=0, dry_run=no, stdout_tail=_not available_, stderr_tail=_not available_
    - **argv**:
      - ssh
      - labuser@192.0.2.102
      - iperf3
      - -s
      - -1
      - -D
  - kind=iperf3-client, executed=yes, returncode=0, dry_run=no, stdout_tail=[ ID] Interval  Transfer  Bitrate\n[  5] 0.00-10.00 sec 21.6 GBytes 18.5 Gbits/sec, stderr_tail=_not available_
    - **argv**:
      - ssh
      - labuser@192.0.2.101
      - iperf3
      - -c
      - 198.51.100.11
      - -t
      - 10

## 12. OVS flow evidence

- **ovs_vsctl_show**:
  - **stdout_lines**:
    - Bridge br-int
    -     Port pf0vf0
    -         Interface pf0vf0
    -     Port pf0vf1
    -         Interface pf0vf1
- **dpctl_show**:
  - **stdout_lines**:
    - netdev@ovs-netdev: hit:184213 missed:12
- **dpctl_dump_flows**:
  - **counts**:
    - **offloaded**: 3
    - **software**: 1
    - **total**: 4
  - **stdout_lines**:
    - recirc_id(0),in_port(3),eth(...),actions:3, packets:91422, bytes:120219144, used:0.210s, offloaded:yes, dp:tc
- **ofctl_dump_flows**:
  - **stdout_lines**:
    - cookie=0x0, duration=612.4s, table=0, n_packets=91422, n_bytes=120219144

## 13. TC filter evidence

- **tc_filters**:
  - **interfaces**:
    - in_hw_count=2, name=pf0vf0, not_in_hw_count=0
    - in_hw_count=2, name=pf0vf1, not_in_hw_count=0

## 14. ethtool counter evidence

- **ethtool_stats**:
  - **interfaces**:
    - name=pf0vf0, rx_packets=91422, tx_packets=91410
    - name=pf0vf1, rx_packets=91410, tx_packets=91422
- **ip_link_stats**:
  - **interfaces**:
    - name=pf0vf0, rx_bytes=120219144, tx_bytes=120198812

## 15. CPU usage comparison

- **cpu_snapshot**:
  - **idle**: 91820.5
  - **system**: 210.1
  - **user**: 812.4
- **duration_section**:
  - **cpu_delta**:
    - **user_pct**: 3.1
  - **duration_seconds**: 10
  - **ethtool_delta**:
    - **pf0vf0**:
      - **rx_packets**: 91422
- **collect_evidence_cpu_stats**: cpu  812 0 210 91820 0 0 0 0 0 0

## 16. Offload confidence assessment

- **status**: probable_offloaded
- **confidence**: 0.83
- **evidence**:
  - [dpctl_dump_flows] 3 of 4 flows show offloaded:yes, dp:tc
  - [tc_filters] pf0vf0/pf0vf1 filters report in_hw for all entries
  - [ethtool_stats] representor counters increasing consistent with offload path
- **warnings**: _none_
- **limitations**:
  - Heuristic based on OVS/TC markers only; not a hardware-verified fact.
  - A future vendor plugin performing an ASIC-side flow cross-check would be required for a 'confirmed' verdict.

## 17. Failures and anomalies

- **errors**: _none_

## 18. Cleanup result

- **ran**: yes
- **dry_run**: no
- **apply**: yes
- **stages_run**:
  - collect_final_state
  - delete_ovs_bridge
  - detach_vfs
  - remove_ovs_ports
  - reset_sriov_numvfs
  - restart_ovs
  - restore_switchdev_legacy
  - restore_vf_driver
  - stop_traffic_tools
  - stop_vms
- **rollback_risks**:
  - sriov_numvfs will be reset to 0 on ens1f0np0, destroying VF 0000:03:00.2 and 0000:03:00.3.
  - OVS bridge br-int will be deleted; any manually added ports on it are also removed.

## 19. Final verdict

**PASS_OFFLOAD_PROBABLE**

traffic succeeded; offload checker assessment=probable_offloaded confidence=0.83

## 20. Next actions

- Archive this run directory as evidence; consider a longer --duration validate-offload pass for a CPU-usage before/after comparison.
