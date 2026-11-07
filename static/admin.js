let msgOffset = 0;

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

function switchTab(tab) {
    document.querySelectorAll('.admin-tab').forEach(b => b.classList.remove('active'));
    document.querySelector(`.admin-tab[data-tab="${tab}"]`).classList.add('active');
    ['stats', 'users', 'rooms', 'messages'].forEach(t => {
        document.getElementById('tab-' + t).style.display = (t === tab) ? '' : 'none';
    });
    if (tab === 'stats') loadStats();
    if (tab === 'users') loadUsers();
    if (tab === 'rooms') loadRooms();
    if (tab === 'messages') { msgOffset = 0; loadMessages(true); }
}

// ============ 概览 ============
async function loadStats() {
    const res = await fetch('/api/admin/stats');
    const s = await res.json();
    const cards = [
        ['👥', s.user_count, '用户总数'],
        ['🏠', s.room_count, '聊天室总数'],
        ['💬', s.msg_count, '消息总数'],
        ['📷', s.img_count, '图片消息'],
        ['🔇', s.muted_count, '被禁言用户'],
        ['🟢', s.online_count, '当前在线'],
        ['⏳', s.pending_count, '待审批申请']
    ];
    document.getElementById('statsGrid').innerHTML = cards.map(c => `
        <div class="stat-card">
            <div style="font-size:24px">${c[0]}</div>
            <div class="num">${c[1]}</div>
            <div class="label">${c[2]}</div>
        </div>
    `).join('');
}

// ============ 用户 ============
async function loadUsers() {
    const res = await fetch('/api/admin/users');
    const users = await res.json();
    const list = document.getElementById('userList');
    if (!users.length) { list.innerHTML = '<div class="empty">暂无用户</div>'; return; }
    list.innerHTML = users.map(u => `
        <div class="list-item">
            <div class="main">
                <div class="name">
                    ${escHtml(u.nickname)}
                    ${u.is_admin ? '<span class="pill pill-admin">管理员</span>' : ''}
                    ${u.muted ? '<span class="pill pill-muted">禁言中</span>' : ''}
                    ${u.online ? '<span class="pill pill-online">在线</span>' : ''}
                </div>
                <div class="meta">
                    📱 ${u.phone} · 🏠 创建${u.created_count} · 👥 加入${u.joined_count} · 💬 ${u.msg_count}条 · 注册于 ${escHtml(u.created_at.slice(0,16).replace('T',' '))}
                    ${u.mute_text ? ' · 🔇 禁言至 ' + escHtml(u.mute_text) : ''}
                </div>
            </div>
            <div class="actions">
                ${!u.is_admin ? `
                    <select class="mute-select" id="muteSel${u.id}">
                        <option value="30">禁言30分钟</option>
                        <option value="60">禁言1小时</option>
                        <option value="1440">禁言1天</option>
                        <option value="10080">禁言7天</option>
                        <option value="forever">永久禁言</option>
                    </select>
                    ${u.muted
                        ? `<button class="btn btn-xs btn-success" onclick="unmuteUser(${u.id}, '${escHtml(u.nickname)}')">解禁</button>`
                        : `<button class="btn btn-xs btn-danger" onclick="muteUser(${u.id}, '${escHtml(u.nickname)}')">禁言</button>`}
                    <button class="btn btn-xs btn-danger" onclick="deleteUser(${u.id}, '${escHtml(u.nickname)}')">删除</button>
                ` : ''}
            </div>
        </div>
    `).join('');
}

async function muteUser(uid, name) {
    const minutes = document.getElementById('muteSel' + uid).value;
    if (!confirm('确定禁言「' + name + '」吗？')) return;
    const res = await fetch('/api/admin/users/' + uid + '/mute', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({duration: minutes})
    });
    const data = await res.json();
    showToast(data.ok ? data.message : data.error);
    loadUsers();
}

async function unmuteUser(uid, name) {
    if (!confirm('确定解除「' + name + '」的禁言吗？')) return;
    const res = await fetch('/api/admin/users/' + uid + '/unmute', {method: 'POST'});
    const data = await res.json();
    showToast(data.ok ? data.message : data.error);
    loadUsers();
}

async function deleteUser(uid, name) {
    if (!confirm('确定删除用户「' + name + '」吗？其所有房间、消息都会被清除！')) return;
    const res = await fetch('/api/admin/users/' + uid + '/delete', {method: 'POST'});
    const data = await res.json();
    showToast(data.ok ? data.message : data.error);
    loadUsers();
}

// ============ 房间 ============
async function loadRooms() {
    const res = await fetch('/api/admin/rooms');
    const rooms = await res.json();
    const list = document.getElementById('roomList');
    if (!rooms.length) { list.innerHTML = '<div class="empty">暂无房间</div>'; return; }
    list.innerHTML = rooms.map(r => `
        <div class="list-item">
            <div class="main">
                <div class="name">${escHtml(r.name)} <span class="pill pill-room">${r.code}</span></div>
                <div class="meta">
                    👑 房主 ${escHtml(r.owner_name)} · 👥 ${r.member_count}人 · 💬 ${r.msg_count}条消息 · 创建于 ${escHtml(r.created_at.slice(0,16).replace('T',' '))}
                </div>
            </div>
            <div class="actions">
                <button class="btn btn-xs btn-danger" onclick="deleteRoom(${r.id}, '${escHtml(r.name)}')">删除房间</button>
            </div>
        </div>
    `).join('');
}

async function deleteRoom(rid, name) {
    if (!confirm('确定删除房间「' + name + '」吗？所有消息和成员关系都会被清除！')) return;
    const res = await fetch('/api/admin/rooms/' + rid + '/delete', {method: 'POST'});
    const data = await res.json();
    showToast(data.ok ? data.message : data.error);
    loadRooms();
}

// ============ 消息 ============
async function loadMessages(reset) {
    const res = await fetch('/api/admin/messages?limit=50&offset=' + msgOffset);
    const msgs = await res.json();
    const list = document.getElementById('msgList');
    if (reset) list.innerHTML = '';
    if (!msgs.length) {
        if (!reset) list.innerHTML += '<div class="empty">没有更多消息了</div>';
        return;
    }
    list.innerHTML += msgs.map(m => `
        <div class="list-item">
            <div class="main">
                <div class="name">${escHtml(m.nickname)} <span class="pill pill-room">${escHtml(m.room_name)}</span>
                    ${m.msg_type === 'image' ? '<span class="pill pill-msg">图片</span>' : ''}
                </div>
                <div class="meta">${escHtml(m.content)}</div>
                <div class="meta" style="font-size:11px">${escHtml(m.created_at.slice(0,16).replace('T',' '))}</div>
            </div>
            <div class="actions">
                <button class="btn btn-xs btn-danger" onclick="deleteMessage(${m.id})">删除</button>
            </div>
        </div>
    `).join('');
    msgOffset += msgs.length;
}

function loadMoreMessages() { loadMessages(false); }

async function deleteMessage(mid) {
    if (!confirm('确定删除这条消息吗？')) return;
    const res = await fetch('/api/admin/messages/' + mid + '/delete', {method: 'POST'});
    const data = await res.json();
    showToast(data.ok ? data.message : data.error);
    msgOffset = 0;
    loadMessages(true);
}

// ============ 启动 ============
loadStats();
fetch('/api/presence', {method: 'POST'}).catch(() => {});
setInterval(() => fetch('/api/presence', {method: 'POST'}).catch(() => {}), 30000);
