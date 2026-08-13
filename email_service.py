################################################################################
#                                                                              #
#  ephemeralREST - Swiss Ephemeris REST API                                   #
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
#  ADDITIONAL NOTICE - Swiss Ephemeris dependency:                             #
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
    'portal_url':  ('PORTAL_URL',    ''),
}


def _load_config() -> dict:
    """
    Load SMTP config, preferring database over environment.
    Config is loaded fresh on each EmailService instantiation so admin
    changes take effect immediately without restarting the server.
    """
    db_cfg = {}
    try:
        from database import create_database_manager
        db = create_database_manager()
        db_cfg = db.get_smtp_config()
    except Exception as e:
        logger.debug(f"SMTP config DB read failed - using env vars: {e}")

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
        # portal_url resolution order:
        #   1. smtp_config table (set via admin SMTP page)
        #   2. portal_settings table (set via admin Portal Settings page)
        #   3. PORTAL_URL env var
        #   4. base_url fallback (wrong for portal links — logs a warning)
        _portal_url = cfg.get('portal_url', '').strip()
        if not _portal_url:
            try:
                from database import create_database_manager
                _ps = create_database_manager().get_portal_settings()
                _portal_url = _ps.get('portal_url', '').strip()
            except Exception:
                pass
        if not _portal_url:
            _portal_url = os.environ.get('PORTAL_URL', '').strip()
        if _portal_url:
            self.portal_url = _portal_url.rstrip('/')
        else:
            self.portal_url = self.base_url
            logger.warning(
                "portal_url is not configured — email links will use the API URL (%s) "
                "instead of the portal URL. Set Portal URL in Settings → Portal Settings.",
                self.base_url
            )
        self.enabled     = bool(self.host and self.user and self.password)

        if not self.enabled:
            logger.warning(
                "Email service not configured. Set SMTP settings in the admin "
                "portal or via SMTP_* environment variables."
            )

    # -------------------------------------------------------------------------
    # Public methods
    # -------------------------------------------------------------------------

    def send_registration_verification(
            self, to_email: str, name: str, token: str,
            base_url: str = None, template: dict = None
    ) -> bool:
        """Send email verification link to a newly registered user."""
        # Link goes to the portal's verify.php, which calls the API internally.
        verify_url = f"{self.portal_url}/verify.php?t={token}"
        vars = {'name': name, 'verify_url': verify_url}

        if template:
            subject   = template.get('subject') or 'Verify your email address'
            text_body = self._substitute(template.get('body_text') or '', vars)
            html_body = self._render_template_html(template, vars)
        else:
            subject   = 'Verify your email address — ephemeralREST'
            text_body = f"""Hello {name},

Thank you for registering with ephemeralREST.

Click the link below to verify your email address:

    {verify_url}

This link expires in 24 hours.

If you did not request this, you can safely ignore this email.

ephemeralREST
"""
            html_body = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#333;">
<h2 style="color:#1F4E79;">Verify your email address</h2>
<p>Hello {name},</p>
<p>Thank you for registering. Click the button below to verify your email address:</p>
<p style="margin:28px 0;">
  <a href="{verify_url}" style="background:#2E75B6;color:#fff;padding:12px 28px;text-decoration:none;border-radius:4px;font-weight:bold;display:inline-block;">
    Verify Email Address
  </a>
</p>
<p style="color:#666;font-size:13px;">Or copy this link into your browser:<br><a href="{verify_url}">{verify_url}</a></p>
<p style="color:#666;font-size:13px;">This link expires in 24 hours.</p>
<hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
<p style="color:#999;font-size:12px;">If you did not request this, you can safely ignore this email.</p>
</body></html>"""
        return self._send(to_email, subject, text_body, html_body)

    def send_user_key_activated(
            self, to_email: str, name: str, api_key: str, template: dict = None
    ) -> bool:
        """Send the API key to a user after their email address is verified."""
        vars = {'name': name, 'identifier': to_email, 'api_key': api_key}

        if template:
            subject   = template.get('subject') or 'Your ephemeralREST API key is ready'
            text_body = self._substitute(template.get('body_text') or '', vars)
            html_body = self._render_template_html(template, vars)
        else:
            subject   = 'Your ephemeralREST API key is ready'
            text_body = f"""Hello {name},

Your email address has been verified and your ephemeralREST API key is now active.

Your API key:

    {api_key}

IMPORTANT: This key will not be shown again. Save it securely now.

Include it in every API request as the X-API-Key header:

    X-API-Key: {api_key}

