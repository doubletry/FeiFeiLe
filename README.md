# FeiFeiLe — 飞飞乐

海南航空（HNA）航班特价监控工具。作为海南航空会员，您可以享受 ¥199 等特价机票，
但放票时间不固定。本工具登录您的账号查询指定航线，
一旦发现符合价格条件的航班，立即通过**企业微信应用消息**推送通知。

每次执行完成一轮查询后自动退出，适合配合外部定时器（cron / Windows 任务计划）使用。

> ⚠️ 本项目仅供个人学习与合理使用，查询频率远低于人工操作，不会对航空公司造成损害。
> 请遵守海南航空用户协议及相关法律法规。

## 功能特性

- ✅ 模拟海南航空移动端 App 登录，支持 Token 自动刷新
- ✅ 查询普通票价与会员专属特价（¥199 起）
- ✅ 多订阅管理（不同日期、不同航线）
- ✅ 单次登录查询所有订阅（复用 HTTP 连接与 Token）
- ✅ 企业微信应用消息推送（textcard 卡片格式）
- ✅ 过期订阅自动清理
- ✅ 无内置定时，适配外部调度器（cron 等）

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
# 编辑 .env，填写 HNA_USERNAME、HNA_PASSWORD、WECOM_CORP_ID、WECOM_SECRET、WECOM_AGENT_ID
```

获取企业微信应用配置的方法：
1. 登录[企业微信管理后台](https://work.weixin.qq.com/)
2. 进入「应用管理」→「自建」→「创建应用」
3. 记录 **AgentId**（即 `WECOM_AGENT_ID`）和 **Secret**（即 `WECOM_SECRET`）
4. 在「我的企业」页面获取 **企业 ID**（即 `WECOM_CORP_ID`）

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

### 6. 执行一次查询

```bash
# 查询所有订阅并发送通知
poetry run feifeile check

# Dry-run 模式：仅输出结果，不发送微信消息
poetry run feifeile check --dry-run
```

### 7. 配合外部定时器

程序每次执行完一轮查询后自动退出，需配合外部定时器实现定期监控。

**Linux / macOS (cron)**：
```bash
# 每 4 小时执行一次（编辑 crontab: crontab -e）
0 */4 * * * cd /path/to/FeiFeiLe && poetry run feifeile check
```

**Windows 任务计划**：
```powershell
# 创建每 4 小时执行一次的计划任务
schtasks /create /tn "FeiFeiLe" /tr "poetry run feifeile check" /sc hourly /mo 4 /sd (Get-Date -Format yyyy/MM/dd)
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
└── notifier.py    # 企业微信应用消息通知（textcard）
tests/
├── test_auth.py
├── test_config.py
├── test_flight.py
├── test_monitor.py
└── test_notifier.py
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
2. **Token 缓存**：程序运行期间 Token 保存在内存中，每次执行会重新登录。
3. **连接复用**：同一次执行中，所有订阅共享同一个 HTTP 连接和 Token，不会重复登录。
4. **API 变动**：海南航空 App API 可能随版本更新变化，如遇查询失败请检查 `HNA_BASE_URL` 和 `HNA_APP_VERSION` 配置。
