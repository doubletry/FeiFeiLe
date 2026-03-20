# FeiFeiLe — 飞飞乐

海南航空（HNA）航班特价监控工具。作为海南航空会员，您可以享受 ¥199 等特价机票，
但放票时间不固定。本工具登录您的账号查询指定航线，
一旦发现符合价格条件的航班，立即通过**企业微信应用消息**推送通知。

每次执行完成一轮查询后自动退出，适合配合外部定时器（cron / Windows 任务计划）使用。

> ⚠️ 本项目仅供个人学习与合理使用，查询频率远低于人工操作，不会对航空公司造成损害。
> 请遵守海南航空用户协议及相关法律法规。

## 功能特性

- ✅ 模拟海南航空移动端 App 登录，支持 Token 自动刷新
- ✅ Token 本地持久化，cron 轮次间免重复登录
- ✅ 支持手动导入 Token（解决云服务器 CAPTCHA 验证问题）
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

通过 `-d` 参数指定数据目录，`.env`、`.auth_token.json`、`subscriptions.json` 将统一存放在该目录下：

```bash
# 使用自定义数据目录
poetry run feifeile -d /path/to/data list
poetry run feifeile -d /path/to/data check --dry-run
```

> 默认数据目录为当前工作目录（`.`）。

获取企业微信应用配置的方法：
1. 登录[企业微信管理后台](https://work.weixin.qq.com/)
2. 进入「应用管理」→「自建」→「创建应用」
3. 记录 **AgentId**（即 `WECOM_AGENT_ID`）和 **Secret**（即 `WECOM_SECRET`）
4. 在「我的企业」页面获取 **企业 ID**（即 `WECOM_CORP_ID`）

### 4. 添加订阅

```bash
# 订阅 2025-02-01 海口→北京 (HAK→PEK)，¥199 以下通知
poetry run feifeile add --from HAK --to PEK --date 2025-02-01

# 指定自定义价格阈值（例如 299 元）
poetry run feifeile add -f HAK -t PEK --date 2025-03-15 --price 299

# 将订阅信息添加到指定数据目录
poetry run feifeile add -d /path/to/data -f HAK -t PEK --date 2025-04-01
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
0 */4 * * * cd /path/to/FeiFeiLe && poetry run feifeile -d /path/to/data check
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

### 9. Token 管理（云服务器必看）

在云服务器上运行时，海航可能触发 CAPTCHA 验证（E000167 滑动验证码），
由于服务器没有浏览器，无法直接完成验证。

**解决方案：从浏览器获取登录接口的 Response JSON，一键导入到服务器。**

#### 获取登录 Response JSON

1. 电脑 Chrome 浏览器打开 `https://m.hnair.com`
2. 按 `F12` 打开开发者工具 → 切换到 **Network（网络）** 选项卡
3. 在页面上登录您的海航账号
4. 在 Network 中找到 `login` 请求（URL 包含 `/appum/common/auth/v2/login`）
5. 点击该请求，切换到 **Response（响应）** 选项卡
6. 复制完整的 JSON 响应内容（类似 `{"success":true,"data":{"token":"...","secret":"..."}}`）

#### 导入 Token 到服务器

```bash
# 直接粘贴 Response JSON 作为参数
poetry run feifeile token import '{"success":true,"data":{"ok":true,"token":"eyJ...","secret":"ref...","expireTime":1750000000,"user":{"ucUserId":"123"}}}'

# 或通过管道输入
echo '{...}' | poetry run feifeile token import

# 或交互式输入（不带参数时会提示粘贴）
poetry run feifeile token import
```

导入成功后会显示会员 ID、Token 前缀和有效期天数，程序自动解析 access token、refresh token 等所有字段，无需手动填写。

#### 查看 / 清除 Token

```bash
# 查看当前 Token 状态
poetry run feifeile token show

# 清除已保存的 Token
poetry run feifeile token clear
```

#### 工作原理

- 首次成功登录或 Token 导入后，Token 会持久化到数据目录下的 `.auth_token.json` 文件
- 后续 cron 执行时直接使用已保存的 Token，**无需重新登录**
- Token 过期时优先使用 Refresh Token 刷新（不触发 CAPTCHA）
- 只有 Refresh Token 也失效时才需重新登录
- 如触发 CAPTCHA，程序会通过企业微信通知您重新导入 Token

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

1. **登录安全**：密码存储在数据目录的 `.env` 文件中，请确保文件权限设置正确（`chmod 600 .env`）。
2. **Token 持久化**：登录成功后 Token 保存在数据目录的 `.auth_token.json` 中，后续执行自动复用，过期时通过 Refresh Token 刷新。请确保该文件权限正确（`chmod 600 .auth_token.json`）。
3. **CAPTCHA 验证**：云服务器触发 CAPTCHA 时，程序会通过企业微信通知，请按提示在浏览器获取登录 Response JSON 并通过 `token import` 导入。
4. **数据目录**：使用 `-d` 参数指定数据目录后，`.env`、`.auth_token.json`、`subscriptions.json` 均存放在该目录下，方便统一管理。
5. **连接复用**：同一次执行中，所有订阅共享同一个 HTTP 连接和 Token，不会重复登录。
6. **API 变动**：海南航空 App API 可能随版本更新变化，如遇查询失败请检查 `HNA_BASE_URL` 和 `HNA_APP_VERSION` 配置。
