"""Gmail REST transport. Never retry a send inside the HTTP layer."""
import base64
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen


SCOPES = ['https://www.googleapis.com/auth/gmail.send',
          'https://www.googleapis.com/auth/gmail.readonly']


class GmailError(RuntimeError):
    pass


class SendRejected(GmailError):
    """An explicit HTTP rejection; no accepted message is known."""


def request_json(url, data=None, headers=None):
    with urlopen(Request(url, data=data, headers=headers or {}), timeout=60) as response:
        return json.load(response)


class Gmail:
    def __init__(self, token):
        self.token = token

    @classmethod
    def from_file(cls, path):
        info = json.loads(Path(path).read_text('utf-8'))
        for key in ('client_id', 'client_secret', 'refresh_token'):
            if not isinstance(info.get(key), str) or not info[key]:
                raise ValueError(f'Gmail OAuth 文件缺少 {key}')
        try:
            token = request_json('https://oauth2.googleapis.com/token', urlencode({
                key: info[key] for key in ('client_id', 'client_secret', 'refresh_token')
            } | {'grant_type': 'refresh_token'}).encode(),
                {'Content-Type': 'application/x-www-form-urlencoded'})['access_token']
        except (HTTPError, URLError, TimeoutError, KeyError, ValueError) as exc:
            raise GmailError('Gmail OAuth 刷新失败，请检查授权文件和网络') from exc
        return cls(token)

    def call(self, path, body=None):
        try:
            return request_json('https://gmail.googleapis.com/gmail/v1/users/me/' + path,
                                json.dumps(body).encode() if body is not None else None,
                                {'Authorization': 'Bearer ' + self.token,
                                 'Content-Type': 'application/json'})
        except HTTPError as exc:
            # 408 and server errors can occur after the server accepted a POST.
            if body is not None and 400 <= exc.code < 500 and exc.code != 408:
                raise SendRejected(f'Gmail 拒绝发送：HTTP {exc.code}') from exc
            raise GmailError(f'Gmail 请求失败：HTTP {exc.code}') from exc
        except (OSError, ValueError) as exc:
            raise GmailError('Gmail 请求结果不明：网络或响应异常') from exc

    def send(self, raw):
        result = self.call('messages/send', {'raw': base64.urlsafe_b64encode(raw).decode()})
        if not isinstance(result.get('id'), str) or not result['id']:
            raise GmailError('Gmail 发送响应缺少 Message ID')
        return result['id']

    def find(self, message_id):
        result = self.call('messages?' + urlencode({
            'q': 'rfc822msgid:' + message_id.strip('<>'), 'labelIds': 'SENT', 'maxResults': 10}))
        return [item['id'] for item in result.get('messages', [])]

    def get(self, message_id):
        return self.call('messages/' + quote(message_id, safe='') + '?format=raw')


def authorize(client, token, port=8765):
    """Interactive setup only; the delivery runtime uses the standard library."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise ValueError('请先运行 pip install -e ".[email-setup]"') from exc
    path = Path(token)
    path.parent.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES)
    credentials = flow.run_local_server(port=port, open_browser=False,
                                        access_type='offline', prompt='consent', timeout_seconds=300)
    if not credentials.refresh_token:
        raise ValueError('未取得 refresh_token，请重新授权')
    # Refuse to overwrite an existing authorization without an explicit new path.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(credentials.to_json())
