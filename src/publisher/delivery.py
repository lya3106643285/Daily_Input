"""Preview/send archived HTML: python -m publisher.delivery --help."""
import argparse
import base64
from contextlib import closing
from datetime import date, datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, getaddresses
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from .gmail import Gmail, GmailError, SendRejected, authorize


def digest(data):
    return hashlib.sha256(data).hexdigest()


def address(value):
    # Configuration accepts plain ASCII mailboxes, not display-name expressions.
    if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}", value):
        raise ValueError(f'无效邮箱地址：{value!r}（请只填写邮箱地址）')
    return value


def load_config(path):
    config = json.loads(Path(path).read_text('utf-8'))
    if not isinstance(config, dict):
        raise ValueError('邮件配置须为 JSON 对象')
    unknown = config.keys() - {'sender', 'to', 'cc', 'bcc', 'subject_prefix'}
    if unknown:
        raise ValueError(f'未知配置项：{sorted(unknown)}')
    config['sender'] = address(config.get('sender'))
    seen = set()
    for field in ('to', 'cc', 'bcc'):
        values = config.get(field, [])
        if not isinstance(values, list):
            raise ValueError(f'{field} 须为邮箱数组')
        config[field] = []
        for value in values:
            value = address(value)
            key = value.lower()
            if key not in seen:
                config[field].append(value)
                seen.add(key)
    if not seen:
        raise ValueError('收件人名单不能为空')
    prefix = config.setdefault('subject_prefix', '每日信息输入')
    if not isinstance(prefix, str) or not prefix.strip() or any(ord(c) < 32 for c in prefix):
        raise ValueError('subject_prefix 须为非空单行文字')
    return config


def prepare(path, day, version, config):
    day = date.fromisoformat(day).isoformat()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', version):
        raise ValueError('版本须为 1–64 位字母、数字、点、下划线或横线')
    path = Path(path)
    if path.suffix.lower() != '.html' or not 0 < path.stat().st_size <= 12 * 1024 * 1024:
        raise ValueError('附件须为非空 HTML，且不超过 12 MiB')
    data = path.read_bytes()
    text = data.decode('utf-8-sig').lower()
    if not all(tag in text for tag in ('<html', '</html>', '</body>')):
        raise ValueError('附件不是完整 HTML 成品')
    identity = {'date': day, 'version': version, 'sender': config['sender'].lower(),
                **{key: sorted(v.lower() for v in config[key]) for key in ('to', 'cc', 'bcc')}}
    key = digest(json.dumps(identity, sort_keys=True).encode())
    message = EmailMessage(policy=policy.SMTP)
    message['From'] = config['sender']
    for field in ('to', 'cc', 'bcc'):
        if config[field]:
            message[field] = ', '.join(config[field])
    message['Subject'] = f'{config["subject_prefix"]} · {day} · V{version}'
    message['Date'] = format_datetime(datetime.now(timezone.utc))
    message['Message-ID'] = f'<daily-input.{key}.1@{config["sender"].split("@")[1]}>'
    message.set_content(f'{day} 的《每日信息输入》已附上。\n请下载 HTML 附件后用浏览器打开，可离线阅读。\n')
    message.add_attachment(data, maintype='text', subtype='html',
                           filename=f'Daily_Input_{day}_V{version}.html')
    return key, digest(data), message.as_bytes()


def parse(raw):
    return BytesParser(policy=policy.default).parsebytes(raw)


def verify(remote, expected, gmail_id=None):
    if 'SENT' not in remote.get('labelIds', []):
        return False
    # Gmail can replace the RFC Message-ID. Its API id is the authoritative
    # identity when it was returned by messages.send and durably recorded.
    if gmail_id is not None and remote.get('id') != gmail_id:
        return False
    try:
        actual = parse(base64.urlsafe_b64decode(remote['raw'] + '==='))
        wanted = parse(expected)
        headers = ('Subject',) if gmail_id is not None else ('Message-ID', 'Subject')
        for header in headers:
            if str(actual.get(header, '')) != str(wanted.get(header, '')):
                return False
        for header in ('From', 'To', 'Cc', 'Bcc'):
            def mailboxes(message):
                return sorted(mailbox.lower() for _, mailbox in
                              getaddresses(message.get_all(header, [])))
            if mailboxes(actual) != mailboxes(wanted):
                return False
        def attachments(message):
            return [(part.get_filename(), part.get_content_type(), digest(part.get_payload(decode=True)))
                    for part in message.iter_attachments()]
        return attachments(actual) == attachments(wanted) and len(attachments(wanted)) == 1
    except (KeyError, ValueError, TypeError):
        return False


