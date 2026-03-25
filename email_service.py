################################################################################
#                                                                              #
#  ephemeralREST — Swiss Ephemeris REST API                                   #
#  Copyright (C) 2026  ephemeralREST contributors                             #
#                                                                              #
#  This program is free software: you can redistribute it and/or modify       #
#  it under the terms of the GNU Affero General Public License as published   #
#  by the Free Software Foundation, either version 3 of the License, or       #
#  (at your option) any later version.                                         #
#                                                                              #
#  This program is distributed in the hope that it will be useful,            #
#  but WITHOUT ANY WARRANTY; without even the implied warranty of             #
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the              #
#  GNU Affero General Public License for more details.                         #
#                                                                              #
#  You should have received a copy of the GNU Affero General Public License   #
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.    #
#                                                                              #
#  ADDITIONAL NOTICE — Swiss Ephemeris dependency:                             #
#  This software uses the Swiss Ephemeris library developed by                #
#  Astrodienst AG, Zurich, Switzerland. The Swiss Ephemeris is licensed       #
#  under the GNU Affero General Public License (AGPL) v3. Use of this        #
#  software therefore requires compliance with the AGPL v3, which includes    #
#  the obligation to make source code available to users who interact with    #
#  this software over a network.                                              #
#  See https://www.astro.com/swisseph/ for full details.                      #
#                                                                              #
################################################################################
################################################################################
# email_service.py                                                            #
################################################################################

"""
Sends transactional email for ephemeralREST.

Configuration is loaded from the smtp_config database table (managed via
the admin SMTP settings page). Any key missing from the database falls back
to the corresponding environment variable so the server can start without
a database entry.

Database key  ←→  Environment variable
──────────────────────────────────────
host          ←→  SMTP_HOST
port          ←→  SMTP_PORT         (default 587)
user          ←→  SMTP_USER
password      ←→  SMTP_PASSWORD
from_addr     ←→  SMTP_FROM         (defaults to user if blank)
use_tls       ←→  SMTP_TLS          (default true)
use_ssl       ←→  SMTP_SSL          (default false)
admin_email   ←→  ADMIN_EMAIL
base_url      ←→  API_BASE_URL      (default http://localhost:5000)
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

# Mapping of db config keys to (env var name, default value)
_ENV_MAP = {
    'host':        ('SMTP_HOST',     ''),
    'port':        ('SMTP_PORT',     '587'),
    'user':        ('SMTP_USER',     ''),
    'password':    ('SMTP_PASSWORD', ''),
    'from_addr':   ('SMTP_FROM',     ''),
    'use_tls':     ('SMTP_TLS',      'true'),
    'use_ssl':     ('SMTP_SSL',      'false'),
    'admin_email': ('ADMIN_EMAIL',   ''),
    'base_url':    ('API_BASE_URL',  'http://localhost:5000'),
}


def _load_config() -> dict:
    """
    Load SMTP config, preferring database over environment.
    Config is loaded fresh on each EmailService instantiation so admin
    changes take effect immediately without restarting the server.
    """
    db_cfg = {}
    try:
        from database import DatabaseManager
        db = DatabaseManager(os.environ.get('DATABASE_PATH', 'ephemeral.db'))
        db_cfg = db.get_smtp_config()
    except Exception as e:
        logger.debug(f"SMTP config DB read failed — using env vars: {e}")

    cfg = {}
    for key, (env_var, default) in _ENV_MAP.items():
        db_val = db_cfg.get(key, '').strip()
        cfg[key] = db_val if db_val else os.environ.get(env_var, default)

    return cfg


class EmailService:
    """Sends transactional emails via SMTP."""

    def __init__(self):
        cfg = _load_config()

        self.host        = cfg['host']
        self.port        = int(cfg['port'] or 587)
        self.user        = cfg['user']
        self.password    = cfg['password']
        self.from_addr   = cfg['from_addr'].strip() or self.user
        self.use_tls     = cfg['use_tls'].lower()  not in ('false', '0', 'no')
        self.use_ssl     = cfg['use_ssl'].lower()  in ('true', '1', 'yes')
        self.admin_email = cfg['admin_email']
        self.base_url    = cfg['base_url'].rstrip('/')
        self.enabled     = bool(self.host and self.user and self.password)

        if not self.enabled:
            logger.warning(
                "Email service not configured. Set SMTP settings in the admin "
                "portal or via SMTP_* environment variables."
            )

    # -------------------------------------------------------------------------
    # Public methods
    # -------------------------------------------------------------------------

    def send_user_verification(self, to_email: str, name: str, token: str) -> bool:
        verify_url = f"{self.base_url}/register/verify?t={token}"
        subject    = "ephemeralREST — Verify your email to activate your API key"
        text = f"""Hello {name},

