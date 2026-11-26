"""Links in emails point at the web app when PUBLIC_URL is set."""


def _capture(monkeypatch):
    import email_service
    sent = {}
    monkeypatch.setattr(email_service.EmailService, '_send',
                        lambda self, to, subject, text, html: sent.update(text=text) or True)
    return email_service, sent


def test_links_use_public_url(app, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, 'PUBLIC_URL', 'https://astro.example.com')
    email_service, sent = _capture(monkeypatch)
    with app.app_context():
        email_service.EmailService().send_set_password('a@example.com', 'A', 'TOKEN')
    assert 'https://astro.example.com/set-password?t=TOKEN' in sent['text']


def test_older_deployments_keep_php_links(app, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, 'PUBLIC_URL', '')
    monkeypatch.setenv('PORTAL_URL', 'https://admin.example.com')
    email_service, sent = _capture(monkeypatch)
    with app.app_context():
        email_service.EmailService().send_registration_verification('a@example.com', 'A', 'TOKEN')
    assert 'https://admin.example.com/verify.php?t=TOKEN' in sent['text']
