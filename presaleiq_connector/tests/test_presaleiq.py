# -*- coding: utf-8 -*-
"""Smoke tests for the PresaleIQ connector.

These cover the pure, dependency-free helpers (PDF / DOCX text extraction),
the configuration guard, and that the scheduled-action entry points run
cleanly with no pending records. They intentionally make no network calls.
"""
import io
import zipfile
import zlib

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


def _make_pdf_with_flate_text(text):
    """Build a minimal one-object PDF whose content stream is FlateDecode
    compressed and draws `text` via a Tj operator. Enough to exercise the
    decompress-then-parse path in _extract_pdf_text."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    compressed = zlib.compress(content)
    return b"%PDF-1.4\n4 0 obj\n<< /Length " + str(len(compressed)).encode() + \
           b" /Filter /FlateDecode >>\nstream\n" + compressed + \
           b"\nendstream\nendobj\n%%EOF"


def _make_docx_with_text(text):
    """Build a minimal .docx (zip + word/document.xml) containing `text`."""
    doc_xml = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
        f'{text}</w:t></w:r></w:p></w:body></w:document>'
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc_xml)
    return buf.getvalue()


@tagged("post_install", "-at_install")
class TestPresaleIQHelpers(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Lead = self.env["crm.lead"]

    def test_pdf_extraction_handles_flate_stream(self):
        raw = _make_pdf_with_flate_text("Hello discovery call")
        out = self.Lead._extract_pdf_text(raw)
        self.assertIn("Hello discovery call", out)

    def test_docx_extraction(self):
        raw = _make_docx_with_text("Customer needs incident management")
        out = self.Lead._extract_docx_text(raw)
        self.assertIn("incident management", out)

    def test_extractors_never_raise_on_garbage(self):
        self.assertEqual(self.Lead._extract_pdf_text(b"not a pdf"), "")
        self.assertEqual(self.Lead._extract_docx_text(b"not a docx"), "")

    def test_config_requires_url_and_key(self):
        ICP = self.env["ir.config_parameter"].sudo()
        ICP.set_param("presaleiq.url", "")
        ICP.set_param("presaleiq.api_key", "")
        with self.assertRaises(UserError):
            self.Lead.new({})._presaleiq_config()

    def test_cron_entrypoints_run_clean(self):
        # No pending records → should be a no-op that never raises.
        self.env["crm.lead"].cron_presaleiq_auto_poll()
        self.env["sale.order"].cron_presaleiq_auto_poll()
