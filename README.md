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
