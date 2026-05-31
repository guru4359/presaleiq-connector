# -*- coding: utf-8 -*-
"""
PresaleIQ shared method mixin — plain Python class, not an Odoo model.
Fields are declared in each concrete model (CrmLead, SaleOrder) separately.
All methods reference self (the Odoo model instance) as normal; self will
be the concrete Odoo model instance at call time.
"""
import base64
import io
import json
import logging
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

from markupsafe import Markup
from odoo import _, fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_POLL_INTERVAL = 30
_POLL_MAX_TRIES = 20


class PresaleIQMixin:
    """All shared PresaleIQ methods. Injected into CrmLead and SaleOrder via explicit assignment."""

    # ── helpers ──────────────────────────────────────────────────────────

    def _presaleiq_config(self):
        """Return (url, api_key, platform) from system parameters.

        Platform resolution order:
          1. presaleiq_platform field on THIS record (per-record override)
          2. presaleiq.platform system parameter (global default)
          3. Hard-coded fallback: 'servicenow'
        """
        ICP = self.env['ir.config_parameter'].sudo()
        url = (ICP.get_param('presaleiq.url') or '').strip().rstrip('/')
        api_key = (ICP.get_param('presaleiq.api_key') or '').strip()
        platform = (
            self.presaleiq_platform
            or (ICP.get_param('presaleiq.platform') or '').strip()
            or 'servicenow'
        )
        if not url or not api_key:
            raise UserError(_(
                'PresaleIQ is not configured. '
                'Go to Settings → Technical → PresaleIQ and enter your '
                'Instance URL and API Key.'
            ))
        return url, api_key, platform

    def _presaleiq_platform_label(self):
        """Return the human-readable platform name for this record."""
        _labels = {
            'servicenow':   'ServiceNow',
            'salesforce':   'Salesforce',
            'atlassian':    'Atlassian (Jira)',
            'zendesk':      'Zendesk',
            'bmc_helix':    'BMC Helix',
            'bmc_controlm': 'BMC Control-M',
            'ivanti':       'Ivanti',
            'manageengine': 'ManageEngine',
            'sailpoint':    'SailPoint',
            'cyberark':     'CyberArk',
            'saviynt':      'Saviynt',
            'microsoft':    'Microsoft (Entra / M365)',
        }
        _, _, slug = self._presaleiq_config()
        return _labels.get(slug, slug.title())

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

    # ── background polling ────────────────────────────────────────────────

    def _presaleiq_poll_background(self, base_url, api_key, analysis_id, lead_id,
                                   field_prefix='presaleiq'):
        """Poll /api/v1/analyze/<id>/status in a daemon thread."""
        db_name    = self.env.cr.dbname   # capture before thread runs
        model_name = self._name           # capture before thread runs
        table_name = model_name.replace('.', '_')
        poll_url   = f'{base_url}/api/v1/analyze/{analysis_id}/status'
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
                                f"UPDATE {table_name} SET {status_field}=%s, "
                                f"{count_field}=%s WHERE id=%s",
                                ('complete', story_count, lead_id),
                            )
                        else:
                            cr.execute(
                                f"UPDATE {table_name} SET {status_field}=%s WHERE id=%s",
                                ('complete', lead_id),
                            )
                    # Auto-attach documents to the record
                    doc_prefix = ('PresaleIQ_SOW' if field_prefix == 'presaleiq'
                                  else 'PresaleIQ_LicenseSizing')
                    self._presaleiq_attach_documents(
                        base_url, api_key, analysis_id, lead_id,
                        prefix=doc_prefix, db_name=db_name,
                        model_name=model_name)
                    return
                elif status == 'error':
                    with self.pool.cursor() as cr:
                        cr.execute(
                            f"UPDATE {table_name} SET {status_field}=%s WHERE id=%s",
                            ('error', lead_id),
                        )
                    return
            except Exception as exc:
                _logger.debug(
                    'PresaleIQ poll attempt %d failed for analysis %d: %s',
                    attempt + 1, analysis_id, exc,
                )

    def _presaleiq_attach_documents(self, base_url, api_key, analysis_id, lead_id,
                                    prefix='PresaleIQ_SOW', db_name=None,
                                    model_name=None):
        """Download PDF + XLSX from PresaleIQ and post as chatter message on the record."""
        model_name = model_name or self._name
        try:
            from odoo import SUPERUSER_ID
            from odoo.modules.registry import Registry as OdooRegistry

            formats = [
                ('pdf',  f'{prefix}.pdf',  'application/pdf'),
                ('xlsx', f'{prefix}.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
            ]
            _db = db_name or (self.env.cr.dbname if hasattr(self, 'env') else None)
            if not _db:
                _logger.warning('PresaleIQ: cannot attach documents — db_name unknown')
                return

            _logger.info('PresaleIQ: starting document attach for analysis %d on db %s', analysis_id, _db)

            attachment_ids = []
            registry = OdooRegistry(_db)

            for fmt, fname, mimetype in formats:
                try:
                    url = f'{base_url}/api/v1/analyze/{analysis_id}/download/{fmt}'
                    _logger.info('PresaleIQ: downloading %s from %s', fmt, url)
                    req = urllib.request.Request(
                        url, headers={'X-API-Key': api_key}, method='GET')
                    with urllib.request.urlopen(req, timeout=60) as resp:
                        file_bytes = resp.read()
                    _logger.info('PresaleIQ: downloaded %s (%d bytes)', fmt, len(file_bytes))
                    encoded = base64.b64encode(file_bytes).decode()

                    with registry.cursor() as cr:
                        from odoo import api as odoo_api
                        env = odoo_api.Environment(cr, SUPERUSER_ID, {})
                        # Remove previous version of same file
                        env['ir.attachment'].search([
                            ('res_model', '=', model_name),
                            ('res_id',    '=', lead_id),
                            ('name',      '=', fname),
                        ]).unlink()
                        att = env['ir.attachment'].create({
                            'name':      fname,
                            'res_model': model_name,
                            'res_id':    lead_id,
                            'type':      'binary',
                            'datas':     encoded,
                            'mimetype':  mimetype,
                        })
                        attachment_ids.append(att.id)
                        cr.commit()
                    _logger.info('PresaleIQ: attached %s to %s %s', fname, model_name, lead_id)
                except Exception as exc:
                    _logger.warning(
                        'PresaleIQ: could not attach %s for analysis %d: %s',
                        fmt, analysis_id, exc,
                    )

            # Post a chatter message with the attachments so they appear visibly
            # in the PresaleIQ conversation thread (not just buried in Internal Notes).
            if attachment_ids:
                try:
                    doc_type = 'License Sizing' if 'LicenseSizing' in prefix else 'SOW + User Stories'
                    results_url = f'{base_url}/results/{analysis_id}'
                    with registry.cursor() as cr:
                        from odoo import api as odoo_api
                        env = odoo_api.Environment(cr, SUPERUSER_ID, {})
                        record = env[model_name].browse(lead_id)
                        record.message_post(
                            body=Markup(
                                '<p><img src="/web/static/img/favicon.ico" '
                                'style="width:16px;height:16px;margin-right:6px;vertical-align:middle;"/>'
                                '<strong>PresaleIQ — {doc_type} ready</strong></p>'
                                '<p>Analysis #{analysis_id} is complete — '
                                '<strong>{doc_type}</strong> documents attached below (PDF + Excel).</p>'
                                '<p><a href="{url}" target="_blank">'
                                '📊 View full results on presaleiq.ai →</a></p>'
                            ).format(
                                doc_type=doc_type,
                                analysis_id=analysis_id,
                                url=results_url,
                            ),
                            attachment_ids=attachment_ids,
                            message_type='comment',
                            subtype_xmlid='mail.mt_comment',
                        )
                        cr.commit()
                    _logger.info(
                        'PresaleIQ: posted chatter message with %d attachments for analysis %d',
                        len(attachment_ids), analysis_id,
                    )
                except Exception as exc:
                    _logger.warning('PresaleIQ: could not post chatter message: %s', exc)

        except Exception as exc:
            _logger.warning('PresaleIQ: _presaleiq_attach_documents failed: %s', exc)

    @staticmethod
    def _extract_docx_text(raw: bytes) -> str:
        """Extract plain text from a .docx file (ZIP + XML, no external deps)."""
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                with z.open('word/document.xml') as f:
                    tree = ET.parse(f)
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            parts = [t.text for t in tree.findall('.//w:t', ns) if t.text]
            return ' '.join(parts)
        except Exception:
            return ''

    @staticmethod
    def _extract_pdf_text(raw: bytes) -> str:
        """Extract plain text from a PDF using only the stdlib (BT/ET stream parsing)."""
        try:
            text = raw.decode('latin-1', errors='replace')
            parts = []
            for block in re.findall(r'BT(.*?)ET', text, re.DOTALL):
                for tj in re.findall(r'\((.*?)\)\s*Tj', block):
                    parts.append(tj)
                for arr in re.findall(r'\[(.*?)\]\s*TJ', block):
                    for s in re.findall(r'\((.*?)\)', arr):
                        parts.append(s)
            return re.sub(r'\s+', ' ', ' '.join(parts)).strip()
        except Exception:
            return ''

    def _presaleiq_extract_docx_text(self, docx_bytes):
        """Extract plain text from a DOCX byte string using stdlib only (no python-docx)."""
        try:
            with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
                with zf.open('word/document.xml') as f:
                    tree = ET.parse(f)
                    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                    lines = []
                    for para in tree.findall('.//w:p', ns):
                        texts = [r.text or '' for r in para.findall('.//w:t', ns)]
                        line = ''.join(texts).strip()
                        if line:
                            lines.append(line)
                    return '\n'.join(lines)
        except Exception as e:
            _logger.warning('PresaleIQ: failed to extract DOCX text: %s', e)
            return ''

    def _presaleiq_find_questionnaire_attachment(self):
        """Return the most recently uploaded non-PresaleIQ DOCX attachment, or None.

        The blank questionnaire generated by PresaleIQ is named
        PresaleIQ_LicenseSizing_Questionnaire_*.docx — we skip it and look
        for any other DOCX the salesperson uploaded (customer-filled version).
        """
        Attachment = self.env['ir.attachment']
        candidates = Attachment.search([
            ('res_model', '=', self._name),
            ('res_id',    '=', self.id),
            ('mimetype',  '=',
             'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
        ], order='create_date desc')

        for att in candidates:
            # Skip the blank template we generated
            if att.name and att.name.startswith('PresaleIQ_LicenseSizing_Questionnaire_'):
                continue
            return att
        return None

    # ── actions ──────────────────────────────────────────────────────────

    def action_analyze_with_presaleiq(self):
        """Send this record to PresaleIQ for AI analysis (SOW + User Stories)."""
        self.ensure_one()
        base_url, api_key, platform = self._presaleiq_config()
        transcript = self._presaleiq_build_transcript()

        data = self._presaleiq_http(
            f'{base_url}/api/v1/analyze',
            api_key,
            payload_dict={
                'title':                self.name or 'Odoo Record',
                'transcript':           transcript,
                'platform':             platform,
                'source':               'odoo_crm',
                'crm_slug':             'odoo',
                'crm_opportunity_id':   str(self.id),
                'crm_opportunity_name': self.name or '',
                'notify_email':         self.env.user.email or '',
            },
        )

        analysis_id = data.get('analysis_id') or data.get('id')
        results_url = (
            data.get('results_url')
            or (f'{base_url}/results/{analysis_id}' if analysis_id else base_url)
        )
        poll_url = data.get('poll_url')

        self.message_post(
            body=Markup(
                '<p><strong>PresaleIQ analysis started</strong></p>'
                '<p>Sent to PresaleIQ for AI analysis (platform: {platform}).</p>'
                '<p><a href="{url}" target="_blank">View analysis →</a></p>'
            ).format(platform=platform, url=results_url),
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

    def action_get_questionnaire(self):
        """Download blank licence-sizing questionnaire DOCX and attach to this record."""
        self.ensure_one()
        base_url, api_key, platform = self._presaleiq_config()
        customer_name = urllib.parse.quote(self.partner_id.name or self.name or '')

        url = (
            f'{base_url}/api/v1/license-sizing/questionnaire'
            f'?platform={platform}&customer_name={customer_name}'
        )
        req = urllib.request.Request(
            url,
            headers={
                'Authorization': f'Bearer {api_key}',
                'X-Platform':    platform,
                'Accept': (
                    'application/vnd.openxmlformats-officedocument'
                    '.wordprocessingml.document'
                ),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                docx_bytes = resp.read()
        except urllib.error.HTTPError as e:
            body = ''
            try:
                body = e.read().decode('utf-8', errors='replace')[:500]
            except Exception:
                pass
            raise UserError(_(
                'Failed to download questionnaire: %(e)s — %(body)s',
                e=str(e), body=body,
            ))
        except Exception as e:
            raise UserError(_('Failed to download questionnaire: %(e)s', e=str(e)))

        platform_label = self._presaleiq_platform_label()
        filename = f'PresaleIQ_LicenseSizing_Questionnaire_{platform}.docx'

        # Remove any previous blank questionnaire to keep attachments tidy
        self.env['ir.attachment'].search([
            ('res_model', '=', self._name),
            ('res_id',    '=', self.id),
            ('name',      '=', filename),
        ]).unlink()

        att = self.env['ir.attachment'].create({
            'name':      filename,
            'res_model': self._name,
            'res_id':    self.id,
            'type':      'binary',
            'datas':     base64.b64encode(docx_bytes).decode(),
            'mimetype': (
                'application/vnd.openxmlformats-officedocument'
                '.wordprocessingml.document'
            ),
        })

        self.message_post(
            body=Markup(
                '<p><strong>Licence Sizing Questionnaire ready</strong></p>'
                '<p>Downloading <strong>{filename}</strong> now. '
                'Send it to your customer, ask them to fill it in and return it.</p>'
                '<p>Once you have the completed questionnaire, attach it here '
                'and click <strong>License Sizing</strong>.</p>'
            ).format(filename=filename),
            message_type='comment',
            subtype_xmlid='mail.mt_comment',
        )

        # Return a direct download action — this triggers the browser to
        # download the DOCX immediately via the Odoo web/content route,
        # using the user's existing authenticated session.  This is the
        # standard Odoo pattern used by all report/export actions and
        # avoids the access-token issues that arise when the user manually
        # clicks the file in the Attachments widget.
        return {
            'type':   'ir.actions.act_url',
            'url':    f'/web/content/{att.id}?download=true',
            'target': 'new',
        }

    def action_license_sizing_presaleiq(self):
        """Run License Sizing.

        1. If a customer-filled questionnaire DOCX is attached → extract its text
           and send directly to the analysis API.
        2. Otherwise → open the PresaleIQ web questionnaire form in the browser
           (pre-filled with record context).
        """
        self.ensure_one()
        base_url, api_key, platform = self._presaleiq_config()

        # ── Path 1: filled questionnaire attached ─────────────────────────
        filled_att = self._presaleiq_find_questionnaire_attachment()
        if filled_att:
            docx_bytes  = base64.b64decode(filled_att.datas or b'')
            transcript  = self._presaleiq_extract_docx_text(docx_bytes)

            if not transcript or len(transcript.split()) < 30:
                raise UserError(_(
                    'The attached document appears to be empty or too short. '
                    'Please make sure the customer has filled in the questionnaire '
                    'before running License Sizing.'
                ))

            data = self._presaleiq_http(
                f'{base_url}/api/v1/analyze',
                api_key,
                payload_dict={
                    'title':                f'License Sizing — {self.name or "Odoo Record"}',
                    'transcript':           transcript,
                    'platform':             platform,
                    'input_kind':           'license_sizing',
                    'source':               'odoo_crm',
                    'crm_slug':             'odoo',
                    'crm_opportunity_id':   str(self.id),
                    'crm_opportunity_name': self.name or '',
                    'notify_email':         self.env.user.email or '',
                },
            )

            analysis_id = data.get('analysis_id') or data.get('id')
            results_url = (
                data.get('results_url')
                or (f'{base_url}/results/{analysis_id}' if analysis_id else base_url)
            )
            poll_url = data.get('poll_url')

            self.message_post(
                body=Markup(
                    '<p><strong>PresaleIQ License Sizing started</strong></p>'
                    '<p>Analysing filled questionnaire (<em>{docname}</em>) '
                    'for {platform}.</p>'
                    '<p><a href="{url}" target="_blank">View report →</a></p>'
                ).format(
                    docname=filled_att.name,
                    platform=self._presaleiq_platform_label(),
                    url=results_url,
                ),
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )

            self.sudo().write({
                'presaleiq_license_url':         results_url,
                'presaleiq_license_analysis_id': analysis_id or 0,
                'presaleiq_license_status':      'pending',
            })

            if analysis_id and poll_url:
                threading.Thread(
                    target=self._presaleiq_poll_background,
                    args=(base_url, api_key, analysis_id, self.id, 'presaleiq_license'),
                    daemon=True,
                ).start()

            return {
                'type':   'ir.actions.act_url',
                'url':    results_url,
                'target': 'new',
            }

        # ── Path 2: no filled questionnaire → open web form ───────────────
        crm_opp_id   = urllib.parse.quote(str(self.id))
        crm_opp_name = urllib.parse.quote(self.name or '')
        web_url = (
            f'{base_url}/license-sizing/new'
            f'?source=odoo'
            f'&crm_opportunity_id={crm_opp_id}'
            f'&crm_opportunity_name={crm_opp_name}'
            f'&platform={platform}'
        )
        return {
            'type':   'ir.actions.act_url',
            'url':    web_url,
            'target': 'new',
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

        error_html = (
            Markup('<p style="color:red">{}</p>').format(
                Markup('<br/>').join(errors[:5])
            ) if errors else Markup('')
        )
        self.message_post(
            body=Markup(
                '<p><strong>PresaleIQ → {platform} push {status}</strong></p>'
                '<p>{created} stories created, {failed} failed.</p>'
                '{errors}'
            ).format(platform=platform_label, status=push_status,
                     created=created, failed=failed, errors=error_html),
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

        return {
            'type':   'ir.actions.client',
            'tag':    'display_notification',
            'params': {
                'title':   _('Push to %(platform)s', platform=platform_label),
                'message': _(
                    '%(status)s — %(created)s stories pushed, %(failed)s failed.',
                    status=push_status.title(),
                    created=created,
                    failed=failed,
                ),
                'type':   'success' if push_status == 'completed' else 'warning',
                'sticky': failed > 0,
            },
        }

    def action_refresh_license_sizing_status(self):
        """Manually refresh the licence sizing status from PresaleIQ."""
        self.ensure_one()
        if not self.presaleiq_license_analysis_id:
            raise UserError(_('No licence sizing analysis found. Click "Run License Sizing" first.'))

        base_url, api_key, _platform = self._presaleiq_config()
        data = self._presaleiq_http(
            f'{base_url}/api/v1/analyze/{self.presaleiq_license_analysis_id}/status',
            api_key,
            payload_dict=None,
            method='GET',
        )

        status = data.get('status', '')
        error  = data.get('error', '')

        self.sudo().write({'presaleiq_license_status': status})

        # If complete, ensure documents are attached
        doc_msg = ''
        if status == 'complete':
            existing = self.env['ir.attachment'].search_count([
                ('res_model', '=', self._name),
                ('res_id',    '=', self.id),
                ('name',      'like', 'PresaleIQ_LicenseSizing'),
            ])
            if not existing:
                db_name    = self.env.cr.dbname
                model_name = self._name
                import threading as _thr
                _thr.Thread(
                    target=self._presaleiq_attach_documents,
                    args=(base_url, api_key,
                          self.presaleiq_license_analysis_id, self.id,
                          'PresaleIQ_LicenseSizing', db_name),
                    kwargs={'model_name': model_name},
                    daemon=True,
                ).start()
                doc_msg = ' — downloading documents…'

        notif_type = 'success' if status == 'complete' else ('danger' if status == 'error' else 'info')
        msg = _('Status: %(status)s%(docs)s', status=status, docs=doc_msg)
        if status == 'error' and error:
            msg = _('Error: %(error)s', error=error)

        action = {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'title':   _('Licence Sizing Status'),
                'message': msg,
                'type':    notif_type,
                'sticky':  status == 'error',
            },
        }
        if status == 'complete':
            action['params']['next'] = {'type': 'ir.actions.client', 'tag': 'reload'}
        return action

    # ── Cron / background auto-poll ───────────────────────────────────────

    def _cron_auto_poll(self):
        """Shared cron logic — finds every pending PresaleIQ analysis or
        license-sizing job on the current model, polls the VPS, updates status
        fields, and silently attaches PDF/XLSX when complete.

        Call via @api.model cron_presaleiq_auto_poll() in each concrete model.
        """
        import threading as _thr

        def _get_config(rec):
            try:
                return rec._presaleiq_config()
            except Exception:
                return None, None, None

        def _refresh_documents(rec, base_url, api_key, analysis_id, doc_prefix):
            """Delete stale {prefix}.pdf / {prefix}.xlsx then re-download fresh ones.

            Uses exact name match (not 'like') so questionnaire files such as
            PresaleIQ_LicenseSizing_Questionnaire_*.docx are never touched.
            """
            for ext in ('.pdf', '.xlsx'):
                stale = rec.env['ir.attachment'].sudo().search([
                    ('res_model', '=', rec._name),
                    ('res_id',    '=', rec.id),
                    ('name',      '=', doc_prefix + ext),
                ])
                if stale:
                    stale.unlink()
            db    = rec.env.cr.dbname
            model = rec._name
            _thr.Thread(
                target=rec._presaleiq_attach_documents,
                args=(base_url, api_key, analysis_id, rec.id, doc_prefix, db),
                kwargs={'model_name': model},
                daemon=True,
            ).start()

        # ── 1. Main analysis (SOW / User Stories) ─────────────────────────
        pending_analysis = self.search([
            ('presaleiq_analysis_id', '>', 0),
            ('presaleiq_status', 'in', ['pending', 'processing']),
        ])
        for rec in pending_analysis:
            base_url, api_key, _ = _get_config(rec)
            if not api_key:
                continue
            try:
                data = rec._presaleiq_http(
                    f'{base_url}/api/v1/analyze/{rec.presaleiq_analysis_id}/status',
                    api_key, payload_dict=None, method='GET',
                )
                status      = data.get('status', '')
                summary     = data.get('summary', {})
                story_count = int(summary.get('story_count') or 0)
                if not status:
                    continue
                vals = {'presaleiq_status': status}
                if status == 'complete' and story_count:
                    vals['presaleiq_story_count'] = story_count
                rec.sudo().write(vals)
                if status == 'complete':
                    _refresh_documents(rec, base_url, api_key,
                                       rec.presaleiq_analysis_id, 'PresaleIQ_SOW')
            except Exception as exc:
                _logger.warning(
                    'PresaleIQ cron: error polling analysis #%s on %s#%s: %s',
                    rec.presaleiq_analysis_id, rec._name, rec.id, exc,
                )

        # ── 2. License sizing ─────────────────────────────────────────────
        pending_license = self.search([
            ('presaleiq_license_analysis_id', '>', 0),
            ('presaleiq_license_status', 'in', ['pending', 'processing']),
        ])
        for rec in pending_license:
            base_url, api_key, _ = _get_config(rec)
            if not api_key:
                continue
            try:
                data = rec._presaleiq_http(
                    f'{base_url}/api/v1/analyze/{rec.presaleiq_license_analysis_id}/status',
                    api_key, payload_dict=None, method='GET',
                )
                status = data.get('status', '')
                if not status:
                    continue
                rec.sudo().write({'presaleiq_license_status': status})
                if status == 'complete':
                    _refresh_documents(rec, base_url, api_key,
                                       rec.presaleiq_license_analysis_id,
                                       'PresaleIQ_LicenseSizing')
            except Exception as exc:
                _logger.warning(
                    'PresaleIQ cron: error polling license sizing #%s on %s#%s: %s',
                    rec.presaleiq_license_analysis_id, rec._name, rec.id, exc,
                )

    def action_open_presaleiq(self):
        """Open the latest PresaleIQ analysis in a new tab."""
        self.ensure_one()
        if not self.presaleiq_analysis_url:
            raise UserError(_('No PresaleIQ analysis found. Click "Analyze with PresaleIQ" first.'))
        return {'type': 'ir.actions.act_url', 'url': self.presaleiq_analysis_url, 'target': 'new'}

    def action_refresh_presaleiq_status(self):
        """Manually refresh the analysis status from PresaleIQ."""
        self.ensure_one()
        if not self.presaleiq_analysis_id:
            raise UserError(_('No analysis ID found. Run "Analyze with PresaleIQ" first.'))

        base_url, api_key, _platform = self._presaleiq_config()
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

        # If complete, ensure documents are attached (handles cases where the
        # background thread ran before the server-side download endpoint was
        # fixed, or where attachment failed for any reason).
        doc_msg = ''
        if status == 'complete':
            existing = self.env['ir.attachment'].search_count([
                ('res_model', '=', self._name),
                ('res_id',    '=', self.id),
                ('name',      'like', 'PresaleIQ_'),
            ])
            if not existing:
                db_name    = self.env.cr.dbname
                model_name = self._name
                import threading as _thr
                _thr.Thread(
                    target=self._presaleiq_attach_documents,
                    args=(base_url, api_key,
                          self.presaleiq_analysis_id, self.id,
                          'PresaleIQ_SOW', db_name),
                    kwargs={'model_name': model_name},
                    daemon=True,
                ).start()
                doc_msg = ' — downloading documents…'

        return {
            'type':   'ir.actions.client',
            'tag':    'display_notification',
            'params': {
                'title':   _('PresaleIQ Status'),
                'message': _(
                    'Status: %(status)s%(stories)s%(docs)s',
                    status=status,
                    stories=(
                        f' — {story_count} user stories generated'
                        if story_count else ''
                    ),
                    docs=doc_msg,
                ),
                'type':   'success' if status == 'complete' else 'info',
                'sticky': False,
                'next':   {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }
