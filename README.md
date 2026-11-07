# 聊天室

淡黄色主题、圆润卡片风格的多用户聊天室。

## 功能

- **手机号注册/登录**：一个手机号对应一个账号
- **创建房间**：每人最多创建 5 个聊天室，每个房间自动分配 6 位随机不重复房间号
- **加入房间**：每人最多加入 10 个聊天室
  - 在主页搜索框输入房间号
  - 输入房主设置的密码
  - 房主同意后才能进入
- **聊天消息**：浏览器每 3 秒通过 HTTP 轮询新消息，无需 WebSocket 长连接
- **房主审批**：房主可在主页看到待审批列表，同意/拒绝加入申请
- **管理员代码编辑器**：管理员可在管理后台打开“代码编辑器”，编辑项目源码并同步保存到服务器目录；文件变更会自动检测

代码编辑器只允许管理员访问，并限制在项目目录内的文本源码和文档文件。编辑 Python 文件后需要重启服务器进程才能应用代码变更；网页保存不会自动执行或重启代码。单文件 EXE 不包含可供网页修改的原始 Python 项目源码，请使用 `python app.py` 启动的项目目录进行编辑。

## 运行

```bash
cd chatroom
pip install -r requirements.txt
python app.py
```

启动后访问 **http://127.0.0.1:5000**

## 免费平台部署

应用使用普通 HTTP 请求轮询消息和状态，不依赖 WebSocket 长连接，可部署到限制长连接的免费容器。容器休眠后仍可能需要等待平台冷启动；免费实例的本地文件可能随重启或重新部署丢失，因此当前 SQLite 数据库不适合作为需要长期保留数据的生产存储。部署平台提供 `PORT` 环境变量时，应用会自动监听该端口。建议在平台设置长期固定的 `SECRET_KEY` 环境变量，避免服务重启后登录会话失效。

项目包含 `Procfile` 和 `wsgi.py`，支持使用 Gunicorn 启动。若平台需要手动设置启动命令，使用 `gunicorn --bind 0.0.0.0:$PORT wsgi:app`。

## 项目结构

```
chatroom/
├── app.py              # Flask 服务器 + HTTP 轮询 + SQLite
├── requirements.txt    # Python 依赖
├── static/
│   ├── style.css       # 淡黄色主题样式
│   ├── login.js
│   ├── register.js
│   ├── home.js         # 主页逻辑（创建/搜索/审批）
│   └── chat.js         # 聊天室逻辑（HTTP 轮询消息）
└── templates/
    ├── login.html
    ├── register.html
    ├── home.html       # 主页
    └── chat.html       # 聊天室
```

## 技术栈

- **后端**：Python + Flask（HTTP 轮询）
- **数据库**：SQLite（自动创建，无需额外配置）
- **前端**：HTML + CSS + JavaScript
