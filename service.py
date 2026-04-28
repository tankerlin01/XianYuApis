import asyncio
import json
import threading
from datetime import datetime

import requests

from goofish_apis import qrcode_login
from goofish_live import XianyuLive
from storage import Storage, utcnow


def now_local():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


class CustomerService:
    def __init__(self, storage=None):
        self.storage = storage or Storage()
        self.storage.init_db()

    def get_account(self):
        return self.storage.fetchone("SELECT * FROM accounts WHERE platform = 'xianyu' LIMIT 1")

    def update_account_status(self, **fields):
        if not fields:
            return
        parts = []
        values = []
        for key, value in fields.items():
            parts.append(f"{key} = ?")
            values.append(value)
        parts.append("updated_at = ?")
        values.append(utcnow())
        values.append('xianyu')
        self.storage.execute(
            f"UPDATE accounts SET {', '.join(parts)} WHERE platform = ?",
            tuple(values),
        )

    def get_settings(self):
        rows = self.storage.fetchall("SELECT setting_key, setting_value FROM system_settings ORDER BY setting_key")
        return {row['setting_key']: row['setting_value'] for row in rows}

    def save_settings(self, payload):
        now = utcnow()
        for key, value in payload.items():
            self.storage.execute(
                """
                INSERT INTO system_settings (setting_key, setting_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value, updated_at = excluded.updated_at
                """,
                (key, value, now),
            )

    def list_conversations(self):
        return self.storage.fetchall(
            """
            SELECT *
            FROM conversations
            ORDER BY COALESCE(last_message_at, created_at) DESC
            """
        )

    def get_conversation(self, conversation_id):
        return self.storage.fetchone(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        )

    def get_messages(self, conversation_id):
        return self.storage.fetchall(
            """
            SELECT *
            FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (conversation_id,),
        )

    def upsert_conversation(self, conversation_id, buyer_id, buyer_name, last_message):
        existing = self.get_conversation(conversation_id)
        now = utcnow()
        if existing:
            unread = (existing['unread_count'] or 0) + 1
            self.storage.execute(
                """
                UPDATE conversations
                SET buyer_name = ?, last_message_at = ?, last_message_preview = ?, unread_count = ?, updated_at = ?
                WHERE conversation_id = ?
                """,
                (buyer_name, now, last_message[:200], unread, now, conversation_id),
            )
            return self.get_conversation(conversation_id)

        self.storage.execute(
            """
            INSERT INTO conversations
            (conversation_id, buyer_id, buyer_name, status, auto_reply_enabled, manual_takeover, last_message_at, last_message_preview, unread_count, created_at, updated_at)
            VALUES (?, ?, ?, 'auto', 1, 0, ?, ?, 1, ?, ?)
            """,
            (conversation_id, buyer_id, buyer_name, now, last_message[:200], now, now),
        )
        return self.get_conversation(conversation_id)

    def mark_conversation_read(self, conversation_id):
        self.storage.execute(
            "UPDATE conversations SET unread_count = 0, updated_at = ? WHERE conversation_id = ?",
            (utcnow(), conversation_id),
        )

    def set_conversation_mode(self, conversation_id, manual_takeover):
        status = 'manual' if manual_takeover else 'auto'
        self.storage.execute(
            """
            UPDATE conversations
            SET manual_takeover = ?, status = ?, updated_at = ?
            WHERE conversation_id = ?
            """,
            (1 if manual_takeover else 0, status, utcnow(), conversation_id),
        )

    def append_message(self, conversation_id, sender_type, sender_id, sender_name, content, message_type='text', raw_payload=None, reply_source=None):
        return self.storage.execute(
            """
            INSERT INTO messages
            (conversation_id, sender_type, sender_id, sender_name, message_type, content, raw_payload, reply_source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                sender_type,
                sender_id,
                sender_name,
                message_type,
                content,
                json.dumps(raw_payload, ensure_ascii=False) if raw_payload is not None else None,
                reply_source,
                utcnow(),
            ),
        )

    def list_rules(self):
        return self.storage.fetchall("SELECT * FROM reply_rules ORDER BY priority ASC, id ASC")

    def save_rule(self, form):
        now = utcnow()
        keywords = [part.strip() for part in form.get('keywords', '').split(',') if part.strip()]
        payload = (
            form.get('rule_name', '').strip() or '未命名规则',
            form.get('rule_type', 'keyword'),
            json.dumps(keywords, ensure_ascii=False),
            form.get('reply_text', '').strip(),
            int(form.get('priority', 100)),
            1 if form.get('enabled') else 0,
            now,
            now,
        )
        rule_id = form.get('id')
        if rule_id:
            self.storage.execute(
                """
                UPDATE reply_rules
                SET rule_name = ?, rule_type = ?, keywords = ?, reply_text = ?, priority = ?, enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                payload[:-2] + (now, rule_id),
            )
            return
        self.storage.execute(
            """
            INSERT INTO reply_rules
            (rule_name, rule_type, keywords, reply_text, priority, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )

    def delete_rule(self, rule_id):
        self.storage.execute("DELETE FROM reply_rules WHERE id = ?", (rule_id,))

    def list_faqs(self):
        return self.storage.fetchall("SELECT * FROM faq_docs ORDER BY id DESC")

    def save_faq(self, form):
        now = utcnow()
        payload = (
            form.get('title', '').strip() or '未命名 FAQ',
            form.get('content', '').strip(),
            form.get('doc_type', 'faq'),
            form.get('tags', '').strip(),
            form.get('match_keywords', '').strip(),
            1 if form.get('enabled') else 0,
            now,
            now,
        )
        faq_id = form.get('id')
        if faq_id:
            self.storage.execute(
                """
                UPDATE faq_docs
                SET title = ?, content = ?, doc_type = ?, tags = ?, match_keywords = ?, enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                payload[:-2] + (now, faq_id),
            )
            return
        self.storage.execute(
            """
            INSERT INTO faq_docs
            (title, content, doc_type, tags, match_keywords, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )

    def delete_faq(self, faq_id):
        self.storage.execute("DELETE FROM faq_docs WHERE id = ?", (faq_id,))

    def list_logs(self):
        return self.storage.fetchall(
            """
            SELECT *
            FROM reply_logs
            ORDER BY created_at DESC, id DESC
            LIMIT 200
            """
        )

    def log_reply(self, conversation_id, message_id, reply_message_id, reply_source, status='success', matched_rule_id=None, matched_doc_id=None, error_message=None):
        settings = self.get_settings()
        self.storage.execute(
            """
            INSERT INTO reply_logs
            (conversation_id, message_id, reply_message_id, reply_source, matched_rule_id, matched_doc_id, ai_model, status, error_message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                message_id,
                reply_message_id,
                reply_source,
                matched_rule_id,
                matched_doc_id,
                settings.get('ai_model', ''),
                status,
                error_message,
                utcnow(),
            ),
        )

    def _parse_keywords(self, raw_keywords):
        if not raw_keywords:
            return []
        try:
            return json.loads(raw_keywords)
        except json.JSONDecodeError:
            return [part.strip() for part in raw_keywords.split(',') if part.strip()]

    def _match_rule(self, send_message, rule_type):
        normalized = (send_message or '').strip().lower()
        for rule in self.list_rules():
            if not rule['enabled'] or rule['rule_type'] != rule_type:
                continue
            keywords = self._parse_keywords(rule['keywords'])
            if any(keyword.lower() in normalized for keyword in keywords):
                return rule
        return None

    def _match_faq(self, send_message):
        normalized = (send_message or '').strip().lower()
        for faq in self.list_faqs():
            if not faq['enabled']:
                continue
            keywords = [part.strip().lower() for part in (faq.get('match_keywords') or '').split(',') if part.strip()]
            if keywords and any(keyword in normalized for keyword in keywords):
                return faq
        return None

    def _call_ai(self, send_user_name, send_message, conversation_id):
        settings = self.get_settings()
        if settings.get('ai_reply_enabled') != '1':
            return None
        if not (settings.get('ai_api_url') and settings.get('ai_api_key') and settings.get('ai_model')):
            return None

        recent_messages = self.get_messages(conversation_id)[-6:]
        history_lines = []
        for item in recent_messages:
            role = '买家' if item['sender_type'] == 'buyer' else '客服'
            history_lines.append(f"{role}: {item['content']}")

        payload = {
            'model': settings['ai_model'],
            'messages': [
                {'role': 'system', 'content': settings.get('ai_system_prompt', '')},
                {'role': 'user', 'content': '\n'.join(history_lines + [f'买家昵称: {send_user_name}', f'当前问题: {send_message}'])},
            ],
            'temperature': 0.6,
        }
        response = requests.post(
            settings['ai_api_url'],
            headers={
                'Authorization': f"Bearer {settings['ai_api_key']}",
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=30,
            proxies={'http': None, 'https': None},
        )
        response.raise_for_status()
        data = response.json()
        return data['choices'][0]['message']['content'].strip()

    async def handle_incoming_message(self, context):
        conversation = self.upsert_conversation(
            context['cid'],
            context['send_user_id'],
            context['send_user_name'],
            context['send_message'],
        )
        message_id = self.append_message(
            context['cid'],
            'buyer',
            context['send_user_id'],
            context['send_user_name'],
            context['send_message'],
            raw_payload=context.get('raw_message'),
        )
        settings = self.get_settings()
        if settings.get('auto_reply_enabled') != '1':
            self.log_reply(context['cid'], message_id, None, 'skipped', status='skipped')
            return {}
        if conversation['manual_takeover']:
            self.log_reply(context['cid'], message_id, None, 'manual', status='skipped')
            return {}

        sensitive_rule = self._match_rule(context['send_message'], 'sensitive')
        if sensitive_rule:
            self.set_conversation_mode(context['cid'], True)
            return {
                'reply': sensitive_rule['reply_text'],
                'source': 'sensitive',
                'incoming_message_id': message_id,
                'matched_rule_id': sensitive_rule['id'],
            }

        keyword_rule = self._match_rule(context['send_message'], 'keyword')
        if keyword_rule:
            return {
                'reply': keyword_rule['reply_text'],
                'source': 'rule',
                'incoming_message_id': message_id,
                'matched_rule_id': keyword_rule['id'],
            }

        faq = self._match_faq(context['send_message'])
        if faq:
            return {
                'reply': faq['content'],
                'source': 'faq',
                'incoming_message_id': message_id,
                'matched_doc_id': faq['id'],
            }

        try:
            ai_reply = await asyncio.to_thread(
                self._call_ai,
                context['send_user_name'],
                context['send_message'],
                context['cid'],
            )
            if ai_reply:
                return {
                    'reply': ai_reply,
                    'source': 'ai',
                    'incoming_message_id': message_id,
                }
        except Exception as exc:
            self.log_reply(context['cid'], message_id, None, 'ai', status='failed', error_message=str(exc))

        return {
            'reply': settings.get('default_reply_text', '你好，消息已收到，我稍后回复你。'),
            'source': 'fallback',
            'incoming_message_id': message_id,
        }

    async def handle_reply_sent(self, payload):
        reply_message_id = self.append_message(
            payload['cid'],
            'system',
            '',
            '系统自动回复',
            payload['reply'],
            reply_source=payload.get('reply_source'),
        )
        self.log_reply(
            payload['cid'],
            payload.get('incoming_message_id'),
            reply_message_id,
            payload.get('reply_source', 'unknown'),
            matched_rule_id=payload.get('matched_rule_id'),
            matched_doc_id=payload.get('matched_doc_id'),
        )

    def save_manual_reply(self, conversation_id, content):
        return self.append_message(
            conversation_id,
            'human',
            '',
            '人工客服',
            content,
            reply_source='manual',
        )


class ChannelBridge:
    def __init__(self, service):
        self.service = service
        self.live = None
        self.thread = None
        self.status = {
            'state': 'idle',
            'message': '未连接',
            'account_name': '',
            'account_id': '',
            'last_error': '',
            'last_event_at': '',
        }

    def get_status(self):
        return dict(self.status)

    def _set_status(self, **fields):
        self.status.update(fields)
        self.status['last_event_at'] = now_local()

    def start(self):
        if self.thread and self.thread.is_alive():
            return False, '监听已在运行'
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return True, '已启动登录流程，请在服务终端扫码'

    def _run(self):
        self._set_status(state='awaiting_scan', message='等待扫码，请查看启动服务的终端')
        self.service.update_account_status(login_status='awaiting_scan', listen_status='offline')
        try:
            api = qrcode_login(show_qrcode=True)
            account_name = api.session.cookies.get('tracknick', '')
            account_id = api.session.cookies.get('unb', '')
            cookies_str = '; '.join(
                f'{c.name}={c.value}'
                for c in api.session.cookies
                if c.domain and '.goofish.com' in c.domain
            )
            self.live = XianyuLive(
                cookies_str,
                message_handler=self.service.handle_incoming_message,
                reply_sent_handler=self.service.handle_reply_sent,
            )
            self.live.xianyu.session = api.session
            self._set_status(state='connected', message='已连接并开始监听', account_name=account_name, account_id=account_id, last_error='')
            self.service.update_account_status(
                account_name=account_name,
                account_id=account_id,
                login_status='connected',
                listen_status='online',
                last_login_at=utcnow(),
                last_heartbeat_at=utcnow(),
            )
            asyncio.run(self.live.main())
        except Exception as exc:
            self._set_status(state='error', message='连接失败', last_error=str(exc))
            self.service.update_account_status(login_status='error', listen_status='offline')

    def send_manual_reply(self, conversation_id, buyer_id, content):
        if not self.live or not self.live.loop:
            raise RuntimeError('闲鱼监听未连接')
        future = asyncio.run_coroutine_threadsafe(
            self.live.send_text(conversation_id, buyer_id, content),
            self.live.loop,
        )
        future.result(timeout=20)
        reply_message_id = self.service.save_manual_reply(conversation_id, content)
        self.service.log_reply(conversation_id, None, reply_message_id, 'manual')

