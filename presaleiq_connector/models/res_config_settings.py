# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    presaleiq_url = fields.Char(
        string='PresaleIQ Instance URL',
        config_parameter='presaleiq.url',
        default='https://presaleiq.ai',
        help='Base URL of your PresaleIQ instance, e.g. https://presaleiq.ai',
    )
    presaleiq_api_key = fields.Char(
        string='API Key',
        config_parameter='presaleiq.api_key',
        help='Your PresaleIQ REST API key. Generate one in PresaleIQ → Settings → API Keys.',
    )
    presaleiq_platform = fields.Selection(
        string='ITSM Platform',
        config_parameter='presaleiq.platform',
        selection=[
            ('servicenow',   'ServiceNow'),
            ('salesforce',   'Salesforce'),
            ('atlassian',    'Atlassian (Jira)'),
            ('zendesk',      'Zendesk'),
            ('bmc_helix',    'BMC Helix'),
            ('ivanti',       'Ivanti'),
            ('manageengine', 'ManageEngine'),
        ],
        default='servicenow',
        help='The ITSM platform your PresaleIQ subscription targets. Used to tailor AI output.',
    )
    presaleiq_auto_push = fields.Boolean(
        string='Auto-push new Opportunities',
        config_parameter='presaleiq.auto_push',
        default=False,
        help='When enabled, every new Opportunity is automatically sent to PresaleIQ for analysis.',
    )
