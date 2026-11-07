# 聊天室

淡黄色主题、圆润卡片风格的多用户聊天室。

## 功能

- **手机号注册/登录**：一个手机号对应一个账号
- **创建房间**：每人最多创建 5 个聊天室，每个房间自动分配 6 位随机不重复房间号
- **加入房间**：每人最多加入 10 个聊天室
  - 在主页搜索框输入房间号
  - 输入房主设置的密码
  - 房主同意后才能进入
- **实时聊天**：WebSocket 实时收发消息
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

## 项目结构

```
chatroom/
├── app.py              # Flask 服务器 + SocketIO + SQLite
├── requirements.txt    # Python 依赖
├── static/
│   ├── style.css       # 淡黄色主题样式
│   ├── login.js
│   ├── register.js
│   ├── home.js         # 主页逻辑（创建/搜索/审批）
│   └── chat.js         # 聊天室逻辑（实时消息）
└── templates/
    ├── login.html
    ├── register.html
    ├── home.html       # 主页
    └── chat.html       # 聊天室
```

## 技术栈

- **后端**：Python + Flask + Flask-SocketIO
- **数据库**：SQLite（自动创建，无需额外配置）
- **前端**：HTML + CSS + JavaScript + Socket.IO 客户端
