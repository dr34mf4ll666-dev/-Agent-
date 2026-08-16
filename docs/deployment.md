# P8 正式部署、安全和质量门禁

## 1. 当前完成状态

P8 已完成。它没有改变金融分析、确定性指标、仓位和风控逻辑，而是补齐了一个应用正式运行时必须具备的身份、安全、容器和自动质量门禁。

后端由两个深 module 分工：

- `DeploymentRuntime`：统一版本、健康、就绪、配置检查和启动阻断。
- `SecurityRuntime`：统一账户、密码哈希、登录会话、角色、CSRF、限流、审计和会话 DeepSeek Key。

Dashboard 只负责把这两个 module 投影为 HTTP 和页面。金融业务代码不需要自己处理 Cookie、密码、限流或审计文件。

## 2. 用户能直接看到什么

访问 `http://127.0.0.1:8765/` 时，未登录用户会先进入 `/login`。

- 客户账号进入证券分析前台，可以分析股票、查看历史、比较报告和设置自己当前会话的 DeepSeek Key。
- 管理员账号进入 `/admin`，除工程验收功能外，还能看到活动会话数、拒绝事件、限流配置、会话模型状态和最近安全审计。
- 客户账号直接访问 `/admin` 时，后端返回 `403`；这不是只在前端隐藏入口。
- 写操作必须携带当前会话的 CSRF Token。读取、修改和模型调用分别限流。

启动时若没有固定密码，终端会一次性显示本次进程生成的客户和管理员密码。正式使用建议通过环境变量固定：

```powershell
$env:AGENT_PLATFORM_CLIENT_USERNAME="client"
$env:AGENT_PLATFORM_CLIENT_PASSWORD="请换成你的客户密码"
$env:AGENT_PLATFORM_ADMIN_USERNAME="admin"
$env:AGENT_PLATFORM_ADMIN_PASSWORD="请换成你的管理员密码"
D:\Anaconda\python.exe Scripts\run_dashboard.py
```

密码只以 PBKDF2-SHA256 哈希参与校验。DeepSeek Key 可以在启动时作为进程级备用值输入，也可以登录后按会话设置；会话 Key 优先，只在内存中存在，退出登录或服务重启后失效，不写入 SQLite、JSON 台账或审计日志。

## 3. 安全与就绪规则

- 本机模式只允许绑定 `127.0.0.1` 或 `localhost`。
- 容器模式才允许内部绑定 `0.0.0.0`，并且认证必须开启；Compose 对宿主机仍只映射 `127.0.0.1:8765`。
- `ALLOW_LIVE_TRADING=true` 会阻止服务启动；所有报告继续保持 `simulation_only=true`、`order_created=false`、`real_trading_allowed=false`。
- 请求体默认上限 32 KB；单任务默认超时 180 秒；模型调用与 Token 预算必须落在安全范围内。
- 登录默认 5 次/5 分钟，读取默认 120 次/分钟，修改默认 30 次/分钟，模型调用默认 6 次/分钟。
- 登录、登出、越权、CSRF 拒绝、限流、模型 Key 设置/清除和关键操作写入 `.runtime/security/audit.jsonl`；日志达到上限后轮转，只保留安全元数据，不记录密码、Key、Prompt、授权头或完整模型输入输出。
- HTTP 响应保留 CSP、`nosniff`、`DENY`、`no-referrer` 和 `Permissions-Policy`。

状态接口：

| 地址 | 用途 | 正常状态 |
| --- | --- | --- |
| `/api/version` | 应用版本和维护提示 | `200` |
| `/api/health` 或 `/healthz` | 进程存活与交易安全 | `200` |
| `/api/readiness` 或 `/readyz` | 配置和资源是否允许接收请求 | `200` / `503` |

## 4. Docker Compose 运行

项目根目录已提供 `Dockerfile`、`.dockerignore` 和 `compose.yaml`。推荐先设置固定密码，再启动：

```powershell
$env:AGENT_PLATFORM_CLIENT_PASSWORD="请换成你的客户密码"
$env:AGENT_PLATFORM_ADMIN_PASSWORD="请换成你的管理员密码"
docker compose up --build
```

容器具备以下约束：

- 服务使用 UID/GID `10001`，不是 root；
- 根文件系统只读，禁止新增权限；
- `/app/.runtime` 使用命名卷持久化；
- 宿主机端口只绑定 `127.0.0.1`；
- 镜像内不包含 DeepSeek Key；
- 内置 `/healthz` 健康检查。

停止服务可在前台窗口按 `Ctrl+C`。需要删除容器但保留运行数据时可执行 `docker compose down`；不要添加 `--volumes`，除非你明确要删除命名卷中的任务和历史运行数据。

## 5. 一键验收

不启动 Docker也可以先查看完整中文门禁结果：

```powershell
D:\Anaconda\python.exe Scripts\demo_deployment_readiness.py
```

安装后等价命令：

```powershell
agent-platform deployment-check
```

该入口检查 readiness、客户/管理员登录、角色隔离、CSRF、模型限流、会话 Key、审计脱敏、Docker/Compose 契约和 CI 门禁。它本身不联网、不调用 DeepSeek、不生成报告文件。

两条真实端到端验收为：

```powershell
D:\Anaconda\python.exe Scripts\e2e_dashboard.py
D:\Anaconda\python.exe Scripts\verify_container_restart.py --image agent-platform-finance:p8
```

2026-08-16 本机验收结果：真实 Chromium 已通过“未登录跳转、客户前台、客户访问 `/admin` 返回 403、管理员安全界面”；Docker 镜像以 UID `10001` 运行，并通过“提交离线分析任务、停止容器、启动同一容器、原任务成功恢复且真实交易仍关闭”；最终 Compose 配置也在只读根文件系统和持久卷条件下实际启动，`/healthz` 返回 200。

## 6. CI 质量门禁

`.github/workflows/ci.yml` 在 push 和 pull request 时运行：

- Ubuntu 与 Windows 完整测试和稳定 CLI；
- Ruff 代码规范；
- mypy 类型检查；
- 60% 最低分支覆盖率；
- 私钥、GitHub Token 和 DeepSeek Key 模式扫描；
- Python 运行依赖漏洞扫描；
- Playwright Chromium 登录与角色 E2E；
- Docker 镜像构建、非 root 检查和容器重启恢复。

开发审计工具只应安装在隔离的 CI 或开发环境中，不需要常驻日常 Anaconda。运行依赖已将存在安全公告的 `setuptools 78.1.1` 更新并锁定为 `83.0.0`。

## 7. 部署边界

当前 Compose 面向本机使用，不是已经配置好公网域名、TLS、反向代理和集中式身份系统的互联网 SaaS。若以后开放远程访问，需要在现有认证之外增加 HTTPS、可信反向代理、外部密钥管理、备份和部署环境监控。真实交易仍不属于当前交付范围。
