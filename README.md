# FeiFeiLe — 飞飞乐

海南航空（HNA）航班特价监控工具。作为海南航空会员，您可以享受 ¥199 等特价机票，
但放票时间不固定。本工具每隔 4 小时自动登录您的账号查询指定航线，
一旦发现符合价格条件的航班，立即通过**企业微信**群机器人推送通知。

> ⚠️ 本项目仅供个人学习与合理使用，查询频率远低于人工操作，不会对航空公司造成损害。
> 请遵守海南航空用户协议及相关法律法规。

## 功能特性

- ✅ 模拟海南航空移动端 App 登录，支持 Token 自动刷新
- ✅ 查询普通票价与会员专属特价（¥199 起）
- ✅ 每 4 小时自动轮询（可配置）
- ✅ 企业微信群机器人 Markdown 消息推送
- ✅ 多订阅管理（不同日期、不同航线）
- ✅ 过期订阅自动清理

## 快速开始

### 1. 环境要求

- Python 3.12+
- [Poetry](https://python-poetry.org/)

### 2. 安装依赖

```bash
git clone https://github.com/doubletry/FeiFeiLe.git
cd FeiFeiLe
poetry install
```

### 3. 配置

复制配置模板并填写您的账号信息：

```bash
cp .env.example .env
# 编辑 .env，填写 HNA_USERNAME、HNA_PASSWORD、WECOM_WEBHOOK_URL
```

获取企业微信 Webhook URL 的方法：
1. 打开企业微信，进入目标群聊
2. 点击右上角「...」→「添加群机器人」
3. 添加后复制 Webhook URL

### 4. 添加订阅

```bash
# 订阅 2025-02-01 海口→北京 (HAK→PEK)，¥199 以下通知
poetry run feifeile add --origin HAK --destination PEK --date 2025-02-01

# 指定自定义价格阈值（例如 299 元）
poetry run feifeile add -o HAK -d PEK -D 2025-03-15 --threshold 299
```

### 5. 查看订阅

```bash
poetry run feifeile list
```

### 6. 启动监控

```bash
# 启动后立即执行一次查询，之后每 4 小时重复（推荐）
poetry run feifeile run

# 仅等待定时触发，不立即查询
poetry run feifeile run --no-immediate

# 自定义间隔（例如每 2 小时）
poetry run feifeile run --interval 2
```

### 7. 立即手动检查一次

```bash
poetry run feifeile check
```

### 8. 删除订阅

```bash
poetry run feifeile remove <订阅ID>
```

## 项目结构

```
feifeile/
├── __init__.py
├── auth.py        # HNA 移动端认证（登录 / Token 刷新）
├── cli.py         # Click CLI 入口
├── config.py      # pydantic-settings 配置模型
├── flight.py      # 航班查询与解析
├── monitor.py     # 监控执行器 + 订阅持久化
├── notifier.py    # 企业微信群机器人通知
└── scheduler.py   # APScheduler 定时调度
tests/
├── test_auth.py
├── test_config.py
├── test_flight.py
├── test_monitor.py
├── test_notifier.py
└── test_scheduler.py
```

## 运行测试

```bash
# 运行全部测试
poetry run pytest -v

# 生成覆盖率报告
poetry run pytest --cov=feifeile --cov-report=term-missing
```

## 常见机场三字码

| 城市     | 三字码 |
|----------|--------|
| 海口美兰 | HAK    |
| 三亚凤凰 | SYX    |
| 北京首都 | PEK    |
| 北京大兴 | PKX    |
| 上海虹桥 | SHA    |
| 上海浦东 | PVG    |
| 广州白云 | CAN    |
| 深圳宝安 | SZX    |
| 成都天府 | TFU    |
| 重庆江北 | CKG    |

## 注意事项

1. **登录安全**：密码存储在本地 `.env` 文件中，请确保文件权限设置正确（`chmod 600 .env`）。
2. **Token 缓存**：程序运行期间 Token 保存在内存中，重启后重新登录。
3. **API 变动**：海南航空 App API 可能随版本更新变化，如遇查询失败请检查 `HNA_BASE_URL` 和 `HNA_APP_VERSION` 配置。
4. **频率限制**：默认 4 小时间隔已相当保守，请勿设置过短的间隔。
