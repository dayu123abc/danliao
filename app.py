import os
import sqlite3
import base64
import random
import string
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24).hex()
app.config['DATABASE'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chatroom.db')
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

socketio = SocketIO(app, cors_allowed_origins="*")

# ============ 在线用户追踪 ============
online_users = {}  # user_id -> room_id

# ============ 数据库 ============

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(app.config['DATABASE'])
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nickname TEXT UNIQUE NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            mute_until TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (owner_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS room_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            UNIQUE(room_id, user_id),
            FOREIGN KEY (room_id) REFERENCES rooms(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            msg_type TEXT NOT NULL DEFAULT 'text',
            created_at TEXT NOT NULL,
            FOREIGN KEY (room_id) REFERENCES rooms(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    ''')
    # 兼容旧数据库：给 users 表补 mute_until 列（若不存在）
    try:
        db.execute("ALTER TABLE users ADD COLUMN mute_until TEXT")
    except sqlite3.OperationalError:
        pass  # 列已存在

    # 检查是否已有管理员，没有就创建一个
    admin = db.execute("SELECT id FROM users WHERE is_admin = 1").fetchone()
    if not admin:
        db.execute(
            "INSERT INTO users (phone, password_hash, nickname, is_admin, created_at) VALUES (?, ?, ?, 1, ?)",
            ('10000000000', generate_password_hash('admin123'), '管理员', datetime.now().isoformat())
        )
    db.commit()
    db.close()

# ============ 工具函数 ============

MAX_CREATED_ROOMS = 5
MAX_JOINED_ROOMS = 10

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': '未登录'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_current_user()
        if not user or not user['is_admin']:
            return jsonify({'error': '需要管理员权限'}), 403
        return f(*args, **kwargs)
    return decorated

def generate_room_code():
    """生成8位数字房间号，确保不重复"""
    db = get_db()
    while True:
        code = ''.join(random.choices(string.digits, k=8))
        row = db.execute('SELECT id FROM rooms WHERE code = ?', (code,)).fetchone()
        if not row:
            return code

def get_current_user():
    if 'user_id' not in session:
        return None
    db = get_db()
    return db.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()

def is_room_member(room_id, user_id):
    db = get_db()
    row = db.execute(
        "SELECT status FROM room_members WHERE room_id = ? AND user_id = ?",
        (room_id, user_id)
    ).fetchone()
    return row and row['status'] == 'approved'

def is_room_owner(room_id, user_id):
    db = get_db()
    row = db.execute('SELECT owner_id FROM rooms WHERE id = ?', (room_id,)).fetchone()
    return row and row['owner_id'] == user_id

def can_access_room(room_id, user):
    return bool(
        user and (
            user['is_admin']
            or is_room_member(room_id, user['id'])
            or is_room_owner(room_id, user['id'])
        )
    )

def is_muted(user):
    """检查用户是否被禁言，返回 (是否禁言, 截止时间字符串或None)"""
    if not user or not user['mute_until']:
        return False, None
    mute_until = user['mute_until']
    # 永久禁言标记
    if mute_until == 'forever':
        return True, 'forever'
    try:
        until = datetime.fromisoformat(mute_until)
    except ValueError:
        return False, None
    if until > datetime.now():
        return True, mute_until
    return False, None

def format_mute_until(mute_until):
    """把禁言截止时间格式化为可读文本"""
    if not mute_until:
        return None
    if mute_until == 'forever':
        return '永久'
    try:
        until = datetime.fromisoformat(mute_until)
    except ValueError:
        return None
    return until.strftime('%Y-%m-%d %H:%M')

# ============ 页面路由 ============

@app.route('/')
@login_required
def home():
    return render_template('home.html')

@app.route('/login')
def login():
    if 'user_id' in session:
        return redirect(url_for('home'))
    return render_template('login.html')

@app.route('/register')
def register():
    if 'user_id' in session:
        return redirect(url_for('home'))
    return render_template('register.html')

@app.route('/chat/<room_code>')
@login_required
def chat_page(room_code):
    db = get_db()
    room = db.execute('SELECT * FROM rooms WHERE code = ?', (room_code,)).fetchone()
    if not room:
        return redirect(url_for('home'))
    user = get_current_user()
    if not can_access_room(room['id'], user):
        return redirect(url_for('home'))
    return render_template('chat.html', room_code=room_code, room_name=room['name'])

@app.route('/admin')
@login_required
@admin_required
def admin_page():
    return render_template('admin.html')

# ============ API: 认证 ============

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.get_json()
    phone = data.get('phone', '').strip()
    password = data.get('password', '')
    nickname = data.get('nickname', '').strip()

    if not phone or not password or not nickname:
        return jsonify({'error': '请填写完整信息'}), 400
    if not phone.isdigit() or len(phone) != 11:
        return jsonify({'error': '请输入11位手机号'}), 400
    if len(password) < 6:
        return jsonify({'error': '密码至少6位'}), 400
    if len(nickname) < 2 or len(nickname) > 20:
        return jsonify({'error': '昵称2-20个字符'}), 400

    db = get_db()
    existing_phone = db.execute('SELECT id FROM users WHERE phone = ?', (phone,)).fetchone()
    if existing_phone:
        return jsonify({'error': '该手机号已注册'}), 400
    existing_nick = db.execute('SELECT id FROM users WHERE nickname = ?', (nickname,)).fetchone()
    if existing_nick:
        return jsonify({'error': '该昵称已被使用，换一个吧'}), 400

    cur = db.execute(
        'INSERT INTO users (phone, password_hash, nickname, created_at) VALUES (?, ?, ?, ?)',
        (phone, generate_password_hash(password), nickname, datetime.now().isoformat())
    )
    db.commit()
    session['user_id'] = cur.lastrowid
    return jsonify({'ok': True, 'nickname': nickname})

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    phone = data.get('phone', '').strip()
    password = data.get('password', '')

    db = get_db()
    user = db.execute('SELECT * FROM users WHERE phone = ?', (phone,)).fetchone()
    if not user or not check_password_hash(user['password_hash'], password):
        return jsonify({'error': '手机号或密码错误'}), 401

    session['user_id'] = user['id']
    return jsonify({'ok': True, 'nickname': user['nickname'], 'is_admin': bool(user['is_admin'])})

@app.route('/api/logout')
def api_logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/api/me')
@login_required
def api_me():
    user = get_current_user()
    db = get_db()
    created_count = db.execute('SELECT COUNT(*) as c FROM rooms WHERE owner_id = ?', (user['id'],)).fetchone()['c']
    joined_count = db.execute(
        "SELECT COUNT(*) as c FROM room_members WHERE user_id = ? AND status = 'approved'",
        (user['id'],)
    ).fetchone()['c']
    return jsonify({
        'user_id': user['id'],
        'nickname': user['nickname'],
        'phone': user['phone'],
        'is_admin': bool(user['is_admin']),
        'muted': is_muted(user)[0],
        'mute_until': format_mute_until(user['mute_until']),
        'created_count': created_count,
        'joined_count': joined_count,
        'max_created': MAX_CREATED_ROOMS,
        'max_joined': MAX_JOINED_ROOMS
    })

# ============ API: 管理员 ============

@app.route('/api/admin/stats')
@login_required
@admin_required
def api_admin_stats():
    db = get_db()
    user_count = db.execute('SELECT COUNT(*) as c FROM users').fetchone()['c']
    room_count = db.execute('SELECT COUNT(*) as c FROM rooms').fetchone()['c']
    msg_count = db.execute('SELECT COUNT(*) as c FROM messages').fetchone()['c']
    img_count = db.execute("SELECT COUNT(*) as c FROM messages WHERE msg_type = 'image'").fetchone()['c']
    muted_count = db.execute(
        "SELECT COUNT(*) as c FROM users WHERE mute_until IS NOT NULL AND mute_until != ''"
    ).fetchone()['c']
    online_count = len(online_users)
    pending_count = db.execute("SELECT COUNT(*) as c FROM room_members WHERE status = 'pending'").fetchone()['c']
    return jsonify({
        'user_count': user_count,
        'room_count': room_count,
        'msg_count': msg_count,
        'img_count': img_count,
        'muted_count': muted_count,
        'online_count': online_count,
        'pending_count': pending_count
    })

@app.route('/api/admin/users')
@login_required
@admin_required
def api_admin_users():
    db = get_db()
    users = db.execute('''
        SELECT u.id, u.phone, u.nickname, u.is_admin, u.mute_until, u.created_at,
               (SELECT COUNT(*) FROM rooms r WHERE r.owner_id = u.id) as created_count,
               (SELECT COUNT(*) FROM room_members rm WHERE rm.user_id = u.id AND rm.status = 'approved') as joined_count,
               (SELECT COUNT(*) FROM messages m WHERE m.user_id = u.id) as msg_count,
               (u.id IN (SELECT user_id FROM room_members WHERE status='approved') OR
                u.id IN (SELECT owner_id FROM rooms)) as in_any_room
        FROM users u
        ORDER BY u.created_at ASC
    ''').fetchall()
    result = []
    for u in users:
        muted, until = is_muted(u)
        d = dict(u)
        d['muted'] = muted
        d['mute_text'] = format_mute_until(u['mute_until'])
        d['online'] = u['id'] in online_users
        result.append(d)
    return jsonify(result)

@app.route('/api/admin/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def api_admin_delete_user(user_id):
    db = get_db()
    target = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not target:
        return jsonify({'error': '用户不存在'}), 404
    if target['is_admin']:
        return jsonify({'error': '不能删除管理员'}), 403

    # 删除用户创建的房间及其数据
    rooms = db.execute('SELECT id FROM rooms WHERE owner_id = ?', (user_id,)).fetchall()
    for r in rooms:
        db.execute('DELETE FROM messages WHERE room_id = ?', (r['id'],))
        db.execute('DELETE FROM room_members WHERE room_id = ?', (r['id'],))
        db.execute('DELETE FROM rooms WHERE id = ?', (r['id'],))
    # 删除用户的所有成员关系和消息
    db.execute('DELETE FROM room_members WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM messages WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()
    # 清理在线记录
    online_users.pop(user_id, None)
    return jsonify({'ok': True, 'message': '用户已删除'})

@app.route('/api/admin/users/<int:user_id>/mute', methods=['POST'])
@login_required
@admin_required
def api_admin_mute_user(user_id):
    """禁言用户。data: {duration: 'forever' 或 分钟数}"""
    user = get_current_user()
    data = request.get_json() or {}
    db = get_db()
    target = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not target:
        return jsonify({'error': '用户不存在'}), 404
    if target['is_admin']:
        return jsonify({'error': '不能禁言管理员'}), 403

    duration = data.get('duration', '30')
    if duration == 'forever':
        mute_until = 'forever'
        text = '永久禁言'
    else:
        try:
            minutes = int(duration)
        except (TypeError, ValueError):
            return jsonify({'error': '无效时长'}), 400
        if minutes < 1 or minutes > 43200:  # 最多30天
            return jsonify({'error': '时长需在1分钟到30天之间'}), 400
        mute_until = (datetime.now() + timedelta(minutes=minutes)).isoformat()
        text = f'禁言{minutes}分钟'

    db.execute("UPDATE users SET mute_until = ? WHERE id = ?", (mute_until, user_id))
    db.commit()
    # 通知被禁言用户
    socketio.emit('you_muted', {'until': mute_until}, room=f'user_{user_id}')
    return jsonify({'ok': True, 'message': f'已将 {target["nickname"]} {text}'})

@app.route('/api/admin/users/<int:user_id>/unmute', methods=['POST'])
@login_required
@admin_required
def api_admin_unmute_user(user_id):
    db = get_db()
    target = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not target:
        return jsonify({'error': '用户不存在'}), 404
    db.execute("UPDATE users SET mute_until = NULL WHERE id = ?", (user_id,))
    db.commit()
    socketio.emit('you_unmuted', {}, room=f'user_{user_id}')
    return jsonify({'ok': True, 'message': f'已解除 {target["nickname"]} 的禁言'})

@app.route('/api/admin/rooms')
@login_required
@admin_required
def api_admin_rooms():
    db = get_db()
    rooms = db.execute('''
        SELECT r.id, r.code, r.name, r.owner_id, r.created_at,
               u.nickname as owner_name,
               (SELECT COUNT(*) FROM room_members rm WHERE rm.room_id = r.id AND rm.status = 'approved') as member_count,
               (SELECT COUNT(*) FROM messages m WHERE m.room_id = r.id) as msg_count
        FROM rooms r
        JOIN users u ON u.id = r.owner_id
        ORDER BY r.created_at DESC
    ''').fetchall()
    return jsonify([dict(r) for r in rooms])

@app.route('/api/admin/rooms/<int:room_id>/delete', methods=['POST'])
@login_required
@admin_required
def api_admin_delete_room(room_id):
    db = get_db()
    room = db.execute('SELECT * FROM rooms WHERE id = ?', (room_id,)).fetchone()
    if not room:
        return jsonify({'error': '房间不存在'}), 404
    db.execute('DELETE FROM messages WHERE room_id = ?', (room['id'],))
    db.execute('DELETE FROM room_members WHERE room_id = ?', (room['id'],))
    db.execute('DELETE FROM rooms WHERE id = ?', (room['id'],))
    db.commit()
    return jsonify({'ok': True, 'message': '房间已删除'})

@app.route('/api/admin/messages')
@login_required
@admin_required
def api_admin_messages():
    db = get_db()
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))
    messages = db.execute('''
        SELECT m.id, m.content, m.msg_type, m.created_at,
               u.id as user_id, u.nickname,
               r.code as room_code, r.name as room_name
        FROM messages m
        JOIN users u ON u.id = m.user_id
        JOIN rooms r ON r.id = m.room_id
        ORDER BY m.created_at DESC
        LIMIT ? OFFSET ?
    ''', (limit, offset)).fetchall()
    result = []
    for m in messages:
        d = dict(m)
        if d['msg_type'] == 'image':
            d['content'] = '[图片]' + (d['content'][:60] if d['content'].startswith('data:') else '')
        elif len(d['content']) > 100:
            d['content'] = d['content'][:100] + '...'
        result.append(d)
    return jsonify(result)

@app.route('/api/admin/messages/<int:msg_id>/delete', methods=['POST'])
@login_required
@admin_required
def api_admin_delete_message(msg_id):
    db = get_db()
    msg = db.execute('SELECT * FROM messages WHERE id = ?', (msg_id,)).fetchone()
    if not msg:
        return jsonify({'error': '消息不存在'}), 404
    db.execute('DELETE FROM messages WHERE id = ?', (msg_id,))
    db.commit()
    return jsonify({'ok': True, 'message': '消息已删除'})

# ============ API: 房间管理 ============

@app.route('/api/rooms/create', methods=['POST'])
@login_required
def api_create_room():
    user = get_current_user()
    data = request.get_json()
    name = data.get('name', '').strip()
    password = data.get('password', '')

    if not name:
        return jsonify({'error': '请输入房间名称'}), 400
    if not password:
        return jsonify({'error': '请设置房间密码'}), 400

    db = get_db()
    created_count = db.execute('SELECT COUNT(*) as c FROM rooms WHERE owner_id = ?', (user['id'],)).fetchone()['c']
    if created_count >= MAX_CREATED_ROOMS:
        return jsonify({'error': f'每人最多创建{MAX_CREATED_ROOMS}个聊天室'}), 400

    code = generate_room_code()
    cur = db.execute(
        'INSERT INTO rooms (code, name, owner_id, password_hash, created_at) VALUES (?, ?, ?, ?, ?)',
        (code, name, user['id'], generate_password_hash(password), datetime.now().isoformat())
    )
    room_id = cur.lastrowid
    db.execute(
        "INSERT INTO room_members (room_id, user_id, status, created_at) VALUES (?, ?, 'approved', ?)",
        (room_id, user['id'], datetime.now().isoformat())
    )
    db.commit()
    return jsonify({'ok': True, 'code': code, 'name': name})

@app.route('/api/rooms/my')
@login_required
def api_my_rooms():
    user = get_current_user()
    db = get_db()
    if user['is_admin']:
        rooms = db.execute('''
            SELECT r.id, r.code, r.name, r.owner_id, r.created_at,
                   (r.owner_id = ?) as is_owner,
                   EXISTS (
                       SELECT 1 FROM room_members rm
                       WHERE rm.room_id = r.id AND rm.user_id = ? AND rm.status = 'approved'
                   ) as is_member
            FROM rooms r
            ORDER BY r.created_at DESC
        ''', (user['id'], user['id'])).fetchall()
    else:
        rooms = db.execute('''
            SELECT r.id, r.code, r.name, r.owner_id, r.created_at,
                   (r.owner_id = ?) as is_owner, 1 as is_member
            FROM rooms r
            JOIN room_members rm ON rm.room_id = r.id
            WHERE rm.user_id = ? AND rm.status = 'approved'
            ORDER BY r.created_at DESC
        ''', (user['id'], user['id'])).fetchall()
    result = [dict(room) for room in rooms]
    for room in result:
        room['is_admin_access'] = bool(user['is_admin'])
    return jsonify(result)

@app.route('/api/rooms/<room_code>/members')
@login_required
def api_room_members(room_code):
    user = get_current_user()
    db = get_db()
    room = db.execute('SELECT * FROM rooms WHERE code = ?', (room_code,)).fetchone()
    if not room:
        return jsonify({'error': '房间不存在'}), 404
    if not can_access_room(room['id'], user):
        return jsonify({'error': '无权限访问'}), 403
    members = db.execute('''
        SELECT u.id, u.nickname,
               (u.id = r.owner_id) as is_owner
        FROM room_members rm
        JOIN users u ON u.id = rm.user_id
        JOIN rooms r ON r.id = rm.room_id
        WHERE rm.room_id = ? AND rm.status = 'approved'
        ORDER BY is_owner DESC, u.nickname ASC
    ''', (room['id'],)).fetchall()
    return jsonify([dict(m) for m in members])

@app.route('/api/rooms/<room_code>/kick', methods=['POST'])
@login_required
def api_kick_member(room_code):
    """房主踢出成员"""
    user = get_current_user()
    data = request.get_json()
    target_user_id = data.get('user_id')
    db = get_db()
    room = db.execute('SELECT * FROM rooms WHERE code = ?', (room_code,)).fetchone()
    if not room:
        return jsonify({'error': '房间不存在'}), 404
    if room['owner_id'] != user['id']:
        return jsonify({'error': '只有房主才能踢人'}), 403
    if target_user_id == user['id']:
        return jsonify({'error': '不能踢自己'}), 400

    target = db.execute('SELECT * FROM users WHERE id = ?', (target_user_id,)).fetchone()
    if not target:
        return jsonify({'error': '用户不存在'}), 404

    # 删除成员关系
    db.execute(
        'DELETE FROM room_members WHERE room_id = ? AND user_id = ?',
        (room['id'], target_user_id)
    )
    db.commit()

    # 通知被踢者
    socketio.emit('kicked', {
        'room_code': room['code'],
        'room_name': room['name']
    }, room=f'user_{target_user_id}')

    # 通知房间内所有人刷新成员列表
    socketio.emit('member_update', {
        'room_code': room['code'],
        'nickname': target['nickname']
    }, room=f'room_{room["id"]}')

    return jsonify({'ok': True, 'message': f'已把 {target["nickname"]} 踢出房间'})

@app.route('/api/rooms/<room_code>/delete', methods=['POST'])
@login_required
def api_delete_room(room_code):
    user = get_current_user()
    db = get_db()
    room = db.execute('SELECT * FROM rooms WHERE code = ?', (room_code,)).fetchone()
    if not room:
        return jsonify({'error': '房间不存在'}), 404
    if room['owner_id'] != user['id']:
        return jsonify({'error': '只有房主才能删除房间'}), 403
    db.execute('DELETE FROM messages WHERE room_id = ?', (room['id'],))
    db.execute('DELETE FROM room_members WHERE room_id = ?', (room['id'],))
    db.execute('DELETE FROM rooms WHERE id = ?', (room['id'],))
    db.commit()
    return jsonify({'ok': True, 'message': '房间已删除'})

@app.route('/api/rooms/<room_code>/leave', methods=['POST'])
@login_required
def api_leave_room(room_code):
    user = get_current_user()
    db = get_db()
    room = db.execute('SELECT * FROM rooms WHERE code = ?', (room_code,)).fetchone()
    if not room:
        return jsonify({'error': '房间不存在'}), 404
    if room['owner_id'] == user['id']:
        return jsonify({'error': '房主不能退出，请先删除房间'}), 403
    db.execute('DELETE FROM room_members WHERE room_id = ? AND user_id = ?', (room['id'], user['id']))
    db.commit()
    return jsonify({'ok': True, 'message': '已退出房间'})

@app.route('/api/rooms/join-requests')
@login_required
def api_join_requests():
    user = get_current_user()
    db = get_db()
    requests = db.execute('''
        SELECT rm.id as request_id, rm.created_at,
               r.code as room_code, r.name as room_name,
               u.id as user_id, u.nickname, u.phone
        FROM room_members rm
        JOIN rooms r ON r.id = rm.room_id
        JOIN users u ON u.id = rm.user_id
        WHERE r.owner_id = ? AND rm.status = 'pending'
        ORDER BY rm.created_at DESC
    ''', (user['id'],)).fetchall()
    return jsonify([dict(r) for r in requests])

@app.route('/api/rooms/search', methods=['POST'])
@login_required
def api_search_room():
    user = get_current_user()
    data = request.get_json()
    code = data.get('code', '').strip()
    password = data.get('password', '')

    if not code:
        return jsonify({'error': '请输入房间号'}), 400

    db = get_db()
    room = db.execute('SELECT * FROM rooms WHERE code = ?', (code,)).fetchone()
    if not room:
        return jsonify({'error': '未找到该聊天室'}), 404
    if not user['is_admin'] and not check_password_hash(room['password_hash'], password):
        return jsonify({'error': '房间密码错误'}), 403
    if user['is_admin'] and room['owner_id'] != user['id'] and not is_room_member(room['id'], user['id']):
        return jsonify({'ok': True, 'status': 'admin', 'code': room['code'], 'name': room['name']})
    if room['owner_id'] == user['id']:
        return jsonify({'ok': True, 'status': 'owner', 'code': room['code'], 'name': room['name']})

    existing = db.execute(
        "SELECT status FROM room_members WHERE room_id = ? AND user_id = ?",
        (room['id'], user['id'])
    ).fetchone()
    if existing:
        if existing['status'] == 'approved':
            return jsonify({'ok': True, 'status': 'approved', 'code': room['code'], 'name': room['name']})
        else:
            return jsonify({'ok': True, 'status': 'pending', 'code': room['code'], 'name': room['name'],
                            'message': '已发送申请，等待房主同意'})

    joined_count = db.execute(
        "SELECT COUNT(*) as c FROM room_members WHERE user_id = ? AND status = 'approved'",
        (user['id'],)
    ).fetchone()['c']
    if joined_count >= MAX_JOINED_ROOMS:
        return jsonify({'error': f'每人最多加入{MAX_JOINED_ROOMS}个聊天室'}), 400

    db.execute(
        "INSERT INTO room_members (room_id, user_id, status, created_at) VALUES (?, ?, 'pending', ?)",
        (room['id'], user['id'], datetime.now().isoformat())
    )
    db.commit()
    socketio.emit('new_join_request', {
        'room_code': room['code'],
        'room_name': room['name'],
        'user_id': user['id'],
        'nickname': user['nickname']
    }, room=f'owner_{room["owner_id"]}')
    return jsonify({'ok': True, 'status': 'pending', 'code': room['code'], 'name': room['name'],
                    'message': '已发送申请，等待房主同意'})

@app.route('/api/rooms/approve', methods=['POST'])
@login_required
def api_approve_join():
    user = get_current_user()
    data = request.get_json()
    request_id = data.get('request_id')
    action = data.get('action')

    db = get_db()
    req = db.execute('''
        SELECT rm.*, r.owner_id, r.code as room_code, r.name as room_name
        FROM room_members rm
        JOIN rooms r ON r.id = rm.room_id
        WHERE rm.id = ?
    ''', (request_id,)).fetchone()
    if not req:
        return jsonify({'error': '申请不存在'}), 404
    if req['owner_id'] != user['id']:
        return jsonify({'error': '无权限操作'}), 403

    if action == 'approve':
        db.execute("UPDATE room_members SET status = 'approved' WHERE id = ?", (request_id,))
        db.commit()
        socketio.emit('join_approved', {
            'room_code': req['room_code'],
            'room_name': req['room_name']
        }, room=f'user_{req["user_id"]}')
        return jsonify({'ok': True, 'message': '已同意加入'})
    elif action == 'reject':
        db.execute("DELETE FROM room_members WHERE id = ?", (request_id,))
        db.commit()
        socketio.emit('join_rejected', {
            'room_code': req['room_code'],
            'room_name': req['room_name']
        }, room=f'user_{req["user_id"]}')
        return jsonify({'ok': True, 'message': '已拒绝'})
    else:
        return jsonify({'error': '无效操作'}), 400

@app.route('/api/rooms/<room_code>/messages')
@login_required
def api_messages(room_code):
    user = get_current_user()
    db = get_db()
    room = db.execute('SELECT * FROM rooms WHERE code = ?', (room_code,)).fetchone()
    if not room:
        return jsonify({'error': '房间不存在'}), 404
    if not can_access_room(room['id'], user):
        return jsonify({'error': '无权限访问'}), 403
    messages = db.execute('''
        SELECT m.id, m.content, m.msg_type, m.created_at,
               u.id as user_id, u.nickname
        FROM messages m
        JOIN users u ON u.id = m.user_id
        WHERE m.room_id = ?
        ORDER BY m.created_at ASC
        LIMIT 200
    ''', (room['id'],)).fetchall()
    return jsonify([dict(m) for m in messages])

# ============ SocketIO 实时通信 ============

@socketio.on('connect')
def on_connect():
    if 'user_id' in session:
        join_room(f'user_{session["user_id"]}')

@socketio.on('join_room')
def on_join_room(data):
    if 'user_id' not in session:
        return
    code = data.get('room_code')
    db = get_db()
    room = db.execute('SELECT * FROM rooms WHERE code = ?', (code,)).fetchone()
    if not room:
        return
    user = get_current_user()
    if not can_access_room(room['id'], user):
        return
    join_room(f'room_{room["id"]}')
    online_users[user['id']] = room['id']
    socketio.emit('online_update', {'user_id': user['id'], 'online': True}, room=f'room_{room["id"]}')

@socketio.on('disconnect')
def on_disconnect():
    if 'user_id' in session:
        uid = session['user_id']
        rid = online_users.pop(uid, None)
        if rid:
            socketio.emit('online_update', {'user_id': uid, 'online': False}, room=f'room_{rid}')

@socketio.on('send_message')
def on_send_message(data):
    if 'user_id' not in session:
        return
    code = data.get('room_code')
    content = data.get('content', '').strip()
    msg_type = data.get('msg_type', 'text')
    if not content:
        return

    db = get_db()
    room = db.execute('SELECT * FROM rooms WHERE code = ?', (code,)).fetchone()
    if not room:
        return
    user = get_current_user()
    if not can_access_room(room['id'], user):
        return

    # 禁言检查
    muted, mute_until = is_muted(user)
    if muted:
        emit('send_blocked', {'reason': '你已被禁言，无法发送消息'}, room=request.sid)
        return

    now = datetime.now().isoformat()
    db.execute(
        'INSERT INTO messages (room_id, user_id, content, msg_type, created_at) VALUES (?, ?, ?, ?, ?)',
        (room['id'], user['id'], content, msg_type, now)
    )
    db.commit()

    emit('new_message', {
        'content': content,
        'msg_type': msg_type,
        'nickname': user['nickname'],
        'user_id': user['id'],
        'created_at': now
    }, room=f'room_{room["id"]}')

@socketio.on('owner_connect')
def on_owner_connect():
    if 'user_id' in session:
        join_room(f'owner_{session["user_id"]}')

# ============ 启动 ============

if __name__ == '__main__':
    init_db()
    print('=' * 50)
    print('  聊天室服务器已启动')
    print('  访问地址: http://127.0.0.1:5000')
    print('  管理员账号: 10000000000 / admin123')
    print('=' * 50)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)