Thank you for registering with ephemeralREST.

To activate your API key click the link below:

    {verify_url}

This link expires in 24 hours. Once verified, your API key will be
shown once — please save it securely.

If you did not request this, ignore this email.

ephemeralREST
"""
        html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#333;">
<h2 style="color:#1F4E79;">ephemeralREST — Email Verification</h2>
<p>Hello {name},</p>
<p>Click below to activate your API key:</p>
<p style="margin:28px 0;">
  <a href="{verify_url}" style="background:#2E75B6;color:#fff;padding:12px 24px;text-decoration:none;border-radius:4px;font-weight:bold;">
    Verify Email &amp; Activate Key
  </a>
</p>
<p style="color:#666;font-size:13px;">Or paste: <a href="{verify_url}">{verify_url}</a></p>
<p style="color:#666;font-size:13px;">Link expires in 24 hours.</p>
<hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
<p style="color:#999;font-size:12px;">If you did not request this, ignore this email.</p>
</body></html>"""
        return self._send(to_email, subject, text, html)

    def send_domain_registration_received(
            self, to_email: str, name: str, domain: str
    ) -> bool:
        subject = "ephemeralREST — Registration request received"
        text = f"""Hello {name},

We have received your API key registration request for:

    {domain}

Your request is pending review. We will email your key once approved.

ephemeralREST
"""
        html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#333;">
<h2 style="color:#1F4E79;">ephemeralREST — Request Received</h2>
<p>Hello {name},</p>
<p>We've received your registration request for <strong>{domain}</strong>.</p>
<p>We'll email your API key once approved.</p>
<hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
<p style="color:#999;font-size:12px;">ephemeralREST</p>
</body></html>"""
        return self._send(to_email, subject, text, html)

    def send_domain_approved(
            self, to_email: str, name: str, domain: str,
            api_key: str, admin_note: str = None
    ) -> bool:
        subject   = "ephemeralREST — Your API key is ready"
        note_text = f"\nNote from admin: {admin_note}\n" if admin_note else ""
        note_html = f'<p style="color:#555;"><em>Note: {admin_note}</em></p>' if admin_note else ""
        text = f"""Hello {name},

Your API key registration for '{domain}' has been approved.

Your API key:

    {api_key}

IMPORTANT: This key will not be shown again. Save it securely now.
{note_text}
Use it in every API request:

    X-API-Key: {api_key}

ephemeralREST
"""
        html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#333;">
<h2 style="color:#375623;">ephemeralREST — API Key Approved ✓</h2>
<p>Hello {name},</p>
<p>Your registration for <strong>{domain}</strong> has been approved.</p>
{note_html}
<p><strong>Your API key:</strong></p>
<p style="background:#f4f4f4;padding:16px;font-family:monospace;font-size:14px;border-radius:4px;word-break:break-all;">{api_key}</p>
<p style="color:#c00;font-weight:bold;">⚠ Save this key — it will not be shown again.</p>
<p style="background:#f4f4f4;padding:12px;font-family:monospace;font-size:13px;border-radius:4px;">X-API-Key: {api_key}</p>
<hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
<p style="color:#999;font-size:12px;">ephemeralREST</p>
</body></html>"""
        return self._send(to_email, subject, text, html)

    def send_domain_rejected(
            self, to_email: str, name: str, domain: str, admin_note: str = None
    ) -> bool:
        subject   = "ephemeralREST — Registration not approved"
        note_text = f"\n{admin_note}\n" if admin_note else "\nContact us if you believe this is an error.\n"
        note_html = f'<p>{admin_note}</p>' if admin_note else '<p>Contact us if you believe this is an error.</p>'
        text = f"""Hello {name},

Your API key registration for '{domain}' could not be approved.
{note_text}
ephemeralREST
"""
        html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#333;">