ephemeralREST
"""
            html_body = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#333;">
<h2 style="color:#375623;">Your API key is ready</h2>
<p>Hello {name},</p>
<p>Your email address has been verified and your API key is now active.</p>
<p><strong>Your API key:</strong></p>
<p style="background:#f4f4f4;padding:16px;font-family:monospace;font-size:14px;border-radius:4px;word-break:break-all;">{api_key}</p>
<p style="color:#c00;font-weight:bold;">Save this key — it will not be shown again.</p>
<p style="background:#f4f4f4;padding:12px;font-family:monospace;font-size:13px;border-radius:4px;">X-API-Key: {api_key}</p>
<hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
<p style="color:#999;font-size:12px;">ephemeralREST</p>
</body></html>"""
        return self._send(to_email, subject, text_body, html_body)

    def send_key_rotated(
            self, to_email: str, name: str, identifier: str, api_key: str, template: dict = None
    ) -> bool:
        """Notify a key holder that their key has been rotated and deliver the new key."""
        vars = {'name': name, 'identifier': identifier, 'api_key': api_key}

        if template:
            subject   = template.get('subject') or 'Your API key has been rotated'
            text_body = self._substitute(template.get('body_text') or '', vars)
            html_body = self._render_template_html(template, vars)
        else:
            subject   = 'Your ephemeralREST API key has been rotated'
            text_body = f"""Hello {name},

Your API key for {identifier} has been rotated.

Your new API key:

    {api_key}

IMPORTANT: This key will not be shown again. Save it securely now.
Your previous key has been deactivated.

Include it in every API request as the X-API-Key header:

    X-API-Key: {api_key}

ephemeralREST
"""
            html_body = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#333;">
<h2 style="color:#375623;">API Key Rotated</h2>
<p>Hello {name},</p>
<p>Your API key for <strong>{identifier}</strong> has been rotated.</p>
<p><strong>Your new API key:</strong></p>
<p style="background:#f4f4f4;padding:16px;font-family:monospace;font-size:14px;border-radius:4px;word-break:break-all;">{api_key}</p>
<p style="color:#c00;font-weight:bold;">Save this key — it will not be shown again. Your previous key has been deactivated.</p>
<p style="background:#f4f4f4;padding:12px;font-family:monospace;font-size:13px;border-radius:4px;">X-API-Key: {api_key}</p>
<hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
<p style="color:#999;font-size:12px;">ephemeralREST</p>
</body></html>"""
        return self._send(to_email, subject, text_body, html_body)

    def send_set_password(self, to_email: str, name: str, token: str, template: dict = None) -> bool:
        """Send a link prompting the user to set a password (post email-verification)."""
        set_password_url = f"{self.portal_url}/set-password.php?t={token}"
        vars = {'name': name, 'set_password_url': set_password_url}

        if template:
            subject   = template.get('subject') or 'Set your password'
            text_body = self._substitute(template.get('body_text') or '', vars)
            html_body = self._render_template_html(template, vars)
        else:
            subject   = 'Set your password — ephemeralREST'
            text_body = f"""Hello {name},

Your email has been verified. Click the link below to set a password
for your account:

    {set_password_url}

This link expires in 24 hours.

ephemeralREST
"""
            html_body = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#333;">
<h2 style="color:#1F4E79;">Set Your Password</h2>
<p>Hello {name},</p>
<p>Your email has been verified. Click below to set a password for your account:</p>
<p style="margin:28px 0;">
  <a href="{set_password_url}" style="background:#2E75B6;color:#fff;padding:12px 28px;text-decoration:none;border-radius:4px;font-weight:bold;display:inline-block;">
    Set Password
  </a>
</p>
<p style="color:#666;font-size:13px;">Or copy this link: <a href="{set_password_url}">{set_password_url}</a></p>
<p style="color:#666;font-size:13px;">This link expires in 24 hours.</p>
<hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
<p style="color:#999;font-size:12px;">ephemeralREST</p>
</body></html>"""
        return self._send(to_email, subject, text_body, html_body)

    def send_password_reset_required(self, to_email: str, name: str, token: str, template: dict = None) -> bool:
        """Notify a user that an admin has required them to set a new password."""
        set_password_url = f"{self.portal_url}/set-password.php?t={token}"
        vars = {'name': name, 'set_password_url': set_password_url}

        if template:
            subject   = template.get('subject') or 'Password reset required'
            text_body = self._substitute(template.get('body_text') or '', vars)
            html_body = self._render_template_html(template, vars)
        else:
            subject   = 'Please reset your password — ephemeralREST'
            text_body = f"""Hello {name},

An administrator has requested that you set a new password for your
account.

Click the link below to set a new password:

    {set_password_url}

This link expires in 24 hours.

ephemeralREST
"""
            html_body = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#333;">
<h2 style="color:#833C00;">Password Reset Required</h2>
<p>Hello {name},</p>
<p>An administrator has requested that you set a new password for your account.</p>
<p style="margin:28px 0;">
  <a href="{set_password_url}" style="background:#2E75B6;color:#fff;padding:12px 28px;text-decoration:none;border-radius:4px;font-weight:bold;display:inline-block;">
    Set New Password
  </a>
</p>
<p style="color:#666;font-size:13px;">Or copy this link: <a href="{set_password_url}">{set_password_url}</a></p>
<p style="color:#666;font-size:13px;">This link expires in 24 hours.</p>
<hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
<p style="color:#999;font-size:12px;">ephemeralREST</p>
</body></html>"""
        return self._send(to_email, subject, text_body, html_body)

    def send_2fa_code(self, to_email: str, name: str, code: str, expiry_minutes: int = 10, template: dict = None) -> bool:
        """Send a 2FA verification code during login."""
        vars = {'name': name, 'code': code, 'expiry_minutes': str(expiry_minutes)}

        if template:
            subject   = template.get('subject') or 'Your login verification code'
            text_body = self._substitute(template.get('body_text') or '', vars)
            html_body = self._render_template_html(template, vars)
        else:
            subject   = 'Your login verification code — ephemeralREST'
            text_body = f"""Hello {name},

