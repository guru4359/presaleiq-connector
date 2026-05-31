# -*- coding: utf-8 -*-
import base64
import re
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from .presaleiq_mixin import PresaleIQMixin

_logger = logging.getLogger(__name__)


class PresaleIQSaleOrder(models.Model):
    _inherit = 'sale.order'   # STRING, not list — unique class name avoids MRO / field collision

    # ── Analysis fields ───────────────────────────────────────────────────
    presaleiq_analysis_url = fields.Char(
        string='PresaleIQ Analysis URL',
        readonly=True,
        help='Link to the latest PresaleIQ analysis for this record.',
    )
    presaleiq_analysis_id = fields.Integer(
        string='PresaleIQ Analysis ID',
        readonly=True,
        default=0,
    )
    presaleiq_last_analyzed = fields.Datetime(
        string='Last Analyzed',
        readonly=True,
    )
    presaleiq_story_count = fields.Integer(
        string='User Stories Generated',
        readonly=True,
        default=0,
    )
    presaleiq_status = fields.Char(
        string='Analysis Status',
        readonly=True,
        default='',
        help='Latest status returned by PresaleIQ (pending / processing / complete / error).',
    )

    # ── License Sizing fields ─────────────────────────────────────────────
    presaleiq_license_url = fields.Char(
        string='License Sizing URL',
        readonly=True,
        help='Link to the latest PresaleIQ License Sizing report.',
    )
    presaleiq_license_analysis_id = fields.Integer(
        string='License Sizing Analysis ID',
        readonly=True,
        default=0,
    )
    presaleiq_license_status = fields.Char(
        string='License Sizing Status',
        readonly=True,
        default='',
    )

    # ── Live Agent fields ─────────────────────────────────────────────────
    presaleiq_session_id = fields.Integer(
        string='Live Agent Session ID',
        readonly=True,
        default=0,
    )
    presaleiq_session_url = fields.Char(
        string='Live Agent Dashboard URL',
        readonly=True,
        help='Battle card dashboard for the active/last live session.',
    )

    # ── Transcript upload ─────────────────────────────────────────────────
    presaleiq_transcript_file = fields.Binary(
        string='Discovery Transcript',
        attachment=True,
        help='Upload a .txt, .docx, or .pdf discovery call transcript. '
             'This will be used as the primary input for PresaleIQ analysis.',
    )
    presaleiq_transcript_filename = fields.Char(
        string='Transcript Filename',
    )

    # ── Platform override ─────────────────────────────────────────────────
    presaleiq_platform = fields.Selection(
        selection=[
            ('servicenow',   'ServiceNow'),
            ('bmc_helix',    'BMC Helix'),
            ('bmc_controlm', 'BMC Control-M'),
            ('salesforce',   'Salesforce'),
            ('atlassian',    'Atlassian (Jira)'),
            ('ivanti',       'Ivanti'),
            ('manageengine', 'ManageEngine'),
            ('zendesk',      'Zendesk'),
            ('sailpoint',    'SailPoint'),
            ('cyberark',     'CyberArk'),
            ('saviynt',      'Saviynt'),
            ('microsoft',    'Microsoft (Entra / M365)'),
        ],
        string='Platform',
        help='Platform being sold for this sale order. '
             'Overrides the global PresaleIQ platform setting. '
             'Determines which questionnaire, prompts, and licence sizing logic are used.',
    )

    # ── Push fields ───────────────────────────────────────────────────────
    presaleiq_push_status = fields.Char(
        string='Last Push Status',
        readonly=True,
        default='',
        help='Result of the last "Push to Platform" action.',
    )
    presaleiq_push_stories_count = fields.Integer(
        string='Stories Pushed',
        readonly=True,
        default=0,
    )

    # ── Inject all shared methods from PresaleIQMixin ─────────────────────
    _presaleiq_config                        = PresaleIQMixin._presaleiq_config
    _presaleiq_platform_label                = PresaleIQMixin._presaleiq_platform_label
    _presaleiq_http                          = staticmethod(PresaleIQMixin._presaleiq_http)
    _presaleiq_poll_background               = PresaleIQMixin._presaleiq_poll_background
    _presaleiq_attach_documents              = PresaleIQMixin._presaleiq_attach_documents
    _extract_docx_text                       = staticmethod(PresaleIQMixin._extract_docx_text)
    _extract_pdf_text                        = staticmethod(PresaleIQMixin._extract_pdf_text)
    _presaleiq_extract_docx_text             = PresaleIQMixin._presaleiq_extract_docx_text
    _presaleiq_find_questionnaire_attachment = PresaleIQMixin._presaleiq_find_questionnaire_attachment
    action_analyze_with_presaleiq            = PresaleIQMixin.action_analyze_with_presaleiq
    action_get_questionnaire                 = PresaleIQMixin.action_get_questionnaire
    action_license_sizing_presaleiq          = PresaleIQMixin.action_license_sizing_presaleiq
    action_push_to_platform_presaleiq        = PresaleIQMixin.action_push_to_platform_presaleiq
    action_open_presaleiq                    = PresaleIQMixin.action_open_presaleiq
    action_refresh_presaleiq_status          = PresaleIQMixin.action_refresh_presaleiq_status
    action_refresh_license_sizing_status     = PresaleIQMixin.action_refresh_license_sizing_status
    _cron_auto_poll                          = PresaleIQMixin._cron_auto_poll

    # ── Cron entry point ──────────────────────────────────────────────────

    @api.model
    def cron_presaleiq_auto_poll(self):
        """Scheduled action: auto-poll all pending analyses + license sizings
        on sale.order and silently attach documents when complete.
        Runs every 2 minutes via ir.cron — no user action required.
        """
        self._cron_auto_poll()

    # ── Sales-specific method ─────────────────────────────────────────────

    def _presaleiq_build_transcript(self):
        """Build transcript from Sales Order fields."""
        lines = []
        if self.name:
            lines.append(f'Sales Order: {self.name}')
        if self.partner_id:
            lines.append(f'Customer: {self.partner_id.name}')
        if self.user_id:
            lines.append(f'Salesperson: {self.user_id.name}')
        if self.date_order:
            lines.append(f'Order Date: {self.date_order}')

        # Order lines — product scope
        if self.order_line:
            lines.append('\nScope / Products:')
            for line in self.order_line:
                product_name = line.product_id.name if line.product_id else ''
                desc = line.name or product_name
                qty  = line.product_uom_qty
                uom  = line.product_uom.name if line.product_uom else ''
                lines.append(f'  - {desc} (qty: {qty} {uom})')

        # Internal notes
        if self.note:
            clean = re.sub(r'<[^>]+>', ' ', self.note or '').strip()
            if clean:
                lines.append(f'\nNotes:\n{clean}')

        # Transcript upload (same field as CRM)
        if self.presaleiq_transcript_file:
            try:
                raw  = base64.b64decode(self.presaleiq_transcript_file)
                name = (self.presaleiq_transcript_filename or '').lower()
                if name.endswith('.docx'):
                    text = self._extract_docx_text(raw)[:12000]
                elif name.endswith('.pdf'):
                    text = self._extract_pdf_text(raw)[:12000]
                else:
                    text = raw.decode('utf-8', errors='replace')[:12000]
                if text:
                    lines.append(f'\nDiscovery Transcript:\n{text}')
                    return '\n'.join(lines)
            except Exception as exc:
                _logger.warning('PresaleIQ: could not read transcript file: %s', exc)

        # Chatter messages
        messages = self.message_ids.filtered(
            lambda m: m.message_type in ('comment', 'email') and m.body
        )[:5]
        if messages:
            lines.append('\nNotes / Emails:')
            for m in messages:
                body = re.sub(r'<[^>]+>', ' ', m.body or '').strip()
                if body:
                    lines.append(body[:3000])

        return '\n'.join(lines)
