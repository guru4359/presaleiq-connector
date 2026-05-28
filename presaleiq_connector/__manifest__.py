# -*- coding: utf-8 -*-
{
    'name': 'PresaleIQ — AI Pre-Sales Agent',
    'version': '17.0.1.3.0',
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
2. Click **"Analyze with PresaleIQ"** to generate a SOW + User Stories.
3. Click **"License Sizing"** to get platform license recommendations.
4. Click **"Live Agent"** to send an AI bot to your live discovery call.
5. Once analysis is complete, click **"Push to Platform"** to push stories
   directly into ServiceNow, Jira, or your configured ITSM platform.

**Features (v1.3)**

- One-click SOW + User Stories analysis from the Opportunity form
- License Sizing report with one click
- Live Agent: AI bot joins your Zoom/Teams/Meet call and provides real-time
  battle cards, objection handling, and competitive intelligence
- Push to Platform: push generated User Stories directly to ServiceNow,
  Jira, or any other connected ITSM platform — no copy/paste
- Results and status tracked on the Opportunity record
- Background polling — story count auto-updates when analysis completes
- Supports ServiceNow, Salesforce, Atlassian (Jira), Zendesk, BMC Helix,
  Ivanti, and ManageEngine
- Works with Odoo Online, Odoo.sh, and on-premise

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
        'views/presaleiq_wizard_views.xml',
        'views/crm_lead_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