<h2 style="color:#833C00;">ephemeralREST — Not Approved</h2>
<p>Hello {name},</p>
<p>Your registration for <strong>{domain}</strong> could not be approved.</p>
{note_html}
<hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
<p style="color:#999;font-size:12px;">ephemeralREST</p>
</body></html>"""
        return self._send(to_email, subject, text, html)

    def send_admin_new_registration(
            self, domain: str, name: str, contact_email: str,
            reason: str, request_id: int
    ) -> bool:
        if not self.admin_email:
            return True  # no admin email configured — silently skip

        approve_url = f"{self.base_url}/admin/registrations/{request_id}/approve"
        reject_url  = f"{self.base_url}/admin/registrations/{request_id}/reject"
        subject     = f"ephemeralREST — New registration: {domain}"
        text = f"""New registration request:

  Domain:  {domain}
  Name:    {name}
  Email:   {contact_email}
  Reason:  {reason or 'Not provided'}
  ID:      {request_id}
"""
        html = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#333;">
<h2 style="color:#1F4E79;">New Registration — {domain}</h2>
<table style="width:100%;border-collapse:collapse;">
  <tr><td style="padding:8px;color:#666;width:80px;">Domain</td><td style="padding:8px;"><strong>{domain}</strong></td></tr>
  <tr><td style="padding:8px;color:#666;">Name</td><td style="padding:8px;">{name}</td></tr>
  <tr><td style="padding:8px;color:#666;">Email</td><td style="padding:8px;">{contact_email}</td></tr>
  <tr><td style="padding:8px;color:#666;">Reason</td><td style="padding:8px;">{reason or '—'}</td></tr>
  <tr><td style="padding:8px;color:#666;">ID</td><td style="padding:8px;">#{request_id}</td></tr>
</table>
<p style="margin-top:20px;">
  <a href="{approve_url}" style="background:#375623;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;margin-right:10px;">Approve</a>
  <a href="{reject_url}"  style="background:#833C00;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;">Reject</a>
</p>
</body></html>"""
        return self._send(self.admin_email, subject, text, html)

    def send_test_email(self, to_email: str) -> bool:
        """Send a test message to verify SMTP configuration."""
        subject = "ephemeralREST — SMTP configuration test"
        text    = "This is a test email from ephemeralREST confirming SMTP is working."
        html    = """<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#333;">
<h2 style="color:#375623;">ephemeralREST — SMTP Test ✓</h2>
<p>Your SMTP configuration is working correctly.</p>
<hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
<p style="color:#999;font-size:12px;">ephemeralREST</p>
</body></html>"""
        return self._send(to_email, subject, text, html)

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _send(self, to: str, subject: str, text_body: str, html_body: str) -> bool:
        if not self.enabled:
            logger.warning(
                f"SMTP not configured — skipping '{subject}' to {to}. "
                "Configure via the admin SMTP settings page."
            )
            return True  # non-fatal — caller continues normally

        try:
            msg            = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From']    = self.from_addr
            msg['To']      = to
            msg.attach(MIMEText(text_body, 'plain'))
            msg.attach(MIMEText(html_body, 'html'))

            if self.use_ssl:
                with smtplib.SMTP_SSL(self.host, self.port) as smtp:
                    smtp.login(self.user, self.password)
                    smtp.sendmail(self.from_addr, to, msg.as_string())
            else:
                with smtplib.SMTP(self.host, self.port) as smtp:
                    if self.use_tls:
                        smtp.starttls()
                    smtp.login(self.user, self.password)
                    smtp.sendmail(self.from_addr, to, msg.as_string())

            logger.info(f"Email sent: '{subject}' → {to}")
            return True

        except Exception as e:
            logger.error(f"Email send failed to {to}: {e}")
            return False