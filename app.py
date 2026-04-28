from flask import Flask, flash, redirect, render_template, request, url_for

from service import ChannelBridge, CustomerService


app = Flask(__name__)
app.secret_key = 'xianyu-customer-service-dev'

service = CustomerService()
bridge = ChannelBridge(service)


@app.template_filter('from_json_list')
def from_json_list(value):
    if not value:
        return []
    import json

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return [part.strip() for part in value.split(',') if part.strip()]


@app.route('/')
def index():
    return redirect(url_for('conversations'))


@app.route('/connect')
def connect():
    return render_template(
        'connect.html',
        account=service.get_account(),
        channel_status=bridge.get_status(),
    )


@app.post('/connect/start')
def start_connect():
    ok, message = bridge.start()
    flash(message, 'success' if ok else 'warning')
    return redirect(url_for('connect'))


@app.route('/conversations')
def conversations():
    items = service.list_conversations()
    selected_id = request.args.get('conversation_id')
    selected = None
    messages = []
    if selected_id:
        selected = service.get_conversation(selected_id)
        if selected:
            service.mark_conversation_read(selected_id)
            selected = service.get_conversation(selected_id)
            messages = service.get_messages(selected_id)
    elif items:
        selected = items[0]
        service.mark_conversation_read(selected['conversation_id'])
        selected = service.get_conversation(selected['conversation_id'])
        messages = service.get_messages(selected['conversation_id'])

    return render_template(
        'conversations.html',
        conversations=items,
        selected=selected,
        messages=messages,
        account=service.get_account(),
        channel_status=bridge.get_status(),
    )


@app.post('/conversations/<conversation_id>/manual-reply')
def manual_reply(conversation_id):
    conversation = service.get_conversation(conversation_id)
    if not conversation:
        flash('会话不存在', 'error')
        return redirect(url_for('conversations'))

    content = request.form.get('content', '').strip()
    if not content:
        flash('回复内容不能为空', 'warning')
        return redirect(url_for('conversations', conversation_id=conversation_id))

    try:
        bridge.send_manual_reply(conversation_id, conversation['buyer_id'], content)
        flash('已发送人工回复', 'success')
    except Exception as exc:
        flash(f'发送失败: {exc}', 'error')
    return redirect(url_for('conversations', conversation_id=conversation_id))


@app.post('/conversations/<conversation_id>/mode')
def update_conversation_mode(conversation_id):
    manual_takeover = request.form.get('manual_takeover') == '1'
    service.set_conversation_mode(conversation_id, manual_takeover)
    flash('会话处理方式已更新', 'success')
    return redirect(url_for('conversations', conversation_id=conversation_id))


@app.route('/rules')
def rules():
    return render_template(
        'rules.html',
        rules=service.list_rules(),
        faqs=service.list_faqs(),
        settings=service.get_settings(),
    )


@app.post('/rules/save')
def save_rule():
    service.save_rule(request.form)
    flash('规则已保存', 'success')
    return redirect(url_for('rules'))


@app.post('/rules/<int:rule_id>/delete')
def delete_rule(rule_id):
    service.delete_rule(rule_id)
    flash('规则已删除', 'success')
    return redirect(url_for('rules'))


@app.post('/faqs/save')
def save_faq():
    service.save_faq(request.form)
    flash('FAQ 已保存', 'success')
    return redirect(url_for('rules'))


@app.post('/faqs/<int:faq_id>/delete')
def delete_faq(faq_id):
    service.delete_faq(faq_id)
    flash('FAQ 已删除', 'success')
    return redirect(url_for('rules'))


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        payload = {
            'auto_reply_enabled': '1' if request.form.get('auto_reply_enabled') else '0',
            'ai_reply_enabled': '1' if request.form.get('ai_reply_enabled') else '0',
            'default_reply_text': request.form.get('default_reply_text', '').strip(),
            'manual_fallback_text': request.form.get('manual_fallback_text', '').strip(),
            'ai_api_url': request.form.get('ai_api_url', '').strip(),
            'ai_api_key': request.form.get('ai_api_key', '').strip(),
            'ai_model': request.form.get('ai_model', '').strip(),
            'ai_system_prompt': request.form.get('ai_system_prompt', '').strip(),
        }
        service.save_settings(payload)
        flash('系统设置已保存', 'success')
        return redirect(url_for('settings'))

    return render_template(
        'settings.html',
        settings=service.get_settings(),
        account=service.get_account(),
        channel_status=bridge.get_status(),
    )


@app.route('/logs')
def logs():
    return render_template(
        'logs.html',
        logs=service.list_logs(),
        account=service.get_account(),
        channel_status=bridge.get_status(),
    )


if __name__ == '__main__':
    app.run(debug=False, use_reloader=False, port=5055)
