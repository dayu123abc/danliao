let currentJoinCode = null;
let currentUser = null;
const socket = io();

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

// ============ 初始化 ============
async function loadUserInfo() {
    const res = await fetch('/api/me');
    const data = await res.json();
    currentUser = data;
    document.getElementById('userNickname').textContent = data.nickname;
    document.getElementById('userPhone').textContent = '手机号: ' + data.phone;
    document.getElementById('createdCount').textContent = data.created_count + '/' + data.max_created;
    document.getElementById('joinedCount').textContent = data.joined_count + '/' + data.max_joined;
    if (data.is_admin) {
        document.getElementById('adminBtn').style.display = '';
    }
}

async function loadRooms() {
    const res = await fetch('/api/rooms/my');
    const rooms = await res.json();
    const list = document.getElementById('roomList');
    if (rooms.length === 0) {
        list.innerHTML = '<p style="color:#aaa;font-size:14px">还没有加入任何聊天室</p>';
        return;
    }
    list.innerHTML = rooms.map(r => `
        <div class="room-card" onclick="enterRoom('${r.code}')">
            <div class="room-tag">
                ${r.is_owner ? '<span class="tag tag-owner">房主</span>' : (r.is_admin_access && !r.is_member ? '<span class="tag tag-member">管理员访问</span>' : '<span class="tag tag-member">成员</span>')}
            </div>
            <div class="room-name">${escHtml(r.name)}</div>
            <div class="room-code">房间号: ${r.code}</div>
            <div style="margin-top:12px" onclick="event.stopPropagation()">
                ${r.is_owner
                    ? '<button class="btn btn-sm btn-danger" onclick="deleteRoom(\'' + r.code + '\')">删除房间</button>'
                    : (r.is_member ? '<button class="btn btn-sm btn-danger" onclick="leaveRoom(\'' + r.code + '\')">退出房间</button>' : '')}
            </div>
        </div>
    `).join('');
}

async function loadRequests() {
    const res = await fetch('/api/rooms/join-requests');
    const requests = await res.json();
    const section = document.getElementById('requestsSection');
    const list = document.getElementById('requestList');
    document.getElementById('requestCount').textContent = requests.length;
    if (requests.length === 0) {
        section.style.display = 'none';
        return;
    }
    section.style.display = 'block';
    list.innerHTML = requests.map(r => `
        <div class="request-item">
            <div class="req-info">
                <div class="name">${escHtml(r.nickname)}</div>
                <div class="meta">申请加入「${escHtml(r.room_name)}」(${r.room_code})</div>
            </div>
            <div class="req-actions">
                <button class="btn btn-sm btn-success" onclick="handleRequest(${r.request_id}, 'approve')">同意</button>
                <button class="btn btn-sm btn-danger" onclick="handleRequest(${r.request_id}, 'reject')">拒绝</button>
            </div>
        </div>
    `).join('');
}

// ============ 创建房间 ============
function openCreateModal() {
    document.getElementById('createModal').classList.add('active');
}
function closeCreateModal() {
    document.getElementById('createModal').classList.remove('active');
    document.getElementById('newRoomName').value = '';
    document.getElementById('newRoomPassword').value = '';
}

async function createRoom() {
    const name = document.getElementById('newRoomName').value.trim();
    const password = document.getElementById('newRoomPassword').value;
    if (!name) return showToast('请输入房间名称');
    if (!password) return showToast('请设置房间密码');
    const res = await fetch('/api/rooms/create', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name, password})
    });
    const data = await res.json();
    if (data.ok) {
        showToast('创建成功！房间号: ' + data.code);
        closeCreateModal();
        loadUserInfo();
        loadRooms();
    } else {
        showToast(data.error || '创建失败');
    }
}

// ============ 搜索加入 ============
async function searchRoom() {
    const code = document.getElementById('searchCode').value.trim();
    if (!code) return showToast('请输入房间号');
    if (code.length !== 8) return showToast('房间号为8位数字');
    currentJoinCode = code;
    document.getElementById('passwordModalRoom').textContent = '房间号: ' + code;
    document.getElementById('passwordModal').classList.add('active');
}

function closePasswordModal() {
    document.getElementById('passwordModal').classList.remove('active');
    document.getElementById('joinPassword').value = '';
    currentJoinCode = null;
}

async function confirmJoin() {
    const password = document.getElementById('joinPassword').value;
    if (!password) return showToast('请输入房间密码');
    const res = await fetch('/api/rooms/search', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({code: currentJoinCode, password})
    });
    const data = await res.json();
    if (data.ok) {
        if (data.status === 'owner' || data.status === 'approved' || data.status === 'admin') {
            closePasswordModal();
            window.location.href = '/chat/' + data.code;
        } else if (data.status === 'pending') {
            closePasswordModal();
            showToast('申请已发送，等待房主同意');
        }
    } else {
        showToast(data.error || '操作失败');
    }
}

// ============ 审批 ============
async function handleRequest(requestId, action) {
    const res = await fetch('/api/rooms/approve', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({request_id: requestId, action})
    });
    const data = await res.json();
    if (data.ok) {
        showToast(data.message);
        loadRequests();
    } else {
        showToast(data.error);
    }
}

function enterRoom(code) {
    window.location.href = '/chat/' + code;
}

// ============ 删除/退出房间 ============
async function deleteRoom(code) {
    if (!confirm('确定要删除这个房间吗？')) return;
    const res = await fetch('/api/rooms/' + code + '/delete', {method: 'POST'});
    const data = await res.json();
    if (data.ok) {
        showToast('房间已删除');
        loadUserInfo();
        loadRooms();
    } else {
        showToast(data.error);
    }
}

async function leaveRoom(code) {
    if (!confirm('确定要退出这个房间吗？')) return;
    const res = await fetch('/api/rooms/' + code + '/leave', {method: 'POST'});
    const data = await res.json();
    if (data.ok) {
        showToast('已退出房间');
        loadUserInfo();
        loadRooms();
    } else {
        showToast(data.error);
    }
}

// ============ SocketIO ============
socket.on('connect', () => {
    socket.emit('owner_connect');
});
socket.on('new_join_request', (data) => {
    showToast(`${data.nickname} 申请加入「${data.room_name}」`);
    loadRequests();
});
socket.on('join_approved', (data) => {
    showToast(`你已被同意加入「${data.room_name}」！`);
    loadUserInfo();
    loadRooms();
});
socket.on('join_rejected', (data) => {
    showToast(`你加入「${data.room_name}」的申请被拒绝了`);
});
socket.on('you_muted', (data) => {
    showToast('你已被管理员禁言，无法发送消息');
});
socket.on('you_unmuted', () => {
    showToast('你的禁言已被解除');
});

// ============ 启动 ============
loadUserInfo();
loadRooms();
loadRequests();
