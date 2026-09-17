from cfpwatch import notify


class _FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.logged_in = None
        self.sent = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, pw):
        self.logged_in = (user, pw)

    def send_message(self, msg):
        self.sent.append(msg)


def test_send_email_uses_gmail_ssl_and_html(monkeypatch):
    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", _FakeSMTP)
    notify.send_email("Subj", "<p>hi</p>", user="me@gmail.com", password="app-pw")
    s = _FakeSMTP.instances[-1]
    assert (s.host, s.port) == ("smtp.gmail.com", 465)
    assert s.logged_in == ("me@gmail.com", "app-pw")
    msg = s.sent[0]
    assert msg["Subject"] == "Subj" and msg["To"] == "me@gmail.com" and msg["From"] == "me@gmail.com"
    assert msg.get_content_type() == "multipart/alternative"
    assert "<p>hi</p>" in msg.get_payload()[1].get_payload(decode=True).decode()


def test_send_email_respects_explicit_recipient(monkeypatch):
    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", _FakeSMTP)
    notify.send_email("S", "<p>x</p>", user="me@gmail.com", password="pw", to="other@x.org")
    assert _FakeSMTP.instances[-1].sent[0]["To"] == "other@x.org"


def test_plain_text_part_decodes_entities(monkeypatch):
    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", _FakeSMTP)
    notify.send_email("S", "<p>Strategy &amp; Society &lt;2027&gt;</p>", user="me@gmail.com", password="pw")
    plain = _FakeSMTP.instances[-1].sent[0].get_payload()[0].get_payload(decode=True).decode()
    assert "Strategy & Society <2027>" in plain and "&amp;" not in plain
