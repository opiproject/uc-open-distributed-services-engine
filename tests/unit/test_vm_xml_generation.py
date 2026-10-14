"""Libvirt XML generation from vm2vm config (no virsh)."""

from __future__ import annotations

import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm_vfio import (
    deterministic_domain_uuid,
    render_libvirt_xml,
    split_pci_address,
)

TEMPLATE = (ROOT / "qemu" / "libvirt-xml" / "vm-vfio-template.xml").read_text(encoding="utf-8")


class VmXmlGenerationTests(unittest.TestCase):
    def test_splits_pci_and_fills_placeholders(self):
        xml = render_libvirt_xml(
            TEMPLATE,
            {
                "name": "odse-vm2vm-vm1",
                "memory_mb": 2048,
                "vcpus": 2,
                "image": "/var/lib/libvirt/images/vm1.qcow2",
                "vf_pci": "0000:03:00.2",
                "mac": "52:54:00:00:00:11",
            },
            include_vf_hostdev=True,
        )
        self.assertIn("<name>odse-vm2vm-vm1</name>", xml)
        self.assertIn("2097152", xml)
        self.assertIn("/var/lib/libvirt/images/vm1.qcow2", xml)
        self.assertIn("domain='0x0000'", xml)
        self.assertIn("bus='0x03'", xml)
        self.assertIn("slot='0x00'", xml)
        self.assertIn("function='0x2'", xml)
        self.assertIn("52:54:00:00:00:11", xml)
        self.assertNotIn("@VM_NAME@", xml)
        self.assertEqual(split_pci_address("0000:03:00.2")["function"], "2")
        self.assertNotIn("@VM_UUID@", xml)
        self.assertIn("<uuid>%s</uuid>" % deterministic_domain_uuid("odse-vm2vm-vm1"), xml)
        ET.fromstring(xml)  # must be well-formed XML (see test_template_is_well_formed_xml)

    def test_omits_hostdev_when_vf_unknown(self):
        xml = render_libvirt_xml(
            TEMPLATE,
            {
                "name": "odse-vm2vm-vm1",
                "memory_mb": 2048,
                "vcpus": 2,
                "image": "/tmp/vm1.qcow2",
                "vf_pci": None,
            },
            include_vf_hostdev=False,
        )
        self.assertNotIn("type='hostdev'", xml)
        self.assertNotIn("ODSE_VF_HOSTDEV", xml)
        self.assertIn("odse-vm2vm-vm1", xml)

    def test_rejects_malformed_pci(self):
        with self.assertRaises(ValueError):
            render_libvirt_xml(
                TEMPLATE,
                {"name": "bad", "memory_mb": 512, "vcpus": 1, "image": "/tmp/x.qcow2", "vf_pci": "not-a-pci"},
                include_vf_hostdev=True,
            )

    def test_uuid_is_stable_across_repeated_renders(self):
        """Regression: re-running `create-vms --apply` after a partial
        failure (define succeeded, start failed for an unrelated reason,
        e.g. base-image permissions) must redefine the *same* domain, not
        collide with the already-persisted one under a freshly generated
        random UUID. libvirt's actual failure mode when that happens is
        `operation failed: domain '<name>' already exists with uuid
        <other-uuid>`. A stable per-name UUID makes repeated `virsh
        define` calls idempotent instead.
        """
        vm = {
            "name": "odse-vm2vm-vm1",
            "memory_mb": 2048,
            "vcpus": 2,
            "image": "/tmp/vm1.qcow2",
            "vf_pci": None,
        }
        first = render_libvirt_xml(TEMPLATE, vm, include_vf_hostdev=False)
        second = render_libvirt_xml(TEMPLATE, dict(vm), include_vf_hostdev=False)
        first_uuid = ET.fromstring(first).findtext("uuid")
        second_uuid = ET.fromstring(second).findtext("uuid")
        self.assertIsNotNone(first_uuid)
        self.assertEqual(first_uuid, second_uuid)

    def test_different_vm_names_get_different_uuids(self):
        uuid1 = deterministic_domain_uuid("odse-vm2vm-vm1")
        uuid2 = deterministic_domain_uuid("odse-vm2vm-vm2")
        self.assertNotEqual(uuid1, uuid2)

    def test_explicit_uuid_override_is_respected(self):
        pinned = "11111111-2222-3333-4444-555555555555"
        xml = render_libvirt_xml(
            TEMPLATE,
            {
                "name": "odse-vm2vm-vm1",
                "uuid": pinned,
                "memory_mb": 2048,
                "vcpus": 2,
                "image": "/tmp/vm1.qcow2",
                "vf_pci": None,
            },
            include_vf_hostdev=False,
        )
        self.assertEqual(ET.fromstring(xml).findtext("uuid"), pinned)

    def test_template_is_well_formed_xml(self):
        """Regression: the raw template itself (before placeholder
        substitution -- '@...@' placeholders are not valid XML content on
        their own, but comments/structure must still be well-formed) must
        not contain a literal '--' inside any <!-- ... --> comment. XML
        forbids '--' anywhere in a comment body (only allowed immediately
        before the closing '-->'); libvirt's libxml2 enforces this and
        `virsh define` hard-fails with "Double hyphen within comment" if
        it's violated. Substring-only assertions elsewhere in this file
        would not have caught that; only an actual XML parse does.
        """
        rendered = render_libvirt_xml(
            TEMPLATE,
            {
                "name": "well-formed-check",
                "memory_mb": 1024,
                "vcpus": 1,
                "image": "/tmp/well-formed-check.qcow2",
                "vf_pci": "0000:03:00.2",
                "mac": "52:54:00:00:00:99",
            },
            include_vf_hostdev=True,
        )
        try:
            ET.fromstring(rendered)
        except ET.ParseError as exc:
            self.fail(
                "rendered libvirt XML is not well-formed (virsh define would "
                "reject it too): %s" % exc
            )


if __name__ == "__main__":
    unittest.main()
