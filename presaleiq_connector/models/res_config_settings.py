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
    presaleiq_share_install_info = fields.Boolean(
        string='Share install info with PresaleIQ',
        config_parameter='presaleiq.share_install_info',
        default=False,
        help='Optional. When enabled, your company name, admin email and Odoo URL '
             'are sent to your PresaleIQ instance on save so support can identify '
             'your account. Off by default — no install data is sent unless you opt in.',
    )

    def set_values(self):
        super().set_values()
        self._presaleiq_register_install()

    def _presaleiq_register_install(self):
        """Optionally notify your PresaleIQ instance that the connector is installed.

        Only runs when the admin has explicitly opted in via the
        "Share install info with PresaleIQ" setting (off by default). Sends
        company name, admin email and Odoo URL so support can identify the
        account. Silent on any failure — never blocks the settings save."""
        try:
            import json
            import ssl
            import urllib.request

            ICP = self.env['ir.config_parameter'].sudo()
            if ICP.get_param('presaleiq.share_install_info') != 'True':
                return  # opt-in only — no data leaves Odoo unless enabled
            api_key  = ICP.get_param('presaleiq.api_key') or ''
            base_url = (ICP.get_param('presaleiq.url') or 'https://presaleiq.ai').rstrip('/')
            if not api_key:
                return

            # Odoo version
            try:
                from odoo.release import version as odoo_version
            except Exception:
                odoo_version = '17.0'

            # Connector module version
            try:
                mod = self.env['ir.module.module'].sudo().search(
                    [('name', '=', 'presaleiq_connector')], limit=1
                )
                module_version = mod.installed_version if mod else '17.0.1.25.0'
            except Exception:
                module_version = '17.0.1.25.0'

            payload = json.dumps({
                'api_key':        api_key,
                'company_name':   self.env.company.name or '',
                'admin_email':    self.env.user.email or '',
                'odoo_url':       ICP.get_param('web.base.url') or '',
                'odoo_version':   str(odoo_version),
                'module_version': str(module_version),
            }).encode('utf-8')

            ctx = ssl.create_default_context()
            req = urllib.request.Request(
                f'{base_url}/api/v1/register-install',
                data=payload,
                headers={
                    'Content-Type': 'application/json',
                    'User-Agent':   'PresaleIQ-Odoo/1.25',
                },
                method='POST',
            )
            urllib.request.urlopen(req, timeout=5, context=ctx)
        except Exception:
            pass  # Never block the settings save
