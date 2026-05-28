# -*- coding: utf-8 -*-
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    presaleiq_analysis_url = fields.Char(
        string='PresaleIQ Analysis URL',
        readonly=True,
        help='Link to the latest PresaleIQ analysis for this opportunity.',
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

    # ── helpers ──────────────────────────────────────────────────────────────

    def _presaleiq_config(self):
        """Return (url, api_key) from system parameters. Raises UserError if not set."""
        ICP = self.env['ir.config_parameter'].sudo()
        url = (ICP.get_param('presaleiq.url') or '').strip().rstrip('/')
        api_key = (ICP.get_param('presaleiq.api_key') or '').strip()
        if not url or not api_key:
            raise UserError(_(
                'PresaleIQ is not configured. '
                'Please go to Settings → PresaleIQ and enter your Instance URL and API Key.'
            ))
        return url, api_key

    def _presaleiq_build_transcript(self):
        """Build a plain-text transcript from the opportunity fields."""
        lines = []
        if self.name:
            lines.append(f'Opportunity: {self.name}')
        if self.partner_name or (self.partner_id and self.partner_id.name):
            lines.append(f'Customer: {self.partner_name or self.partner_id.name}')
        if self.expected_revenue:
            lines.append(f'Expected Revenue: {self.expected_revenue} {self.company_currency.name}')
        if self.description:
            lines.append(f'\nDescription:\n{self.description}')
        # Pull plain text from chatter notes (first 3000 chars of each)
        messages = self.message_ids.filtered(
            lambda m: m.message_type in ('comment', 'email') and m.body
        )[:5]
        if messages:
            lines.append('\nDiscovery Notes / Emails:')
            for m in messages:
                # Strip HTML tags simply
                import re
                body = re.sub(r'<[^>]+>', ' ', m.body or '').strip()
                if body:
                    lines.append(body[:3000])
        return '\n'.join(lines)

    # ── actions ──────────────────────────────────────────────────────────────

    def action_analyze_with_presaleiq(self):
        """Send this opportunity to PresaleIQ for AI analysis."""
        self.ensure_one()
        url, api_key = self._presaleiq_config()
        transcript = self._presaleiq_build_transcript()

        payload = json.dumps({
            'title': self.name or 'Odoo Opportunity',
            'transcript': transcript,
            'source': 'odoo_crm',
            'crm_opportunity_id': str(self.id),
            'crm_opportunity_name': self.name or '',
        }).encode()

        req = urllib.request.Request(
            f'{url}/api/v1/analyze',
            data=payload,
            headers={
                'Content-Type': 'application/json',
                'X-API-Key': api_key,
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            raise UserError(_(
                'PresaleIQ API error %(code)s: %(body)s',
                code=e.code, body=body,
            ))
        except Exception as e:
            raise UserError(_('Could not reach PresaleIQ: %(err)s', err=str(e)))

        analysis_id = data.get('analysis_id') or data.get('id')
        analysis_url = f'{url}/results/{analysis_id}' if analysis_id else url

        # Post a chatter message so the team can see it was sent
        self.message_post(
            body=_(
                '<p><strong>PresaleIQ analysis started</strong></p>'
                '<p>The opportunity has been sent to PresaleIQ for AI analysis. '
                '<a href="%(url)s" target="_blank">View analysis →</a></p>',
                url=analysis_url,
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

        self.sudo().write({
            'presaleiq_analysis_url': analysis_url,
            'presaleiq_last_analyzed': fields.Datetime.now(),
        })

        # Open the analysis in a new tab
        return {
            'type': 'ir.actions.act_url',
            'url': analysis_url,
            'target': 'new',
        }

    def action_open_presaleiq(self):
        """Open the latest PresaleIQ analysis in a new tab."""
        self.ensure_one()
        if not self.presaleiq_analysis_url:
            raise UserError(_('No PresaleIQ analysis found for this opportunity. Click "Analyze with PresaleIQ" first.'))
        return {
            'type': 'ir.actions.act_url',
            'url': self.presaleiq_analysis_url,
            'target': 'new',
        }

    # ── auto-push on creation ────────────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        ICP = self.env['ir.config_parameter'].sudo()
        if ICP.get_param('presaleiq.auto_push') == 'True':
            for rec in records.filtered(lambda r: r.type == 'opportunity'):
                try:
                    rec.action_analyze_with_presaleiq()
                except Exception as e:
                    _logger.warning('PresaleIQ auto-push failed for lead %s: %s', rec.id, e)
        return records
