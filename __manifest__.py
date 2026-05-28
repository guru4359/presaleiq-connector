# -*- coding: utf-8 -*-
{
    'name': 'PresaleIQ — AI Pre-Sales Agent',
    'version': '17.0.1.0.0',
    'category': 'Sales/CRM',
    'summary': 'Turn discovery calls into SOWs, User Stories & Effort Estimates — automatically.',
    'description': """
PresaleIQ Connector
===================

Connect your Odoo CRM to PresaleIQ (presaleiq.ai) — the AI pre-sales agent that
listens to discovery calls and generates:

* Statement of Work (SOW)
* User Stories & Acceptance Criteria
* Effort Estimates & LLD
* License Sizing recommendations

**How it works**

1. Open any CRM Opportunity.
2. Click **"Analyze with PresaleIQ"**.
3. PresaleIQ analyses the opportunity description and any attached transcript.
4. Results (SOW, stories, effort) are automatically posted as a chatter note
   on the opportunity.

**Features**

- One-click analysis from the Opportunity form
- Results posted directly to the opportunity chatter
- Configurable PresaleIQ instance URL and API key
- Works with Odoo Online, Odoo.sh, and on-premise
- Supports Odoo 16 and 17

**About PresaleIQ**

PresaleIQ (presaleiq.ai) is an AI pre-sales platform trusted by ServiceNow,
Salesforce, BMC Helix, and 12+ enterprise platform consultants worldwide.
Turn every discovery call into a signed contract faster.

Setup: install the module → Settings → PresaleIQ → enter your API key.
    """,
    'author': 'PresaleIQ.ai',
    'website': 'https://presaleiq.ai',
    'license': 'OPL-1',
    'price': 0.00,
    'currency': 'USD',
    'depends': ['crm', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/res_config_settings.xml',
        'views/res_config_settings_views.xml',
        'views/crm_lead_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