Your verification code is:

    {code}

This code expires in {expiry_minutes} minutes.

If you did not attempt to log in, you can ignore this email.

ephemeralREST
"""
            html_body = f"""<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#333;">
<h2 style="color:#1F4E79;">Verification Code</h2>
<p>Hello {name},</p>
<p>Your verification code is:</p>
<p style="background:#f4f4f4;padding:16px;font-family:monospace;font-size:28px;letter-spacing:4px;border-radius:4px;text-align:center;font-weight:bold;">{code}</p>
<p style="color:#666;font-size:13px;">This code expires in {expiry_minutes} minutes.</p>
<hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
<p style="color:#999;font-size:12px;">If you did not attempt to log in, you can ignore this email.</p>
</body></html>"""
        return self._send(to_email, subject, text_body, html_body)

    def send_test_email(self, to_email: str, template: dict = None) -> bool:
        """Send a test message to verify SMTP configuration."""
        if template:
            subject   = template.get('subject') or 'ephemeralREST - SMTP test'
            text_body = template.get('body_text') or 'This is a test email confirming SMTP is working.'
            html_body = self._render_template_html(template)
        else:
            subject   = 'ephemeralREST - SMTP configuration test'
            text_body = 'This is a test email from ephemeralREST confirming SMTP is working.'
            html_body = """<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:40px auto;color:#333;">
<h2 style="color:#375623;">ephemeralREST - SMTP Test </h2>
<p>Your SMTP configuration is working correctly.</p>
<hr style="border:none;border-top:1px solid #eee;margin:28px 0;">
<p style="color:#999;font-size:12px;">ephemeralREST</p>
</body></html>"""
        return self._send(to_email, subject, text_body, html_body)

    # -------------------------------------------------------------------------
    # Template helpers
    # -------------------------------------------------------------------------

    def _substitute(self, text: str, vars: dict) -> str:
        """Replace {key} placeholders in text with values from vars dict."""
        for k, v in vars.items():
            text = text.replace('{' + k + '}', str(v) if v is not None else '')
        return text

    def _render_template_html(self, template: dict, vars: dict = None) -> str:
        """Render a full styled HTML email from a template dict."""
        bg_color      = template.get('bg_color', '#f4f4f4')
        panel_color   = template.get('panel_color', '#ffffff')
        text_color    = template.get('text_color', '#1a1a1a')
        content_width = int(template.get('content_width', 600))
        header_align  = template.get('header_align', 'left')
        header_text   = template.get('header_text', 'ephemeralREST')
        body_text     = template.get('body_text', '')
        footer_text   = template.get('footer_text', 'ephemeralREST')

        if vars:
            header_text = self._substitute(header_text, vars)
            body_text   = self._substitute(body_text, vars)
            footer_text = self._substitute(footer_text, vars)

        # Convert plain line breaks to HTML paragraphs, auto-linking bare URLs
        import re as _re
        _url_re = _re.compile('(https?://[^ \t\n<>"\']+)')

        def _linkify(text: str) -> str:
            return _url_re.sub(
                lambda m: '<a href="' + m.group(1) + '" style="color:#2E75B6;word-break:break-all;">' + m.group(1) + '</a>',
                text
            )

        body_html = ''.join(
            f'<p style="margin:0 0 12px;">{_linkify(line)}</p>' if line.strip() else '<br>'
            for line in body_text.split('\n')
        )

        return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:{bg_color};font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:{bg_color};padding:32px 16px;">
    <tr><td align="center">
      <table width="{content_width}" cellpadding="0" cellspacing="0"
             style="max-width:{content_width}px;background:{panel_color};border-radius:6px;overflow:hidden;">
        <tr>
          <td style="padding:28px 32px 20px;border-bottom:1px solid #e0e0e0;text-align:{header_align};">
            <span style="font-size:18px;font-weight:600;color:{text_color};">{header_text}</span>
          </td>
        </tr>
        <tr>
          <td style="padding:28px 32px;color:{text_color};font-size:14px;line-height:1.6;">
            {body_html}
          </td>
        </tr>
        <tr>
          <td style="padding:16px 32px;background:{panel_color};color:#888;font-size:12px;border-top:1px solid #e0e0e0;">
            {footer_text}
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body></html>"""

    # -------------------------------------------------------------------------
    # Internal send
    # -------------------------------------------------------------------------

    def _send(self, to: str, subject: str, text_body: str, html_body: str) -> bool:
        if not self.enabled:
            logger.warning(
                f"SMTP not configured - skipping '{subject}' to {to}. "
                "Configure via the admin SMTP settings page."
            )
            return True  # non-fatal - caller continues normally

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