def deliver(key, sha, raw, database, gmail):
    """Serialize local sends and persist intent before making an external request.

    Re-run after SEND_UNKNOWN to reconcile, then make at most one explained resend.
    Accepted sends are only verified on re-run; they are never resent.
    """
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    with database.with_suffix(database.suffix + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with closing(sqlite3.connect(database)) as db:
            db.row_factory = sqlite3.Row
            db.executescript('''
                CREATE TABLE IF NOT EXISTS email_tasks (
                    task_key TEXT PRIMARY KEY, sha256 TEXT NOT NULL, raw BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS email_attempts (
                    task_key TEXT NOT NULL, attempt INTEGER NOT NULL, raw BLOB NOT NULL,
                    status TEXT NOT NULL, gmail_id TEXT, error TEXT,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(task_key, attempt));
            ''')
            task = db.execute('SELECT * FROM email_tasks WHERE task_key=?', (key,)).fetchone()
            if task and task['sha256'] != sha:
                raise ValueError('同日期、版本和收件人的附件已变更；请递增版本后发送')
            if not task:
                db.execute('INSERT INTO email_tasks VALUES (?, ?, ?)', (key, sha, raw))
                db.commit()
            else:
                raw = task['raw']
            attempts = db.execute('SELECT * FROM email_attempts WHERE task_key=? ORDER BY attempt',
                                  (key,)).fetchall()

            def update(number, status, gmail_id=None, error=None):
                db.execute('''UPDATE email_attempts SET status=?, gmail_id=?, error=?,
                              updated_at=CURRENT_TIMESTAMP WHERE task_key=? AND attempt=?''',
                           (status, gmail_id, error, key, number))
                db.commit()

            # Search every prior attempt before considering another POST.
            for attempt in attempts:
                if attempt['status'] == 'SENT_VERIFIED':
                    return {'status': 'SENT_VERIFIED', 'skipped': True, 'gmail_id': attempt['gmail_id']}
                if attempt['status'] == 'SEND_REJECTED':
                    continue
                try:
                    ids = ([attempt['gmail_id']] if attempt['gmail_id'] else
                           gmail.find(str(parse(attempt['raw'])['Message-ID'])))
                    for message_id in ids:
                        if verify(gmail.get(message_id), attempt['raw'], attempt['gmail_id']):
                            update(attempt['attempt'], 'SENT_VERIFIED', message_id)
                            return {'status': 'SENT_VERIFIED', 'skipped': True, 'gmail_id': message_id}
                except GmailError:
                    # Failed reconciliation must not trigger a resend.
                    return {'status': attempt['status'], 'skipped': True,
                            'detail': '核验失败，请检查 OAuth 只读权限与网络后重跑'}
            if any(a['status'] == 'SEND_ACCEPTED' for a in attempts):
                return {'status': 'SEND_ACCEPTED', 'skipped': True, 'detail': '已接受，待核验；不会补发'}
            if len(attempts) >= 2:
                return {'status': attempts[-1]['status'], 'skipped': True, 'detail': '已达两次尝试上限'}
            number = len(attempts) + 1
            if number == 2:
                message = parse(raw)
                message.replace_header('Message-ID', str(message['Message-ID']).replace('.1@', '.2@'))
                reason = attempts[-1]['error'] or '首次请求中断，发送结果不明'
                message.get_payload(0).set_content(
                    f'本邮件为补发。上次故障：{reason}。\n'
                    '前一封附件可能不完整，请核对两封邮件的附件；本次使用同一份归档文件。\n\n'
                    + message.get_payload(0).get_content())
                raw = message.as_bytes(policy=policy.SMTP)
            db.execute('INSERT INTO email_attempts(task_key, attempt, raw, status) VALUES (?, ?, ?, ?)',
                       (key, number, raw, 'SEND_UNKNOWN'))
            db.commit()
            try:
                message_id = gmail.send(raw)
            except SendRejected as exc:
                update(number, 'SEND_REJECTED', error=str(exc))
                return {'status': 'SEND_REJECTED', 'detail': str(exc)}
            except GmailError as exc:
                update(number, 'SEND_UNKNOWN', error=str(exc))
                return {'status': 'SEND_UNKNOWN', 'detail': '发送结果不明；重跑时先核验，最多补发一次'}
            update(number, 'SEND_ACCEPTED', message_id)
            try:
                if verify(gmail.get(message_id), raw, message_id):
                    update(number, 'SENT_VERIFIED', message_id)
                    return {'status': 'SENT_VERIFIED', 'gmail_id': message_id}
            except GmailError:
                pass
            return {'status': 'SEND_ACCEPTED', 'gmail_id': message_id, 'detail': 'Gmail 已接受，待核验'}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('html', type=Path, nargs='?')
    cli.add_argument('--date')
    cli.add_argument('--version')
    cli.add_argument('--config', type=Path, default=Path('config/email.json'))
    cli.add_argument('--database', type=Path, default=Path('var/email.sqlite3'))
    cli.add_argument('--token', type=Path, default=Path(os.environ.get('GMAIL_TOKEN_FILE', 'secrets/gmail-token.json')))
    cli.add_argument('--preview', type=Path, default=Path('outputs/email/preview.eml'))
    cli.add_argument('--send', action='store_true', help='实际发送；省略时仅输出 EML 预览')
    cli.add_argument('--authorize', type=Path, metavar='CLIENT_JSON', help='授权 Gmail 并保存 token，不发送邮件')
    args = cli.parse_args()
    try:
        if args.authorize:
            if args.send:
                cli.error('--authorize 不能和 --send 同时使用')
            authorize(args.authorize, args.token)
            print('Gmail OAuth 授权已保存')
            return
        if not all((args.html, args.date, args.version)):
            cli.error('请提供 HTML 路径、--date 和 --version')
        config = load_config(args.config)
        key, sha, raw = prepare(args.html, args.date, args.version, config)
        if not args.send:
            args.preview.parent.mkdir(parents=True, exist_ok=True)
            args.preview.write_bytes(raw)
            print(json.dumps({'status': 'PREVIEW', 'file': str(args.preview), 'sha256': sha}, ensure_ascii=False))
            return
        result = deliver(key, sha, raw, args.database, Gmail.from_file(args.token))
        print(json.dumps(result, ensure_ascii=False))
        if result['status'] != 'SENT_VERIFIED':
            raise SystemExit(1)
    except (OSError, ValueError, GmailError, sqlite3.Error) as exc:
        cli.exit(1, f'邮件处理失败：{exc}\n')


if __name__ == '__main__':
    main()
