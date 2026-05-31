# -*- coding: utf-8 -*-
import base64
import re
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from .presaleiq_mixin import PresaleIQMixin

_logger = logging.getLogger(__name__)


class PresaleIQCrmLead(models.Model):
    _inherit = 'crm.lead'   # STRING, not list — unique class name avoids MRO / field collision

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
        help='Platform being sold for this opportunity. '
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
        on crm.lead and silently attach documents when complete.
        Runs every 2 minutes via ir.cron — no user action required.
        """
        self._cron_auto_poll()

    # ── CRM-specific methods ──────────────────────────────────────────────

    def _presaleiq_build_transcript(self):
        """Build a plain-text transcript from opportunity fields + chatter.

        Priority order:
          1. Dedicated transcript upload (presaleiq_transcript_file field)
          2. Description field
          3. Chatter messages / emails
        """
        lines = []
        if self.name:
            lines.append(f'Opportunity: {self.name}')
        customer = self.partner_name or (self.partner_id and self.partner_id.name)
        if customer:
            lines.append(f'Customer: {customer}')
        if self.expected_revenue:
            lines.append(
                f'Expected Revenue: {self.expected_revenue} '
                f'{self.company_currency.name}'
            )

        # ── 1. Dedicated transcript file (highest priority) ───────────────────
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
                    return '\n'.join(lines)   # skip description + chatter
            except Exception as exc:
                _logger.warning('PresaleIQ: could not read transcript file: %s', exc)

        if self.description:
            clean_desc = re.sub(r'<[^>]+>', ' ', self.description or '').strip()
            if clean_desc:
                lines.append(f'\nDescription:\n{clean_desc}')

        # ── 2. Chatter attachments: .txt / .docx / .pdf ───────────────────────
        attachments = self.env['ir.attachment'].search([
            ('res_model', '=', self._name),
            ('res_id',    '=', self.id),
        ])
        for att in attachments:
            name  = (att.name or '').lower()
            if not any(name.endswith(ext) for ext in ('.txt', '.docx', '.pdf')):
                continue
            try:
                raw = base64.b64decode(att.datas or b'')
                if name.endswith('.txt'):
                    text = raw.decode('utf-8', errors='replace')[:8000]
                    lines.append(f'\nAttachment ({att.name}):\n{text}')
                elif name.endswith('.docx'):
                    text = self._extract_docx_text(raw)[:8000]
                    if text:
                        lines.append(f'\nAttachment ({att.name}):\n{text}')
                elif name.endswith('.pdf'):
                    text = self._extract_pdf_text(raw)[:8000]
                    if text:
                        lines.append(f'\nAttachment ({att.name}):\n{text}')
            except Exception as exc:
                _logger.warning('PresaleIQ: could not read attachment %s: %s', att.name, exc)

        messages = self.message_ids.filtered(
            lambda m: m.message_type in ('comment', 'email') and m.body
        )[:5]
        if messages:
            lines.append('\nDiscovery Notes / Emails:')
            for m in messages:
                body = re.sub(r'<[^>]+>', ' ', m.body or '').strip()
                if body:
                    lines.append(body[:3000])
        return '\n'.join(lines)

    def action_start_live_agent_presaleiq(self):
        """Open the Live Agent wizard to collect the meeting URL."""
        self.ensure_one()
        self._presaleiq_config()   # validate config early
        return {
            'type':      'ir.actions.act_window',
            'name':      _('Start PresaleIQ Live Agent'),
            'res_model': 'presaleiq.live.agent.wizard',
            'view_mode': 'form',
            'target':    'new',
            'context':   {'default_lead_id': self.id},
        }

    def action_open_live_agent_dashboard(self):
        """Open the Live Agent battle card dashboard in a new tab."""
        self.ensure_one()
        if not self.presaleiq_session_url:
            raise UserError(_('No Live Agent session found for this opportunity.'))
        return {'type': 'ir.actions.act_url', 'url': self.presaleiq_session_url, 'target': 'new'}

    def action_refresh_live_session(self):
        """Check live session status and auto-attach post-meeting notes when the call ends."""
        self.ensure_one()

        session_id = self.presaleiq_session_id
        # Fallback: extract session_id from URL for records written before the wizard fix
        if not session_id and self.presaleiq_session_url:
            try:
                session_id = int(self.presaleiq_session_url.rstrip('/').split('/')[-1])
                self.sudo().write({'presaleiq_session_id': session_id})
            except (ValueError, IndexError):
                pass

        if not session_id:
            raise UserError(_('No Live Agent session found. Click "Live Agent" first.'))

        base_url, api_key, _platform = self._presaleiq_config()
        data = self._presaleiq_http(
            f'{base_url}/api/v1/engage/{session_id}/status',
            api_key,
            payload_dict=None,
            method='GET',
        )

        session_status   = data.get('status', 'unknown')
        analysis_id      = data.get('analysis_id')
        analysis_status  = data.get('analysis_status', '')
        has_transcript   = data.get('has_transcript', True)
        thin_transcript  = data.get('thin_transcript', False)
        word_count       = data.get('word_count', 0)

        msg_type = 'info'
        msg_body = ''

        if thin_transcript:
            msg_body = _(
                'The bot only captured %(n)d words — not enough for meeting notes. '
                'Hold a longer discovery conversation, or paste the full transcript '
                'manually via New Analysis.',
                n=word_count,
            )
            msg_type = 'warning'
        elif session_status == 'ended' and analysis_id and analysis_status == 'complete':
            existing = self.env['ir.attachment'].search_count([
                ('res_model', '=', self._name),
                ('res_id',    '=', self.id),
                ('name',      'like', 'PresaleIQ_Meeting'),
            ])
            if not existing:
                self._presaleiq_attach_documents(
                    base_url, api_key, analysis_id, 'PresaleIQ_MeetingNotes',
                )
                msg_body = _('Meeting notes ready — PDF & Excel attached to this opportunity.')
            else:
                msg_body = _('Meeting notes already attached.')
            msg_type = 'success'
        elif session_status == 'ended' and analysis_id and analysis_status == 'pending':
            msg_body = _('Generating meeting notes — analysis started. '
                         'Click Refresh Meeting Notes again in 2–3 minutes.')
            msg_type = 'warning'
        elif session_status == 'ended' and not has_transcript:
            msg_body = _('Session ended but the bot did not capture a transcript. '
                         'The meeting may have ended before the bot could join, '
                         'or recording was not enabled.')
            msg_type = 'warning'
        elif session_status == 'ended':
            msg_body = _('Meeting ended. No post-meeting analysis available yet — try again in a moment.')
        elif session_status == 'active':
            msg_body = _('Session is still active. Refresh after the call ends to get meeting notes.')
        else:
            msg_body = _('Session status: %(s)s', s=session_status)

        is_sticky = (msg_type != 'success')
        action = {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   _('Live Agent Status'),
                'message': msg_body,
                'type':    msg_type,
                'sticky':  is_sticky,
            },
        }
        if not is_sticky:
            action['params']['next'] = {'type': 'ir.actions.client', 'tag': 'reload'}
        return action

    # ── auto-push on creation ────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        ICP = self.env['ir.config_parameter'].sudo()
        if ICP.get_param('presaleiq.auto_push') == 'True':
            for rec in records.filtered(lambda r: r.type == 'opportunity'):
                try:
                    rec.action_analyze_with_presaleiq()
                except Exception as e:
                    _logger.warning(
                        'PresaleIQ auto-push failed for lead %s: %s', rec.id, e
                    )
        return records
