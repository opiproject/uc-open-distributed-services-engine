#!/usr/bin/env python3
"""Unit tests for ovs_offload_checker parsers/assessment (no hardware required)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ovs_offload_checker import (
    assess_offload,
    build_report,
    classify_datapath_flow,
    classify_tc_block,
    load_forwarding_result,
    parse_dump_flows,
    parse_ethtool_stats,
    parse_ip_link_stats,
    parse_proc_stat_cpu_line,
    parse_tc_filters,
    render_markdown,
    render_text,
    resolve_interfaces,
    parse_args,
    write_output_dir_reports,
    _diff_ethtool_section,
    _diff_ip_link_section,
    _diff_tc_section,
    _diff_cpu,
)


class ClassifyDatapathFlowTests(unittest.TestCase):
    def test_offloaded_yes(self) -> None:
        classification, markers = classify_datapath_flow(
            "recirc_id(0),in_port(1), packets:5, bytes:500, used:0.1s, "
            "actions:2 offloaded:yes dp:tc"
        )
        self.assertEqual(classification, "probable_offloaded")
        self.assertIn("offloaded:yes", markers)

    def test_offloaded_no_is_software(self) -> None:
        classification, _ = classify_datapath_flow("... offloaded:no dp:ovs")
        self.assertEqual(classification, "software")

    def test_dp_tc_without_explicit_offloaded_marker(self) -> None:
        classification, markers = classify_datapath_flow("... dp:tc")
        self.assertEqual(classification, "probable_offloaded")
        self.assertIn("dp:tc", markers)

    def test_unknown_line(self) -> None:
        classification, markers = classify_datapath_flow("in_port(1),actions:2")
        self.assertEqual(classification, "unknown")
        self.assertEqual(markers, [])


class ParseDumpFlowsTests(unittest.TestCase):
    def test_skips_banners(self) -> None:
        raw = "netdev@ovs-netdev: hit:0 missed:0\nin_port(1),actions:2 offloaded:yes\n"
        flows = parse_dump_flows(raw)
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0]["classification"], "probable_offloaded")

    def test_empty_input(self) -> None:
        self.assertEqual(parse_dump_flows(None), [])
        self.assertEqual(parse_dump_flows(""), [])


class TcFilterParsingTests(unittest.TestCase):
    def test_in_hw_count(self) -> None:
        classification, markers = classify_tc_block(
            "filter dev p0vf0 ingress protocol ip pref 1 flower chain 0 \n"
            "  in_hw_count 2"
        )
        self.assertEqual(classification, "probable_offloaded")
        self.assertIn("in_hw_count:2", markers)

    def test_not_in_hw(self) -> None:
        classification, markers = classify_tc_block(
            "filter dev p0vf0 ingress protocol ip pref 1 flower chain 0 not_in_hw"
        )
        self.assertEqual(classification, "software")
        self.assertIn("not_in_hw", markers)

    def test_parse_tc_filters_splits_blocks(self) -> None:
        raw = (
            "filter dev p0vf0 ingress protocol ip pref 1 flower chain 0 in_hw\n"
            "  cookie deadbeef\n"
            "filter dev p0vf0 ingress protocol ip pref 2 flower chain 0 not_in_hw\n"
        )
        filters = parse_tc_filters(raw)
        self.assertEqual(len(filters), 2)
        self.assertEqual(filters[0]["classification"], "probable_offloaded")
        self.assertEqual(filters[1]["classification"], "software")

    def test_empty_input(self) -> None:
        self.assertEqual(parse_tc_filters(None), [])
        self.assertEqual(parse_tc_filters("   "), [])


class EthtoolStatsParsingTests(unittest.TestCase):
    def test_counters_and_highlight(self) -> None:
        raw = "NIC statistics:\n     rx_packets: 10\n     rx_hw_offload_packets: 4\n"
        parsed = parse_ethtool_stats(raw)
        self.assertEqual(parsed["counters"]["rx_packets"], 10)
        self.assertIn("rx_hw_offload_packets", parsed["highlighted_counters"])
        self.assertNotIn("rx_packets", parsed["highlighted_counters"])


class IpLinkStatsParsingTests(unittest.TestCase):
    def test_parses_rx_tx_blocks(self) -> None:
        raw = (
            "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500\n"
            "    link/ether 00:11:22:33:44:55 brd ff:ff:ff:ff:ff:ff\n"
            "    RX: bytes  packets  errors  dropped overrun mcast\n"
            "    1000       10       0       1       0       0\n"
            "    TX: bytes  packets  errors  dropped carrier collsns\n"
            "    2000       20       0       2       0       0\n"
        )
        stats = parse_ip_link_stats(raw)
        self.assertEqual(stats["rx_bytes"], 1000)
        self.assertEqual(stats["rx_packets"], 10)
        self.assertEqual(stats["rx_dropped"], 1)
        self.assertEqual(stats["tx_bytes"], 2000)
        self.assertEqual(stats["tx_dropped"], 2)

    def test_empty_input(self) -> None:
        self.assertEqual(parse_ip_link_stats(None), {})


class ProcStatParsingTests(unittest.TestCase):
    def test_parses_cpu_line(self) -> None:
        raw = "cpu  1000 10 500 90000 20 0 5 0 0 0\ncpu0 500 5 250 45000 10 0 2 0 0 0\n"
        parsed = parse_proc_stat_cpu_line(raw)
        self.assertEqual(parsed["user"], 1000)
        self.assertEqual(parsed["idle"], 90000)

    def test_malformed_line_returns_none(self) -> None:
        self.assertIsNone(parse_proc_stat_cpu_line("not cpu data"))
        self.assertIsNone(parse_proc_stat_cpu_line(None))


class ResolveInterfacesTests(unittest.TestCase):
    def test_merges_and_dedupes(self) -> None:
        args = parse_args(
            ["-i", "eth0", "-i", "eth1", "--interfaces", "eth1,p0", "--pf", "p0"]
        )
        self.assertEqual(resolve_interfaces(args), ["eth0", "eth1", "p0"])

    def test_empty_when_nothing_given(self) -> None:
        args = parse_args([])
        self.assertEqual(resolve_interfaces(args), [])

    def test_new_flags_parse(self) -> None:
        args = parse_args(
            [
                "--vendor",
                "nvidia_mellanox_bluefield",
                "--duration",
                "5",
                "--json",
                "--markdown",
                "--output-dir",
                "/tmp/out",
            ]
        )
        self.assertEqual(args.vendor, "nvidia_mellanox_bluefield")
        self.assertEqual(args.duration, 5.0)
        self.assertTrue(args.json)
        self.assertTrue(args.markdown)
        self.assertEqual(args.output_dir, "/tmp/out")

    def test_forwarding_result_merged_into_interfaces(self) -> None:
        args = parse_args(["-i", "pf0hpf"])
        forwarding_result = {
            "error": None,
            "configured_ports": ["pf0vf0", "pf0vf1", "pf0hpf"],
        }
        self.assertEqual(
            resolve_interfaces(args, forwarding_result),
            ["pf0hpf", "pf0vf0", "pf0vf1"],
        )

    def test_forwarding_result_with_error_not_merged(self) -> None:
        args = parse_args([])
        forwarding_result = {"error": "boom", "configured_ports": ["pf0vf0"]}
        self.assertEqual(resolve_interfaces(args, forwarding_result), [])


class LoadForwardingResultTests(unittest.TestCase):
    def _write(self, tmpdir: str, data) -> str:
        path = Path(tmpdir) / "configure_forwarding.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def test_extracts_expected_fields(self) -> None:
        data = {
            "intent": {
                "name": "odse-vm2vm-nvidia",
                "bridge": "br-int",
                "endpoints": [
                    {"name": "vm1-endpoint", "vf_pci": "0000:03:00.2", "representor": "pf0vf0"},
                    {"name": "vm2-endpoint", "vf_pci": "0000:03:00.3", "representor": "pf0vf1"},
                ],
            },
            "result": {
                "configured_ports": ["pf0vf0", "pf0vf1"],
                "configured_flows": ["in_port=pf0vf0,actions=output:pf0vf1"],
                "offload_requested": True,
                "offload_configured": True,
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, data)
            info = load_forwarding_result(path)
        self.assertIsNone(info["error"])
        self.assertEqual(info["name"], "odse-vm2vm-nvidia")
        self.assertEqual(info["bridge"], "br-int")
        self.assertEqual(info["configured_ports"], ["pf0vf0", "pf0vf1"])
        self.assertEqual(info["configured_flows"], ["in_port=pf0vf0,actions=output:pf0vf1"])
        self.assertEqual(info["vf_pci_addresses"], ["0000:03:00.2", "0000:03:00.3"])
        self.assertEqual(info["representors"], ["pf0vf0", "pf0vf1"])
        self.assertTrue(info["offload_requested"])
        self.assertTrue(info["offload_configured"])

    def test_falls_back_to_result_details_bridge(self) -> None:
        data = {"intent": {}, "result": {"details": {"bridge": "br-phy"}}}
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, data)
            info = load_forwarding_result(path)
        self.assertEqual(info["bridge"], "br-phy")

    def test_missing_file_is_error_not_exception(self) -> None:
        info = load_forwarding_result("/no/such/path/configure_forwarding.json")
        self.assertIsNotNone(info["error"])
        self.assertEqual(info["configured_ports"], [])

    def test_invalid_json_is_error_not_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.json"
            path.write_text("{not valid json", encoding="utf-8")
            info = load_forwarding_result(str(path))
        self.assertIsNotNone(info["error"])

    def test_non_object_json_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, ["not", "an", "object"])
            info = load_forwarding_result(path)
        self.assertIsNotNone(info["error"])


class BuildReportForwardingResultTests(unittest.TestCase):
    def test_forwarding_result_section_present_and_rendered(self) -> None:
        forwarding_result = {
            "error": None,
            "path": "tests/results/x/configure_forwarding.json",
            "name": "odse-vm2vm-nvidia",
            "bridge": "br-int",
            "configured_ports": ["pf0vf0"],
            "configured_flows": [],
            "offload_requested": True,
            "offload_configured": None,
            "vf_pci_addresses": ["0000:03:00.2"],
            "representors": ["pf0vf0"],
        }
        report = build_report(
            interfaces=[],
            pf=None,
            vendor=None,
            flow=None,
            bridge="br-int",
            more=True,
            datapath=None,
            duration=0.0,
            timeout=5,
            plugins=[],
            forwarding_result=forwarding_result,
            forwarding_result_path="tests/results/x/configure_forwarding.json",
        )
        self.assertEqual(report["forwarding_result"], forwarding_result)
        self.assertEqual(
            report["inputs"]["forwarding_result_path"],
            "tests/results/x/configure_forwarding.json",
        )
        text = render_text(report)
        self.assertIn("forwarding result", text)
        self.assertIn("odse-vm2vm-nvidia", text)
        md = render_markdown(report)
        self.assertIn("Forwarding Result", md)
        self.assertIn("pf0vf0", md)

    def test_no_forwarding_result_omits_section(self) -> None:
        report = build_report(
            interfaces=[],
            pf=None,
            vendor=None,
            flow=None,
            bridge=None,
            more=True,
            datapath=None,
            duration=0.0,
            timeout=5,
            plugins=[],
        )
        self.assertIsNone(report["forwarding_result"])
        self.assertNotIn("forwarding result", render_text(report))
        self.assertNotIn("Forwarding Result", render_markdown(report))


class AssessOffloadTests(unittest.TestCase):
    def _base_report(self, **overrides):
        report = {
            "dpctl_dump_flows": {"command": {"error": None}, "counts": {"total": 0}},
            "tc_filters": {"interfaces": []},
            "ethtool_stats": {"interfaces": []},
        }
        report.update(overrides)
        return report

    def test_all_offloaded_signals_yield_high_confidence(self) -> None:
        report = self._base_report(
            dpctl_dump_flows={
                "command": {"error": None},
                "counts": {"total": 2, "probable_offloaded": 2, "probable_partial": 0, "software": 0, "unknown": 0},
            },
            tc_filters={
                "interfaces": [
                    {
                        "interface": "p0vf0",
                        "ingress": {"counts": {"total": 1, "probable_offloaded": 1, "software": 0}},
                        "egress": {"counts": {"total": 0, "probable_offloaded": 0, "software": 0}},
                    }
                ]
            },
        )
        assessment = assess_offload(report)
        self.assertEqual(assessment["status"], "probable_offloaded")
        self.assertGreaterEqual(assessment["confidence"], 0.5)
        self.assertEqual(len(assessment["evidence"]), 2)

    def test_all_software_signals_yield_software_only(self) -> None:
        report = self._base_report(
            dpctl_dump_flows={
                "command": {"error": None},
                "counts": {"total": 2, "probable_offloaded": 0, "probable_partial": 0, "software": 2, "unknown": 0},
            },
        )
        assessment = assess_offload(report)
        self.assertEqual(assessment["status"], "software_only")

    def test_no_data_sources_is_unknown_not_penalized(self) -> None:
        report = self._base_report(
            dpctl_dump_flows={"command": {"error": "'ovs-appctl' not found on PATH"}, "counts": {}},
            tc_filters={"interfaces": [], "skipped": "no interfaces given"},
            ethtool_stats={"interfaces": [], "skipped": "no interfaces given"},
        )
        assessment = assess_offload(report)
        self.assertEqual(assessment["status"], "unknown")
        self.assertEqual(assessment["confidence"], 0.0)
        self.assertTrue(assessment["warnings"])

    def test_mixed_signals_yield_partial_or_lower_confidence(self) -> None:
        report = self._base_report(
            dpctl_dump_flows={
                "command": {"error": None},
                "counts": {"total": 4, "probable_offloaded": 2, "probable_partial": 0, "software": 2, "unknown": 0},
            },
        )
        assessment = assess_offload(report)
        self.assertEqual(assessment["status"], "probable_partial")

    def test_limitations_always_present(self) -> None:
        assessment = assess_offload(self._base_report())
        self.assertTrue(len(assessment["limitations"]) >= 5)


class DurationDeltaTests(unittest.TestCase):
    def test_diff_ethtool_section(self) -> None:
        before = {"interfaces": [{"interface": "p0", "counters": {"rx_hw_offload_packets": 10}}]}
        after = {"interfaces": [{"interface": "p0", "counters": {"rx_hw_offload_packets": 25}}]}
        deltas = _diff_ethtool_section(before, after)
        self.assertEqual(deltas[0]["deltas"]["rx_hw_offload_packets"], 15)
        self.assertEqual(deltas[0]["highlighted_deltas"]["rx_hw_offload_packets"], 15)

    def test_diff_ip_link_section(self) -> None:
        before = {"interfaces": [{"interface": "p0", "stats": {"rx_packets": 5}}]}
        after = {"interfaces": [{"interface": "p0", "stats": {"rx_packets": 20}}]}
        deltas = _diff_ip_link_section(before, after)
        self.assertEqual(deltas[0]["deltas"]["rx_packets"], 15)

    def test_diff_tc_section(self) -> None:
        before = {
            "interfaces": [
                {
                    "interface": "p0",
                    "ingress": {"counts": {"probable_offloaded": 1}},
                    "egress": {"counts": {"probable_offloaded": 0}},
                }
            ]
        }
        after = {
            "interfaces": [
                {
                    "interface": "p0",
                    "ingress": {"counts": {"probable_offloaded": 3}},
                    "egress": {"counts": {"probable_offloaded": 0}},
                }
            ]
        }
        deltas = _diff_tc_section(before, after)
        self.assertEqual(deltas[0]["ingress_count_deltas"]["probable_offloaded"], 2)

    def test_diff_cpu_missing_returns_none(self) -> None:
        self.assertIsNone(_diff_cpu({"proc_stat": None}, {"proc_stat": None}))

    def test_diff_cpu_present(self) -> None:
        before = {"proc_stat": {"user": 100, "idle": 900}}
        after = {"proc_stat": {"user": 150, "idle": 950}}
        deltas = _diff_cpu(before, after)
        self.assertEqual(deltas["user"], 50)
        self.assertEqual(deltas["idle"], 50)


class RenderingTests(unittest.TestCase):
    def _minimal_report(self):
        return {
            "schema_version": "odse-ovs-offload-checker/v1alpha1",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "host": "test-host",
            "inputs": {"interfaces": ["p0"], "pf": "p0", "bridge": "br-int", "vendor": None},
            "ovs_vsctl_show": {"command": {"error": None}, "stdout_lines": []},
            "dpctl_show": {"command": {"error": None}, "stdout_lines": []},
            "dpctl_dump_flows": {"command": {"command": "ovs-appctl dpctl/dump-flows -m", "error": None}, "flows": [], "counts": {"total": 0, "probable_offloaded": 0, "probable_partial": 0, "software": 0, "unknown": 0}},
            "ofctl_dump_flows": {"command": None, "stdout_lines": [], "skipped": "no --bridge given"},
            "ofproto_trace": None,
            "tc_filters": {"interfaces": []},
            "ethtool_stats": {"interfaces": []},
            "ip_link_stats": {"interfaces": []},
            "cpu_snapshot": {"source": None, "proc_stat": None},
            "duration": None,
            "assessment": {"status": "unknown", "confidence": 0.0, "evidence": [], "warnings": [], "limitations": ["a limitation"]},
            "summary": {"datapath_flows": {}, "note": "note"},
            "plugins": {"loaded": [], "results": {}},
        }

    def test_render_text_smoke(self) -> None:
        text = render_text(self._minimal_report())
        self.assertIn("ODSE OVS offload checker", text)
        self.assertIn("status: unknown", text)

    def test_render_markdown_smoke(self) -> None:
        md = render_markdown(self._minimal_report())
        self.assertIn("# OVS Offload Validation Report", md)
        self.assertIn("## Assessment", md)
        self.assertIn("`unknown`", md)


class OutputDirWritingTests(unittest.TestCase):
    def test_writes_both_by_default(self) -> None:
        report = {"schema_version": "x", "inputs": {}, "assessment": {"status": "unknown", "confidence": 0.0, "evidence": [], "warnings": [], "limitations": []}, "dpctl_dump_flows": {"counts": {}}, "tc_filters": {"interfaces": []}, "ethtool_stats": {"interfaces": []}, "duration": None, "plugins": {"loaded": []}}
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = str(Path(tmp) / "validate-offload")
            written = write_output_dir_reports(out_dir, report, write_json=True, write_markdown=True, pretty=True)
            self.assertEqual(len(written), 2)
            self.assertTrue((Path(out_dir) / "offload-validation.json").exists())
            self.assertTrue((Path(out_dir) / "offload-validation-report.md").exists())
            loaded_back = json.loads((Path(out_dir) / "offload-validation.json").read_text(encoding="utf-8"))
            self.assertEqual(loaded_back["schema_version"], "x")

    def test_json_only(self) -> None:
        report = {"schema_version": "x", "inputs": {}, "assessment": {"status": "unknown", "confidence": 0.0, "evidence": [], "warnings": [], "limitations": []}, "dpctl_dump_flows": {"counts": {}}, "tc_filters": {"interfaces": []}, "ethtool_stats": {"interfaces": []}, "duration": None, "plugins": {"loaded": []}}
        with tempfile.TemporaryDirectory() as tmp:
            written = write_output_dir_reports(tmp, report, write_json=True, write_markdown=False, pretty=False)
            self.assertEqual(len(written), 1)
            self.assertTrue(written[0].endswith("offload-validation.json"))


if __name__ == "__main__":
    unittest.main()
