"""Lab YAML loader for vendorctl. Unknown facts stay null; no invented PFs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

LOG = logging.getLogger(__name__)

VENDOR_ALIASES = {
    "nvidia": "nvidia-bluefield",
    "nvidia_bluefield": "nvidia-bluefield",
    "nvidia-bluefield": "nvidia-bluefield",
    "bluefield": "nvidia-bluefield",
    "intel": "intel-ipu",
    "intel_ipu": "intel-ipu",
    "intel-ipu": "intel-ipu",
    "marvell": "marvell-octeon",
    "marvell_octeon": "marvell-octeon",
    "marvell-octeon": "marvell-octeon",
    "octeon": "marvell-octeon",
    "amd": "amd-pensando",
    "amd_pensando": "amd-pensando",
    "amd-pensando": "amd-pensando",
    "pensando": "amd-pensando",
}


def normalize_vendor(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return VENDOR_ALIASES.get(str(name).strip().lower(), str(name).strip().lower())


def _parse_scalar(raw: str) -> Any:
    text = raw.strip()
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    if text in ("true", "True", "yes"):
        return True
    if text in ("false", "False", "no"):
        return False
    if text in ("[]",):
        return []
    if text in ("{}",):
        return {}
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    return text


def _strip_inline_comment(content: str) -> str:
    """Drop unquoted ` # ...` comments. Quoted hashes are kept."""
    in_single = False
    in_double = False
    for i, ch in enumerate(content):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or content[i - 1].isspace():
                return content[:i].rstrip()
    return content


def parse_simple_yaml(text: str) -> Dict[str, Any]:
    """Minimal YAML subset (mappings, lists, scalars, comments)."""
    lines: List[tuple] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = _strip_inline_comment(raw.strip())
        if not content:
            continue
        lines.append((indent, content))

    def parse_block(index: int, indent: int) -> tuple:
        mapping: Dict[str, Any] = {}
        sequence: List[Any] = []
        is_list: Optional[bool] = None
        while index < len(lines):
            cur_indent, content = lines[index]
            if cur_indent < indent:
                break
            if cur_indent > indent:
                raise ValueError("invalid YAML indent near %r" % content)
            if content.startswith("- "):
                if is_list is False:
                    raise ValueError("mixed mapping/list at %r" % content)
                is_list = True
                item_raw = content[2:].strip()
                index += 1
                child = None
                if index < len(lines) and lines[index][0] > cur_indent:
                    child, index = parse_block(index, lines[index][0])
                if ":" in item_raw and not item_raw.startswith("http"):
                    key, _, val = item_raw.partition(":")
                    node: Dict[str, Any] = {key.strip(): _parse_scalar(val)}
                    if isinstance(child, dict):
                        node.update(child)
                    sequence.append(node)
                elif item_raw:
                    sequence.append(_parse_scalar(item_raw) if child is None else child)
                else:
                    sequence.append(child if child is not None else {})
                continue
            if is_list is True:
                break
            is_list = False
            key, _, val = content.partition(":")
            key = key.strip()
            val = val.strip()
            index += 1
            if val:
                mapping[key] = _parse_scalar(val)
                continue
            if index < len(lines) and lines[index][0] > cur_indent:
                child, index = parse_block(index, lines[index][0])
                mapping[key] = child
            else:
                mapping[key] = None
        if is_list:
            return sequence, index
        return mapping, index

    root, _ = parse_block(0, 0)
    if not isinstance(root, dict):
        raise ValueError("lab config root must be a mapping")
    return root


def load_lab_config(path: Union[str, Path]) -> Dict[str, Any]:
    target = Path(path)
    raw = target.read_text(encoding="utf-8")
    data: Any = None
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw)
    except ImportError:
        LOG.info("PyYAML not installed; using simple YAML parser for %s", target)
        data = parse_simple_yaml(raw)
    if not isinstance(data, dict):
        raise ValueError("%s must contain a YAML mapping" % target)
    if data.get("schema") not in (None, "odse-lab-config/v1alpha1"):
        LOG.warning("unexpected lab schema %r", data.get("schema"))
    return data


def config_dry_run(lab: Optional[Mapping[str, Any]]) -> bool:
    if not lab:
        return True
    value = lab.get("dry_run")
    if value is None:
        return True
    return bool(value)


def select_device(
    lab: Optional[Mapping[str, Any]],
    vendor: str,
    host_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not lab:
        return None
    want = normalize_vendor(vendor)
    for host in lab.get("hosts") or []:
        if not isinstance(host, dict):
            continue
        if host_id and host.get("id") != host_id:
            continue
        for dev in host.get("dpu_devices") or []:
            if not isinstance(dev, dict):
                continue
            if normalize_vendor(dev.get("vendor")) == want:
                out = dict(dev)
                out["_host_id"] = host.get("id")
                out["_hostname"] = host.get("hostname")
                return out
    return None


def first_pf(device: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not device:
        return {}
    pfs = device.get("pfs") or []
    if pfs and isinstance(pfs[0], dict):
        return dict(pfs[0])
    return {}


def first_bridge(device: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not device:
        return {}
    bridges = device.get("bridges") or []
    if bridges and isinstance(bridges[0], dict):
        return dict(bridges[0])
    return {}


def intel_plugin_config(device: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not device:
        return {}
    cfg: Dict[str, Any] = {}
    if device.get("redfish_endpoint"):
        cfg["redfish_base"] = device.get("redfish_endpoint")
    if device.get("opi_endpoint"):
        cfg["opi_endpoint"] = device.get("opi_endpoint")
    if device.get("management_ip"):
        cfg["imc_address"] = device.get("management_ip")
    cfg["opi_mode"] = device.get("opi_mode") or "http"
    return cfg


def public_lab_view(lab: Optional[Mapping[str, Any]], device: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Safe snippet for OperationResult.details (no secrets)."""
    view: Dict[str, Any] = {
        "schema": (lab or {}).get("schema"),
        "dry_run": config_dry_run(lab),
    }
    if device:
        view["device_id"] = device.get("id")
        view["vendor"] = device.get("vendor")
        view["host_id"] = device.get("_host_id")
        view["management_ip"] = device.get("management_ip")
        view["opi_endpoint"] = device.get("opi_endpoint")
        view["redfish_endpoint"] = device.get("redfish_endpoint")
    return view
