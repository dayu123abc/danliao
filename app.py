import os
import shutil
import time
import sqlite3
import base64
import hashlib
import random
import secrets
import string
import tempfile
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24).hex()
app.config['DATABASE'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chatroom.db')
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
PROJECT_ROOT = Path(__file__).resolve().parent
EDITOR_EXTENSIONS = {'.py', '.js', '.css', '.html', '.md', '.txt', '.json', '.toml', '.yml', '.yaml', '.bat', '.spec'}
EDITOR_IGNORED_DIRS = {
    '.git', '.venv', '__pycache__', 'uploads', 'instance', 'node_modules',
    'dist', 'build', '.pytest_cache', '.mypy_cache',
    '.file_versions',            # ★ 版本归档隔离，不进文件树、不可编辑
}
EDITOR_PROTECTED_DELETE = {'app.py', 'requirements.txt', 'templates/code_editor.html', 'static/code_editor.js', 'static/code_editor.css'}
EDITOR_MAX_FILE_SIZE = 1024 * 1024
EDITOR_MAX_VERSIONS = 100       # ★ 每个文件最多保留的版本数

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
        CREATE TABLE IF NOT EXISTS file_versions (          -- ★ 回档索引
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            version_file TEXT NOT NULL,
            author_id INTEGER,
            message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_file_versions_path ON file_versions(path);
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

    # ★ 版本归档根目录，与源码树完全隔离
    os.makedirs(PROJECT_ROOT.joinpath('.file_versions'), exist_ok=True)

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

def resolve_editor_path(relative_path, require_existing=True):
    if not isinstance(relative_path, str) or not relative_path or '\\' in relative_path:
        raise ValueError('无效的文件路径')
    parts = relative_path.split('/')
    if any(not part or part in {'.', '..'} or part.startswith('.') for part in parts):
        raise ValueError('无效的文件路径')
    if any(part in EDITOR_IGNORED_DIRS for part in parts[:-1]):
        raise ValueError('该目录不允许通过编辑器访问')
    if Path(parts[-1]).suffix.lower() not in EDITOR_EXTENSIONS:
        raise ValueError('该文件类型不允许通过编辑器访问')

    target = PROJECT_ROOT.joinpath(*parts)
    current = PROJECT_ROOT
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('不允许访问符号链接')
    try:
        target.resolve(strict=False).relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError('文件路径超出项目目录') from exc

    if require_existing and not target.is_file():
        raise FileNotFoundError('文件不存在')
    if not require_existing and not target.parent.is_dir():
        raise FileNotFoundError('目标文件夹不存在')
    return target

def editor_file_hash(content):
    return hashlib.sha256(content).hexdigest()

def editor_csrf_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        expected = session.get('editor_csrf_token', '')
        supplied = request.headers.get('X-CSRF-Token', '')
        if not expected or not secrets.compare_digest(expected, supplied):
            return jsonify({'error': '安全校验失败，请刷新页面后重试'}), 403
        return f(*args, **kwargs)
    return decorated

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

# ============ ★ 回档：每版一个独立文件 ============

def _version_dir(relative_path):
    """项目内路径 → .file_versions/ 下的镜像目录，并校验不越界。"""
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError('无效的文件路径')
    parts = relative_path.replace('\\', '/').split('/')
    if any(not part or part in {'.', '..'} for part in parts):
        raise ValueError('无效的文件路径')
    if Path(parts[-1]).suffix.lower() not in EDITOR_EXTENSIONS:
        raise ValueError('该文件类型不支持版本存档')
    # 归档只按路径镜像，不再重复校验源码白名单，但必须落在归档根内
    target = PROJECT_ROOT.joinpath('.file_versions', *parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.resolve(strict=False).is_relative_to(PROJECT_ROOT.joinpath('.file_versions')):
        raise ValueError('版本路径超出归档目录')
    return target.parent

def _sanitize_message(value):
    return '' if not isinstance(value, str) else value.strip()[:200]

def _trim_versions(relative_path):
    """每个文件只留最近 N 版，旧的连文件带索引一起清。"""
    db = get_db()
    rows = db.execute(
        'SELECT id, version_file FROM file_versions WHERE path=? ORDER BY id DESC',
        (relative_path,)
    ).fetchall()
    if len(rows) <= EDITOR_MAX_VERSIONS:
        return
    for r in rows[EDITOR_MAX_VERSIONS:]:
        try:
            p = Path(r['version_file'])
            if p.is_file():
                p.unlink()
        except OSError:
            pass
    db.execute('DELETE FROM file_versions WHERE path=? AND id NOT IN '
               '(SELECT id FROM file_versions WHERE path=? ORDER BY id DESC LIMIT ?)',
               (relative_path, relative_path, EDITOR_MAX_VERSIONS))
    db.commit()

def _do_snapshot(relative_path, content, message=''):
    """把当前内容存成一个独立版本文件。失败只抛异常，由调用方吞掉。"""
    if not isinstance(content, str):
        raise ValueError('版本内容无效')
    if len(content.encode('utf-8')) > EDITOR_MAX_FILE_SIZE:
        raise ValueError('文件超过1MB，不生成版本快照')
    resolve_editor_path(relative_path)          # 源文件必须真实存在
    vdir = _version_dir(relative_path)
    ts = time.strftime('%Y%m%d_%H%M%S')
    seq = len([f for f in vdir.iterdir() if f.is_file()]) + 1
    ext = Path(relative_path).suffix or '.bak'
    version_file = vdir.joinpath(f'{ts}_{seq:03d}{ext}')
    fd, tmp = tempfile.mkstemp(prefix='.ver-', suffix='.tmp', dir=vdir)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, version_file)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    db = get_db()
    db.execute(
        'INSERT INTO file_versions(path, version_file, author_id, message, created_at) VALUES (?,?,?,?,?)',
        (relative_path, str(version_file), session.get('user_id'), message,
         datetime.now().isoformat(timespec='seconds'))
    )
    db.commit()
    _trim_versions(relative_path)

@app.route('/api/admin/editor/file/versions', methods=['POST'])
@login_required
@admin_required
@editor_csrf_required
def api_editor_version_snapshot():
    """保存成功后再留档时走这里（也允许手动点保存说明时补一版）。"""
    data = request.get_json(silent=True) or {}
    try:
        _do_snapshot(data.get('path'), data.get('content'), _sanitize_message(data.get('message')))
    except (ValueError, FileNotFoundError) as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception:
        return jsonify({'error': '版本存档写入失败'}), 500
    return jsonify({'ok': True})

@app.route('/api/admin/editor/file/versions', methods=['GET'])
@login_required
@admin_required
def api_editor_version_list():
    """列出某文件的版本索引，不读文件内容。"""
    path_arg = request.args.get('path', '')
    try:
        _version_dir(path_arg)                      # 仅校验合法性
        normalized = Path(path_arg).as_posix()
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    rows = get_db().execute(
        'SELECT id, message, author_id, created_at FROM file_versions '
        'WHERE path=? ORDER BY id DESC LIMIT 200', (normalized,)
    ).fetchall()
    return jsonify({'ok': True, 'list': [dict(r) for r in rows]})

@app.route('/api/admin/editor/file/versions/<int:vid>', methods=['GET'])
@login_required
@admin_required
def api_editor_version_get(vid):
    """读取某一版全文，供 diff / 恢复预览。"""
    path_arg = request.args.get('path', '')
    normalized = Path(path_arg).as_posix()
    try:
        _version_dir(path_arg)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    row = get_db().execute(
        'SELECT version_file FROM file_versions WHERE id=? AND path=?',
        (vid, normalized)
    ).fetchone()
    if not row:
        return jsonify({'error': '版本不存在或已被清理'}), 404
    vf = Path(row['version_file'])
    if not vf.is_file() or not vf.resolve(strict=False).is_relative_to(PROJECT_ROOT.joinpath('.file_versions')):
        return jsonify({'error': '版本文件已损坏'}), 404
    try:
        content = vf.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return jsonify({'error': '该版本不是有效的 UTF-8 文本'}), 415
    except OSError:
        return jsonify({'error': '版本文件读取失败'}), 500
    return jsonify({'ok': True, 'content': content})

@app.route('/api/admin/editor/file/versions', methods=['DELETE'])
@login_required
@admin_required
@editor_csrf_required
def api_editor_version_clean():
    """删源文件时，把它的全部版本归档一起清掉。"""
    path_arg = request.args.get('path', '')
    try:
        vdir = _version_dir(path_arg)
        normalized = Path(path_arg).as_posix()
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    db = get_db()
    for r in db.execute('SELECT version_file FROM file_versions WHERE path=?', (normalized,)).fetchall():
        try:
            p = Path(r['version_file'])
            if p.is_file():
                p.unlink()
        except OSError:
            pass
    db.execute('DELETE FROM file_versions WHERE path=?', (normalized,))
    db.commit()
    if vdir.is_dir():
        try:
            vdir.rmdir()      # 空了才删，非空说明有残留，留着无害
        except OSError:
            pass
    return jsonify({'ok': True})

# ============ ★ 自动生成 .gitignore（不新建任何编辑器文件） ============

_GITIGNORE_LINES = [
    ".file_versions/",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    "chatroom.db",
    "chatroom.db-journal",
    "*.log",
    "static/uploads/",
    "dist/",
    "build/",
    ".vscode/",
    ".idea/",
    ".DS_Store",
    "Thumbs.db",
]

def _ensure_gitignore():
    """启动时把规则直接写成真正的 .gitignore，不依赖编辑器建文件。"""
    path = PROJECT_ROOT.joinpath('.gitignore')
    if path.is_file():
        return
    try:
        fd, tmp = tempfile.mkstemp(prefix='.gi-', suffix='.tmp', dir=PROJECT_ROOT)
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as f:
            f.write("\n".join(_GITIGNORE_LINES) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        print("=> 已生成 .gitignore")
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass

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

@app.route('/admin/editor')
@login_required
@admin_required
def code_editor_page():
    csrf_token = session.setdefault('editor_csrf_token', secrets.token_urlsafe(32))
    return render_template('code_editor.html', csrf_token=csrf_token)

@app.route('/api/admin/editor/files')
@login_required
@admin_required
def api_editor_files():
    files = []
    for current_root, dirnames, filenames in os.walk(PROJECT_ROOT, followlinks=False):
        current_path = Path(current_root)
        dirnames[:] = [
            name for name in dirnames
            if name not in EDITOR_IGNORED_DIRS and not name.startswith('.')
            and not (current_path / name).is_symlink()
        ]
        for filename in filenames:
            relative_path = (current_path / filename).relative_to(PROJECT_ROOT).as_posix()
            try:
                path = resolve_editor_path(relative_path)
                size = path.stat().st_size
            except (ValueError, FileNotFoundError, OSError):
                continue
            files.append({'path': relative_path, 'name': filename, 'size': size})
    files.sort(key=lambda item: item['path'].lower())
    return jsonify(files)

@app.route('/api/admin/editor/file')
@login_required
@admin_required
def api_editor_read_file():
    try:
        path = resolve_editor_path(request.args.get('path'))
        content = path.read_bytes()
        if len(content) > EDITOR_MAX_FILE_SIZE:
            return jsonify({'error': '文件超过1MB，无法在网页中编辑'}), 413
        text = content.decode('utf-8')
    except FileNotFoundError:
        return jsonify({'error': '文件不存在'}), 404
    except UnicodeDecodeError:
        return jsonify({'error': '文件不是有效的 UTF-8 文本'}), 415
    except (ValueError, OSError) as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'path': request.args.get('path'), 'content': text, 'hash': editor_file_hash(content)})

@app.route('/api/admin/editor/file', methods=['PUT'])
@login_required
@admin_required
@editor_csrf_required
def api_editor_save_file():
    data = request.get_json(silent=True) or {}
    relative_path = data.get('path')
    content = data.get('content')
    expected_hash = data.get('hash')
    if not isinstance(content, str) or not isinstance(expected_hash, str):
        return jsonify({'error': '保存数据不完整'}), 400
    encoded = content.encode('utf-8')
    if len(encoded) > EDITOR_MAX_FILE_SIZE:
        return jsonify({'error': '文件不能超过1MB'}), 413
    try:
        path = resolve_editor_path(relative_path)
        current = path.read_bytes()
        if editor_file_hash(current) != expected_hash:
            return jsonify({'error': '文件已在其他位置发生变化，请先同步最新内容', 'conflict': True}), 409
        fd, temporary_path = tempfile.mkstemp(prefix='.editor-', suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as temporary_file:
                temporary_file.write(content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            resolve_editor_path(relative_path)
            os.replace(temporary_path, path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
    except FileNotFoundError:
        return jsonify({'error': '文件不存在'}), 404
    except (ValueError, OSError) as exc:
        return jsonify({'error': str(exc)}), 400

    # ★ 先落盘成功，再留一版。留档失败不影响“已保存”这个结果。
    saved_hash = editor_file_hash(encoded)
    try:
        _do_snapshot(relative_path, content, _sanitize_message(data.get('message')))
    except Exception:
        pass
    return jsonify({'ok': True, 'hash': saved_hash, 'saved_at': datetime.now().isoformat(timespec='seconds')})

@app.route('/api/admin/editor/file', methods=['POST'])
@login_required
@admin_required
@editor_csrf_required
def api_editor_create_file():
    data = request.get_json(silent=True) or {}
    relative_path = data.get('path')
    content = data.get('content', '')
    if not isinstance(content, str):
        return jsonify({'error': '文件内容格式无效'}), 400
    encoded = content.encode('utf-8')
    if len(encoded) > EDITOR_MAX_FILE_SIZE:
        return jsonify({'error': '文件不能超过1MB'}), 413
    try:
        path = resolve_editor_path(relative_path, require_existing=False)
        with path.open('x', encoding='utf-8', newline='') as new_file:
            new_file.write(content)
    except FileExistsError:
        return jsonify({'error': '文件已存在'}), 409
    except FileNotFoundError as exc:
        return jsonify({'error': str(exc)}), 404
    except (ValueError, OSError) as exc:
        return jsonify({'error': str(exc)}), 400
    return jsonify({'ok': True, 'hash': editor_file_hash(encoded)})

@app.route('/api/admin/editor/file', methods=['DELETE'])
@login_required
@admin_required
@editor_csrf_required
def api_editor_delete_file():
    data = request.get_json(silent=True) or {}
    relative_path = data.get('path')
    if relative_path in EDITOR_PROTECTED_DELETE:
        return jsonify({'error': '该核心文件不能通过编辑器删除'}), 403
    try:
        path = resolve_editor_path(relative_path)
        path.unlink()
    except FileNotFoundError:
        return jsonify({'error': '文件不存在'}), 404
    except (ValueError, OSError) as exc:
        return jsonify({'error': str(exc)}), 400
    # ★ 源文件删了，版本归档也一并清掉（失败不阻塞删除结果）
    try:
        api_editor_version_clean()
    except Exception:
        pass
    return jsonify({'ok': True})

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
    _ensure_gitignore()          # ★ 第一次启动自动生成 .gitignore，之后不再覆盖
    print('=' * 50)
    print('  聊天室服务器已启动')
    print('  访问地址: http://127.0.0.1:5000')
    print('  管理员账号: 10000000000 / admin123')
    print('=' * 50)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)