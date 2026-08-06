# 统一 Model Gateway

## 它解决什么问题

Loop 和 Graph 不应该分别学习每家模型供应商的参数、响应字段和异常类型。`ModelGateway` 把这些差异收在一个边界里：上层提交同一种 `ModelRequest`，成功时收到同一种 `ModelResponse`，失败时收到带 trace 的 `ModelGatewayExecutionError`。

当前有三个适配器：

- `MockModelAdapter`：结果完全确定，用于离线测试、演示和故障注入；
- `OpenAIResponsesAdapter`：调用 OpenAI Responses API；
- `DeepSeekChatAdapter`：调用 DeepSeek Chat Completions，使用 DeepSeek Key 完成当前项目的真实验证。

这些实现证明了适配器接口是实际替换边界，而不是为了未来想象出来的抽象。

## 调用流程

```text
ModelRequest
    ↓
ModelGateway：校验配置、记录 trace、控制重试
    ↓
ModelAdapter：Mock 或 OpenAI
    ↓
ModelAdapterResponse
    ↓
本地 JSON Schema 复核
    ↓
ModelResponse + ModelTraceEvent[]
```

一次成功结果记录：供应商、实际模型名、input/output/total token、总耗时、尝试次数、响应 ID、结构化结果和有序 trace。失败结果通过异常保留错误类别、是否可重试、尝试次数、原始原因和失败前 trace。

## 结构化输出

调用方把 JSON Schema 放入 `ModelRequest.response_schema`。OpenAI 适配器会把它转换为 Responses API 的 `text.format` 严格 JSON Schema。DeepSeek 适配器使用 `response_format={"type":"json_object"}`，同时把 Schema 写进 system prompt；DeepSeek 只保证合法 JSON，因此真正的字段、类型和取值范围仍由 Gateway 的本地 Schema 复核。

远端约束负责提高模型输出的格式稳定性，本地复核负责守住平台自己的数据边界。二者不是重复：远端约束可能因为模型、供应商或接口变化而改变，本地业务代码不能因此跳过验证。

## 超时、重试和错误

`ModelRetryPolicy` 同时限制每次 I/O 超时和总尝试次数。当前退避公式为：

```text
delay = initial_backoff_seconds × backoff_multiplier^(attempt - 1)
```

只有临时错误会重试，例如超时、连接故障、普通限流和服务端 5xx。认证失败、权限不足、余额或额度耗尽、无效请求、拒答以及响应结构错误不会重试。这样可以避免用重复请求掩盖配置问题或继续消耗额度。

当前超时是“传给适配器网络 I/O 的单次超时”，不是强制终止任意 Python 适配器代码的硬超时。OpenAI 标准库 HTTP 实现会使用这个超时；测试中的自定义适配器仍需要遵守接口约定。

## 密钥边界

OpenAI 适配器只读取进程环境中的 `OPENAI_API_KEY`，DeepSeek 适配器只读取 `DEEPSEEK_API_KEY`。仓库忽略 `.env`，代码、测试、演示输出和 trace 都不会记录密钥。OpenAI 请求还显式设置 `store: false`。

不要把真实密钥写入 `.env.example`、命令脚本、测试 fixture 或 Git 提交。PowerShell 中可以临时、安全地输入：

```powershell
$secureKey = Read-Host "DEEPSEEK_API_KEY" -AsSecureString
$env:DEEPSEEK_API_KEY = [System.Net.NetworkCredential]::new("", $secureKey).Password
python Scripts\demo_model_gateway.py --live --provider deepseek --model deepseek-v4-flash
Remove-Item Env:DEEPSEEK_API_KEY
```

DeepSeek 模型名可以通过 `--model` 或 `DEEPSEEK_MODEL` 改写。项目当前默认 `deepseek-v4-flash`；旧的 `deepseek-chat` 和 `deepseek-reasoner` 已过官方弃用日期，不作为默认值。若要验证 OpenAI，可执行 `--live --provider openai` 并设置 `OPENAI_API_KEY`。

## 验证命令

离线演示：

```powershell
python Scripts\demo_model_gateway.py
```

A4 定向测试：

```powershell
python -m unittest tests.test_model_gateway tests.test_openai_adapter tests.test_demo_model_gateway -v
```

完整回归：

```powershell
python -m unittest discover -s tests -v
```

真实验证不是单元测试的一部分。A4 已在 2026-08-06 使用 DeepSeek 完成受控真实验证：`deepseek-v4-flash` 一次调用成功，response ID 为 `b78413b7-fb38-4621-ac51-4ba3eb0925e2`，input/output/total token 为 116/12/128，耗时 1063ms，结构化结果为 `mode: live`。该记录不包含 API Key 或原始响应缓存。

演示会根据运行方式收紧 Schema：离线模式只接受 `mode: offline`，真实模式只接受 `mode: live`。这不仅检查 JSON 格式，也防止模型返回与本次运行事实矛盾的状态。
