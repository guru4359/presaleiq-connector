# -*- coding: utf-8 -*-
from markupsafe import Markup
from odoo import _, fields, models
from odoo.exceptions import UserError


class PresaleIQLiveAgentWizard(models.TransientModel):
    """Wizard: collect meeting URL then start a PresaleIQ Live Agent session."""
    _name = 'presaleiq.live.agent.wizard'
    _description = 'Start PresaleIQ Live Agent'

    lead_id = fields.Many2one('crm.lead', required=True, readonly=True)
    meeting_url = fields.Char(
        string='Meeting URL',
        required=True,
        help='Paste your Zoom, Teams, or Google Meet link here. '
             'The AI agent will join silently and listen to the call.',
    )
    agent_name = fields.Char(
        string='Agent Name',
        default='Sage',
        required=True,
        help='How the bot introduces itself in the meeting participant list.',
    )
    session_mode = fields.Selection(
        string='Session Mode',
        selection=[
            ('platform_sale', 'Platform Sale (battle cards + objection handling)'),
            ('ai_strategy',   'AI Strategy (roadmap + advisory mode)'),
        ],
        default='platform_sale',
        required=True,
    )

    def action_start(self):
        """POST /api/v1/engage and write results back to the lead."""
        self.ensure_one()
        lead = self.lead_id
        base_url, api_key, _platform = lead._presaleiq_config()

        data = lead._presaleiq_http(
            f'{base_url}/api/v1/engage',
            api_key,
            payload_dict={
                'meeting_url':          self.meeting_url.strip(),
                'title':                lead.name or 'Live Session',
                'agent_name':           self.agent_name or 'Sage',
                'session_mode':         self.session_mode,
                'crm_opportunity_id':   str(lead.id),
                'crm_opportunity_name': lead.name or '',
            },
        )

        session_id  = data.get('session_id')
        dashboard   = data.get('dashboard_url') or f'{base_url}/engage/{session_id}'
        status      = data.get('status', 'active')

        lead.sudo().write({
            'presaleiq_session_id':  session_id or 0,
            'presaleiq_session_url': dashboard,
        })

        lead.message_post(
            body=Markup(
                '<p><strong>PresaleIQ Live Agent started</strong></p>'
                '<p>Agent <em>{agent}</em> is joining the meeting.</p>'
                '<p><a href="{url}" target="_blank">Open battle card dashboard →</a></p>'
            ).format(agent=self.agent_name, url=dashboard),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

        if status == 'error':
            raise UserError(_(
                'The agent failed to join the meeting: %(err)s',
                err=data.get('error', 'unknown error'),
            ))

        # Close the wizard and show a success toast.
        # The "Live Agent Dashboard" button in the Opportunity header opens the
        # battle card view — no need to keep this dialog open.
        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   _('Live Agent Started'),
                'message': _(
                    '%(agent)s is joining the meeting. '
                    'Click "Live Agent Dashboard" above to open the battle card view.',
                    agent=self.agent_name,
                ),
                'type':   'success',
                'sticky': False,
                'next':   {'type': 'ir.actions.act_window_close'},
            },
        }
