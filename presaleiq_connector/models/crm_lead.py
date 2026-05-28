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

    # ââ Analysis fields âââââââââââââââââââââââââââââââââââââââââââââââââââ
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

    # ââ License Sizing fields âââââââââââââââââââââââââââââââââââââââââââââ
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

    # ââ Live Agent fields âââââââââââââââââââââââââââââââââââââââââââââââââ
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

    # ââ Push fields âââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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

    # ââ helpers ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

    def _presaleiq_config(self):
        """Return (url, api_key, platform) from system parameters."""
        ICP = self.env['ir.config_parameter'].sudo()
        url = (ICP.get_param('presaleiq.url') or '').strip().rstrip('/')
        api_key = (ICP.get_param('presaleiq.api_key') or '').strip()
        platform = (ICP.get_param('presaleiq.platform') or 'servicenow').strip()
        if not url or not api_key:
            raise UserError(_(
                'PresaleIQ is not configured. '
                'Go to Settings â Technical â PresaleIQ and enter your '
                'Instance URL and API Key.'
            ))
        return url, api_key, platform

    def _presaleiq_platform_label(self):
        """Return the human-readable platform name for the configured platform."""
        _labels = {
            'servicenow':   'ServiceNow',
            'salesforce':   'Salesforce',
            'atlassian':    'Atlassian (Jira)',
            'zendesk':      'Zendesk',
            'bmc_helix':    'BMC Helix',
            'ivanti':       'Ivanti',
            'manageengine': 'ManageEngine',
        }
        ICP = self.env['ir.config_parameter'].sudo()
        slug = (ICP.get_param('presaleiq.platform') or 'servicenow').strip()
        return _labels.get(slug, slug.title())

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
        """Make an authenticated HTTP request to the PresaleIQ API."""
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

    # ââ background polling ââââââââââââââââââââââââââââââââââââââââââââââââ

    def _presaleiq_poll_background(self, base_url, api_key, analysis_id, lead_id,
                                   field_prefix='presaleiq'):
        """Poll /api/v1/analyze/<id>/status in a daemon thread."""
        poll_url = f'{base_url}/api/v1/analyze/{analysis_id}/status'
        req = urllib.request.Request(
            poll_url,
            headers={'X-API-Key': api_key},
            method='GET',
        )
        status_field = f'{field_prefix}_status'
        count_field  = 'presaleiq_story_count' if field_prefix == 'presaleiq' else None

        for attempt in range(_POLL_MAX_TRIES):
            import time as _time
            _time.sleep(_POLL_INTERVAL)
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode())
                status = data.get('status', '')
                if status == 'complete':
                    summary = data.get('summary', {})
                    story_count = int(summary.get('story_count') or 0)
                    with self.pool.cursor() as cr:
                        if count_field and story_count:
                            cr.execute(
                                f"UPDATE crm_lead SET {status_field}=%s, "
                                f"{count_field}=%s WHERE id=%s",
                                ('complete', story_count, lead_id),
                            )
                        else:
                            cr.execute(
                                f"UPDATE crm_lead SET {status_field}=%s WHERE id=%s",
                                ('complete', lead_id),
                            )
                    return
                elif status == 'error':
                    with self.pool.cursor() as cr:
                        cr.execute(
                            f"UPDATE crm_lead SET {status_field}=%s WHERE id=%s",
                            ('error', lead_id),
                        )
                    return
            except Exception as exc:
                _logger.debug(
                    'PresaleIQ poll attempt %d failed for analysis %d: %s',
                    attempt + 1, analysis_id, exc,
                )

    # ââ actions ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

    def action_analyze_with_presaleiq(self):
        """Send this opportunity to PresaleIQ for AI analysis (SOW + User Stories)."""
        self.ensure_one()
        base_url, api_key, platform = self._presaleiq_config()
        transcript = self._presaleiq_build_transcript()

        data = self._presaleiq_http(
            f'{base_url}/api/v1/analyze',
            api_key,
            payload_dict={
                'title':                self.name or 'Odoo Opportunity',
                'transcript':           transcript,
                'platform':             platform,
                'source':               'odoo_crm',
                'crm_slug':             'odoo',
                'crm_opportunity_id':   str(self.id),
                'crm_opportunity_name': self.name or '',
            },
        )

        analysis_id = data.get('analysis_id') or data.get('id')
        results_url = (
            data.get('results_url')
            or (f'{base_url}/results/{analysis_id}' if analysis_id else base_url)
        )
        poll_url = data.get('poll_url')

        self.message_post(
            body=_(
                '<p><strong>PresaleIQ analysis started</strong></p>'
                '<p>Sent to PresaleIQ for AI analysis (platform: %(platform)s).</p>'
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

        if analysis_id and poll_url:
            t = threading.Thread(
                target=self._presaleiq_poll_background,
                args=(base_url, api_key, analysis_id, self.id, 'presaleiq'),
                daemon=True,
            )
            t.start()

        return {
            'type':   'ir.actions.act_url',
            'url':    results_url,
            'target': 'new',
        }

    def action_license_sizing_presaleiq(self):
        """Send this opportunity to PresaleIQ for License Sizing analysis."""
        self.ensure_one()
        base_url, api_key, platform = self._presaleiq_config()
        transcript = self._presaleiq_build_transcript()

        data = self._presaleiq_http(
            f'{base_url}/api/v1/analyze',
            api_key,
            payload_dict={
                'title':                f'License Sizing â {self.name or "Odoo Opportunity"}',
                'transcript':           transcript,
                'platform':             platform,
                'input_kind':           'license_sizing',
                'source':               'odoo_crm',
                'crm_slug':             'odoo',
                'crm_opportunity_id':   str(self.id),
                'crm_opportunity_name': self.name or '',
            },
        )

        analysis_id = data.get('analysis_id') or data.get('id')
        results_url = (
            data.get('results_url')
            or (f'{base_url}/results/{analysis_id}' if analysis_id else base_url)
        )
        poll_url = data.get('poll_url')

        self.message_post(
            body=_(
                '<p><strong>PresaleIQ License Sizing started</strong></p>'
                '<p>Generating license recommendations for %(platform)s.</p>'
                '<p><a href="%(url)s" target="_blank">View report &rarr;</a></p>',
                platform=self._presaleiq_platform_label(), url=results_url,
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

        self.sudo().write({
            'presaleiq_license_url':         results_url,
            'presaleiq_license_analysis_id': analysis_id or 0,
            'presaleiq_license_status':      'pending',
        })

        if analysis_id and poll_url:
            t = threading.Thread(
                target=self._presaleiq_poll_background,
                args=(base_url, api_key, analysis_id, self.id, 'presaleiq_license'),
                daemon=True,
            )
            t.start()

        return {
            'type':   'ir.actions.act_url',
            'url':    results_url,
            'target': 'new',
        }

    def action_start_live_agent_presaleiq(self):
        """Open the Live Agent wizard to collect the meeting URL."""
        self.ensure_one()
        self._presaleiq_config()   # validate config early
        wizard = self.env['presaleiq.live.agent.wizard'].create({
            'lead_id': self.id,
        })
        return {
            'type':      'ir.actions.act_window',
            'name':      _('Start PresaleIQ Live Agent'),
            'res_model': 'presaleiq.live.agent.wizard',
            'res_id':    wizard.id,
            'view_mode': 'form',
            'target':    'new',
        }

    def action_push_to_platform_presaleiq(self):
        """Push the completed analysis stories to the configured ITSM platform."""
        self.ensure_one()
        if not self.presaleiq_analysis_id:
            raise UserError(_('No analysis found. Run "Analyze with PresaleIQ" first.'))
        if self.presaleiq_status != 'complete':
            raise UserError(_(
                'Analysis is not complete yet (status: %(s)s). '
                'Wait for it to finish or click "Refresh Status".',
                s=self.presaleiq_status or 'unknown',
            ))

        base_url, api_key, _platform = self._presaleiq_config()
        platform_label = self._presaleiq_platform_label()

        data = self._presaleiq_http(
            f'{base_url}/api/v1/push/{self.presaleiq_analysis_id}',
            api_key,
            payload_dict={'mode': 'stories'},
        )

        push_status = data.get('status', 'unknown')
        created     = data.get('stories_created', 0)
        failed      = data.get('stories_failed', 0)
        errors      = data.get('errors') or []

        self.sudo().write({
            'presaleiq_push_status':        push_status,
            'presaleiq_push_stories_count': created,
        })

        self.message_post(
            body=_(
                '<p><strong>PresaleIQ â %(platform)s push %(status)s</strong></p>'
                '<p>%(created)s stories created, %(failed)s failed.</p>'
                '%(errors)s',
                platform=platform_label,
                status=push_status,
                created=created,
                failed=failed,
                errors=(
                    '<p style="color:red">' + '<br/>'.join(errors[:5]) + '</p>'
                    if errors else ''
                ),
            ),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

        return {
            'type':   'ir.actions.client',
            'tag':    'display_notification',
            'params': {
                'title':   _('Push to %(platform)s', platform=platform_label),
                'message': _(
                    '%(status)s â %(created)s stories pushed, %(failed)s failed.',
                    status=push_status.title(),
                    created=created,
                    failed=failed,
                ),
                'type':   'success' if push_status == 'completed' else 'warning',
                'sticky': failed > 0,
            },
        }

    def action_open_presaleiq(self):
        """Open the latest PresaleIQ analysis in a new tab."""
        self.ensure_one()
        if not self.presaleiq_analysis_url:
            raise UserError(_('No PresaleIQ analysis found. Click "Analyze with PresaleIQ" first.'))
        return {'type': 'ir.actions.act_url', 'url': self.presaleiq_analysis_url, 'target': 'new'}

    def action_open_live_agent_dashboard(self):
        """Open the Live Agent battle card dashboard in a new tab."""
        self.ensure_one()
        if not self.presaleiq_session_url:
            raise UserError(_('No Live Agent session found for this opportunity.'))
        return {'type': 'ir.actions.act_url', 'url': self.presaleiq_session_url, 'target': 'new'}

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
            'type':   'ir.actions.client',
            'tag':    'display_notification',
            'params': {
                'title':   _('PresaleIQ Status'),
                'message': _(
                    'Status: %(status)s%(stories)s',
                    status=status,
                    stories=(
                        f' â {story_count} user stories generated'
                        if story_count else ''
                    ),
                ),
                'type':   'success' if status == 'complete' else 'info',
                'sticky': False,
            },
        }

    # ââ auto-push on creation ââââââââââââââââââââââââââââââââââââââââââââ

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
