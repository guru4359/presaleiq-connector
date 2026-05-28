# -*- coding: utf-8 -*-
import json
import logging
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# How long (seconds) the background polling thread waits between status checks.
_POLL_INTERVAL = 30
# Maximum number of status checks before giving up.
_POLL_MAX_TRIES = 20


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    presaleiq_analysis_url = fields.Char(
        string='PresaleIQ Analysis URL',
        readonly=True,
        help='Link to the latest PresaleIQ analysis for this opportunity.',
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

    # ── helpers ──────────────────────────────────────────────────────────────

    def _presaleiq_config(self):
        """Return (url, api_key, platform) from system parameters.
        Raises UserError if URL or API key are not set.
        """
        ICP = self.env['ir.config_parameter'].sudo()
        url = (ICP.get_param('presaleiq.url') or '').strip().rstrip('/')
        api_key = (ICP.get_param('presaleiq.api_key') or '').strip()
        platform = (ICP.get_param('presaleiq.platform') or 'servicenow').strip()
        if not url or not api_key:
            raise UserError(_(
                'PresaleIQ is not configured. '
                'Go to Settings → Technical → PresaleIQ and enter your '
                'Instance URL and API Key.'
            ))
        return url, api_key, platform

    def _presaleiq_build_transcript(self):
        """Build a plain-text transcript from opportunity fields + chatter."""
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
        if self.description:
            lines.append(f'\nDescription:\n{self.description}')
        # Pull plain text from chatter notes (up to 5 most recent)
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

    @staticmethod
    def _presaleiq_http(url, api_key, payload_dict=None, method='POST', timeout=30):
        """Make an authenticated HTTP request to the PresaleIQ API.

        Returns the decoded JSON response dict.
        Raises UserError on HTTP errors or connection failures.
        """
        data = json.dumps(payload_dict).encode() if payload_dict is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                'Content-Type': 'application/json',
                'X-API-Key': api_key,
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:400]
            raise UserError(_(
                'PresaleIQ API error %(code)s: %(body)s',
                code=e.code, body=body,
            ))
        except Exception as e:
            raise UserError(_('Could not reach PresaleIQ: %(err)s', err=str(e)))

    # ── background polling ────────────────────────────────────────────────────

    def _presaleiq_poll_background(self, base_url, api_key, analysis_id, lead_id):
        """Poll /api/v1/analyze/<id>/status in a daemon thread.

        Updates the lead's story_count and status once the analysis completes.
        Runs entirely outside the Odoo request cycle — uses a fresh cursor.
        """
        poll_url = f'{base_url}/api/v1/analyze/{analysis_id}/status'
        req = urllib.request.Request(
            poll_url,
            headers={'X-API-Key': api_key},
            method='GET',
        )

        for attempt in range(_POLL_MAX_TRIES):
            threading.Event().wait(_POLL_INTERVAL)
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode())
                status = data.get('status', '')
                if status == 'complete':
                    summary = data.get('summary', {})
                    story_count = int(summary.get('story_count') or 0)
                    with self.pool.cursor() as cr:
                        cr.execute(
                            "UPDATE crm_lead "
                            "SET presaleiq_story_count=%s, presaleiq_status=%s "
                            "WHERE id=%s",
                            (story_count, 'complete', lead_id),
                        )
                    return
                elif status == 'error':
                    with self.pool.cursor() as cr:
                        cr.execute(
                            "UPDATE crm_lead SET presaleiq_status=%s WHERE id=%s",
                            ('error', lead_id),
                        )
                    return
            except Exception as exc:
                _logger.debug(
                    'PresaleIQ poll attempt %d failed for analysis %d: %s',
                    attempt + 1, analysis_id, exc,
                )

    # ── actions ──────────────────────────────────────────────────────────────

    def action_analyze_with_presaleiq(self):
        """Send this opportunity to PresaleIQ for AI analysis and open results."""
        self.ensure_one()
        base_url, api_key, platform = self._presaleiq_config()
        transcript = self._presaleiq_build_transcript()

        data = self._presaleiq_http(
            f'{base_url}/api/v1/analyze',
            api_key,
            payload_dict={
                'title':               self.name or 'Odoo Opportunity',
                'transcript':          transcript,
                'platform':            platform,
                'source':              'odoo_crm',
                'crm_slug':            'odoo',
                'crm_opportunity_id':  str(self.id),
                'crm_opportunity_name': self.name or '',
            },
        )

        analysis_id  = data.get('analysis_id') or data.get('id')
        results_url  = (
            data.get('results_url')
            or (f'{base_url}/results/{analysis_id}' if analysis_id else base_url)
        )
        poll_url     = data.get('poll_url')

        # Post a chatter note visible to the whole team
        self.message_post(
            body=_(
                '<p><strong>PresaleIQ analysis started</strong></p>'
                '<p>The opportunity has been sent to PresaleIQ for AI analysis '
                '(platform: %(platform)s).</p>'
                '<p><a href="%(url)s" target="_blank">View analysis &rarr;</a></p>',
                platform=platform, url=results_url,
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

        self.sudo().write({
            'presaleiq_analysis_url':  results_url,
            'presaleiq_analysis_id':   analysis_id or 0,
            'presaleiq_last_analyzed': fields.Datetime.now(),
            'presaleiq_status':        'pending',
        })

        # Start background polling so story_count updates automatically
        if analysis_id and poll_url:
            t = threading.Thread(
                target=self._presaleiq_poll_background,
                args=(base_url, api_key, analysis_id, self.id),
                daemon=True,
            )
            t.start()

        # Open the results page in a new tab
        return {
            'type':   'ir.actions.act_url',
            'url':    results_url,
            'target': 'new',
        }

    def action_open_presaleiq(self):
        """Open the latest PresaleIQ analysis in a new tab."""
        self.ensure_one()
        if not self.presaleiq_analysis_url:
            raise UserError(_(
                'No PresaleIQ analysis found for this opportunity. '
                'Click "Analyze with PresaleIQ" first.'
            ))
        return {
            'type':   'ir.actions.act_url',
            'url':    self.presaleiq_analysis_url,
            'target': 'new',
        }

    def action_refresh_presaleiq_status(self):
        """Manually refresh the analysis status from PresaleIQ."""
        self.ensure_one()
        if not self.presaleiq_analysis_id:
            raise UserError(_('No analysis ID found. Run "Analyze with PresaleIQ" first.'))

        base_url, api_key, _ = self._presaleiq_config()
        data = self._presaleiq_http(
            f'{base_url}/api/v1/analyze/{self.presaleiq_analysis_id}/status',
            api_key,
            payload_dict=None,
            method='GET',
        )

        status      = data.get('status', '')
        summary     = data.get('summary', {})
        story_count = int(summary.get('story_count') or 0)

        vals = {'presaleiq_status': status}
        if status == 'complete' and story_count:
            vals['presaleiq_story_count'] = story_count
        self.sudo().write(vals)

        return {
            'type':    'ir.actions.client',
            'tag':     'display_notification',
            'params':  {
                'title':   _('PresaleIQ Status'),
                'message': _(
                    'Status: %(status)s'
                    '%(stories)s',
                    status=status,
                    stories=(
                        f' — {story_count} user stories generated'
                        if story_count else ''
                    ),
                ),
                'type':    'success' if status == 'complete' else 'info',
                'sticky':  False,
            },
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
                    _logger.warning(
                        'PresaleIQ auto-push failed for lead %s: %s', rec.id, e
                    )
        return records
