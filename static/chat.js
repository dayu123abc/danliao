let myId = null;
let muted = false;
let muteText = null;
let lastMessageId = 0;
let messagePollInFlight = false;

function showToast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2500);
}

function escHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function scrollToBottom() {
    const c = document.getElementById('chatMessages');
    c.scrollTop = c.scrollHeight;
}

function renderBubble(content, msgType) {
    if (msgType === 'image') {
        return `<img src="${escHtml(content)}" style="max-width:200px;border-radius:8px;cursor:pointer" onclick="window.open('${escHtml(content)}')">`;
    }
    return escHtml(content);
}

function formatMessage(m) {
    const isMine = m.user_id === myId;
    const time = new Date(m.created_at).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'});
    return `
        <div class="message ${isMine ? 'mine' : 'other'}">
            <div class="msg-meta">${escHtml(m.nickname)} · ${time}</div>
            <div class="msg-bubble">${renderBubble(m.content, m.msg_type)}</div>
        </div>
    `;
}

function appendMessage(data) {
    const container = document.getElementById('chatMessages');
    const isMine = data.user_id === myId;
    const time = new Date(data.created_at).toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'});
    container.insertAdjacentHTML('beforeend', `
        <div class="message ${isMine ? 'mine' : 'other'}">
            <div class="msg-meta">${escHtml(data.nickname)} · ${time}</div>
            <div class="msg-bubble">${renderBubble(data.content, data.msg_type)}</div>
        </div>
    `);
    lastMessageId = Math.max(lastMessageId, Number(data.id) || 0);
    scrollToBottom();
}

async function sendMessage() {
    const input = document.getElementById('msgInput');
    const content = input.value.trim();
    if (!content) return;
    if (muted) { showToast('你已被禁言，无法发送消息'); return; }
    try {
        const res = await fetch('/api/rooms/' + window.ROOM_CODE + '/messages', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({content, msg_type: 'text'})
        });
        const data = await res.json();
        if (!res.ok) {
            if (res.status === 403 && data.error.includes('禁言')) muted = true;
            showToast(data.error || '发送失败');
            return;
        }
        input.value = '';
        await pollMessages();
    } catch (error) {
        showToast('网络暂时不可用，消息未发送');
    }
}

document.getElementById('msgInput').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendMessage();
});

// 图片发送
document.getElementById('imgInput').addEventListener('change', function() {
    if (muted) { showToast('你已被禁言，无法发送消息'); this.value = ''; return; }
    const file = this.files[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) {
        showToast('图片不能超过2MB');
        this.value = '';
        return;
    }
    const reader = new FileReader();
    reader.onload = async function(e) {
        try {
            const res = await fetch('/api/rooms/' + window.ROOM_CODE + '/messages', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({content: e.target.result, msg_type: 'image'})
            });
            const data = await res.json();
            if (!res.ok) {
                showToast(data.error || '图片发送失败');
                return;
            }
            await pollMessages();
        } catch (error) {
            showToast('网络暂时不可用，图片未发送');
        }
    };
    reader.readAsDataURL(file);
    this.value = '';
});

// 成员列表
function toggleMembers() {
    const panel = document.getElementById('memberPanel');
    panel.style.display = panel.style.display === 'none' ? '' : 'none';
    if (panel.style.display !== 'none') loadMembers();
}

async function loadMembers() {
    const res = await fetch('/api/rooms/' + window.ROOM_CODE + '/members');
    const members = await res.json();
    if (!res.ok) {
        if (res.status === 403 || res.status === 404) {
            showToast('你已无法访问此房间');
            setTimeout(() => window.location.href = '/', 1200);
        }
        return;
    }
    // 判断我是不是房主
    const meRes = await fetch('/api/me');
    const me = await meRes.json();
    const isOwner = members.some(m => m.is_owner && m.id === me.user_id);

    document.getElementById('memberList').innerHTML = members.map(m => `
        <div style="padding:8px 0;border-bottom:1px solid #f5f5f5;display:flex;align-items:center;gap:8px">
            <span style="width:8px;height:8px;border-radius:50%;background:#ccc"></span>
            <span style="font-size:14px;flex:1">${escHtml(m.nickname)}</span>
            ${m.is_owner ? '<span class="tag tag-owner">房主</span>' : ''}
            ${isOwner && !m.is_owner ? `<button class="btn btn-sm btn-danger" onclick="kickMember(${m.id}, '${escHtml(m.nickname)}')">踢出</button>` : ''}
        </div>
    `).join('');
}

async function kickMember(uid, name) {
    if (!confirm('确定把「' + name + '」踢出房间吗？')) return;
    const res = await fetch('/api/rooms/' + window.ROOM_CODE + '/kick', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({user_id: uid})
    });
    const data = await res.json();
    if (data.ok) {
        showToast(data.message);
        loadMembers();
    } else {
        showToast(data.error);
    }
}

async function pollMessages() {
    if (messagePollInFlight) return;
    messagePollInFlight = true;
    try {
        const res = await fetch('/api/rooms/' + window.ROOM_CODE + '/messages?after_id=' + lastMessageId);
        if (res.status === 403 || res.status === 404) {
            showToast('你已无法访问此房间');
            setTimeout(() => window.location.href = '/', 1200);
            return;
        }
        if (!res.ok) return;
        const messages = await res.json();
        messages.forEach(appendMessage);
    } catch (error) {
        // 下一轮轮询会自动重试。
    } finally {
        messagePollInFlight = false;
    }
}

async function pollUserState() {
    try {
        const res = await fetch('/api/presence', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({room_code: window.ROOM_CODE})
        });
        if (!res.ok) return;
        const state = await res.json();
        const wasMuted = muted;
        muted = !!state.muted;
        if (muted && !wasMuted) showToast('你已被管理员禁言，无法发送消息');
        if (!muted && wasMuted) showToast('你的禁言已被解除，可以发送消息了');
        if (!muted) {
            document.getElementById('msgInput').placeholder = '输入消息...';
        } else if (wasMuted !== muted) {
            const meRes = await fetch('/api/me');
            if (meRes.ok) {
                const me = await meRes.json();
                if (me.mute_until) document.getElementById('msgInput').placeholder = '🔇 禁言中，无法发送消息（至 ' + me.mute_until + '）';
            }
        }
    } catch (error) {
        // 下一轮轮询会自动重试。
    }
}

// 初始化
async function init() {
    const meRes = await fetch('/api/me');
    const me = await meRes.json();
    myId = me.user_id;
    muted = !!me.muted;
    if (me.mute_until) {
        const input = document.getElementById('msgInput');
        input.placeholder = '🔇 禁言中，无法发送消息（至 ' + me.mute_until + '）';
    }
    const res = await fetch('/api/rooms/' + window.ROOM_CODE + '/messages');
    const messages = await res.json();
    document.getElementById('chatMessages').innerHTML = messages.map(m => formatMessage(m)).join('');
    lastMessageId = messages.reduce((latest, message) => Math.max(latest, Number(message.id) || 0), 0);
    scrollToBottom();
    setInterval(() => {
        pollMessages();
        pollUserState();
        if (document.getElementById('memberPanel').style.display !== 'none') loadMembers();
    }, 3000);
}
init();
