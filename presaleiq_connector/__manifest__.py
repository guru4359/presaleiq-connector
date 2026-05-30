# -*- coding: utf-8 -*-
{
    'name': 'PresaleIQ — AI Pre-Sales Agent',
    'version': '17.0.1.22.0',
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

**Requirements**

This connector requires a free PresaleIQ account at https://presaleiq.ai.

Sign up takes 2 minutes — no credit card required for the trial. Once signed up,
generate your API key under Settings → API Keys and paste it into
Odoo → Settings → PresaleIQ → API Key.

All CRM users in your Odoo instance share the same PresaleIQ account quota,
so one account covers your entire team.

**How it works**

1. Sign up free at https://presaleiq.ai and copy your API key.
2. In Odoo: Settings → PresaleIQ → paste your API key → Save.
3. Open any CRM Opportunity and click **"Analyze with PresaleIQ"**.
4. SOW + User Stories (PDF + XLSX) auto-attach to the Opportunity when ready.
5. Click **"License Sizing"** to get platform license recommendations.
6. Click **"Live Agent"** to send an AI bot to your live discovery call.

**Features (v1.20)**

- One-click SOW + User Stories analysis from the Opportunity form
- SOW PDF + XLSX automatically attached to the Opportunity on completion
- License Sizing: Get Questionnaire → customer fills it in → Run Sizing (PDF + XLSX auto-attached)
- Results visible directly in the CRM chatter — PDF and Excel download links
  appear as a PresaleIQ notification in the Opportunity activity thread
- Live Agent: AI bot joins your Zoom/Teams/Meet call and provides real-time
  battle cards, objection handling, and competitive intelligence
- Results and status tracked on the Opportunity record
- Background polling — story count and documents auto-update when complete
- Refresh Status re-triggers document download if attachment was missed
- Supports 12 platforms: ServiceNow, Salesforce, Atlassian (Jira), Zendesk,
  BMC Helix, BMC Control-M, Ivanti, ManageEngine, SailPoint, CyberArk,
  Saviynt, and Microsoft (Entra / M365)
- Works with Odoo Online, Odoo.sh, and on-premise

**About PresaleIQ**

PresaleIQ (presaleiq.ai) is an AI pre-sales platform trusted by ServiceNow,
Salesforce, BMC Helix, and 12+ enterprise platform consultants worldwide.
Turn every discovery call into a signed contract faster.

Questions? Email support@presaleiq.ai
    """,
    'author': 'PresaleIQ.ai',
    'website': 'https://presaleiq.ai',
    'license': 'OPL-1',
    'price': 0.00,
    'currency': 'USD',
    'depends': ['crm', 'mail', 'sale'],
    'data': [
        'security/ir.model.access.csv',
        'data/res_config_settings.xml',
        'views/res_config_settings_views.xml',
        'views/presaleiq_wizard_views.xml',
        'views/crm_lead_views.xml',
        'views/sale_order_views.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': False,
    'auto_install': False,
}
