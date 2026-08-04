# Harness Guardrail 使用说明

## 作用

Guardrail 是 `AgentHarness` 的可插拔检查规则。调用方只需要提供规则列表，Harness 会在 Agent 运行前执行输入检查，在 Agent 返回后执行输出检查。规则拒绝数据时统一抛出 `GuardrailViolation`，Harness 再把它包装成带完整 trace 的 `HarnessExecutionError`。

```python
result = AgentHarness(agent, guardrails).run(request)
```

配置错误和运行时违规分开处理：

- `GuardrailConfigurationError`：创建规则时发现参数、Schema、插件类型或依赖无效。此时 Agent 尚未运行。
- `GuardrailViolation`：输入或输出违反规则。Harness 会记录规则名称、检查阶段和原因。

## 五类内置规则

### JSONSchemaValidator

验证选定的请求或响应字段。路径从请求根对象 `task/context` 或响应根对象 `content/metadata` 开始，例如 `metadata.report`。

当前实现支持项目需要的确定性子集：

- `type`、`required`、`properties`、`additionalProperties`；
- `items`、`enum`、`const`；
- `minimum`、`maximum`；
- `minLength`、`maxLength`、`minItems`、`maxItems`。

不支持的 Schema 关键字会在创建规则时直接报错，避免出现“配置看似生效，实际没有校验”的情况。

### SourceAttributionFilter

检查指定记录是否包含来源字段。默认要求 `source` 和 `timestamp`；金融数据应额外配置 `as_of`。

规则只检查配置中明确选定的路径。路径可以指向单条记录，也可以指向记录列表，这样不会把普通包装字段误当成一条外部数据。

### RateLimiter

使用滑动时间窗口限制 Harness 调用次数。当前版本是单进程内存实现，并使用锁保护并发访问，适合本地 Agent 和后续单进程 Graph。

限流器统计“已经执行到该规则的调用尝试”。Guardrail 按配置顺序执行，因此希望先过滤明显无效输入时，应把关键词或 Schema 检查放在限流器之前。

它不是分布式限流器。多进程或多机器共享配额属于后续工程化范围，不能把当前实现宣传成全局限流。

### KeywordBlocker

扫描配置路径下的文本，默认检查输入 `task` 和输出 `content`。它可以递归扫描对象和列表中的字符串，并支持大小写敏感或不敏感匹配。

金融场景用于阻断“绝对稳赚”“100% 收益”等误导性表达，但它不能替代完整的合规审查。

### CrossValidator

把选定的输出交给独立的确定性函数重算或核对。验证函数返回 `bool` 或带说明的 `CrossValidationResult`。

确定性函数通过注册表注入，配置文件只保存验证器名称，不保存无法序列化的 Python 函数。这条 seam 以后可以同时连接本地测试实现和真实指标计算实现。

## 配置注册表

`GuardrailRegistry` 负责把可序列化配置转换成 Guardrail。内置类型包括：

- `json_schema`
- `source_attribution`
- `rate_limiter`
- `keyword_blocker`
- `cross_validator`

```python
registry = GuardrailRegistry.with_builtins(
    cross_validators={"score_formula": validate_score}
)

guardrails = registry.build(
    [
        {
            "type": "json_schema",
            "output_schema": report_schema,
            "output_path": "metadata.report",
        },
        {
            "type": "source_attribution",
            "required_fields": ["source", "timestamp", "as_of"],
            "output_paths": ["metadata.report"],
        },
        {"type": "rate_limiter", "max_calls": 10},
        {
            "type": "keyword_blocker",
            "blocked_keywords": ["绝对稳赚", "100%收益"],
        },
        {
            "type": "cross_validator",
            "validator": "score_formula",
            "output_path": "metadata.report",
        },
    ]
)
```

自定义插件通过 `registry.register(type_name, factory)` 注册。每个规则必须提供唯一名称以及 `check_input()`、`check_output()` 两个方法。

## Trace

每条规则都会留下以下事件：

```text
guardrail.input.started
guardrail.input.passed / guardrail.input.failed
guardrail.output.started
guardrail.output.passed / guardrail.output.failed
```

事件 `detail` 包含规则名称；失败事件同时包含拒绝原因。调用方不需要访问 Harness 内部状态，就能判断哪条规则在哪个阶段阻止了执行。

## 运行和验证

运行离线演示：

```powershell
python Scripts\demo_guardrails.py
```

演示依次显示五类规则全部通过、误导性关键词在输出阶段被拦截，以及超出调用配额后在输入阶段被拦截。它不访问网络、真实模型或交易接口。

运行全部测试：

```powershell
python -m unittest discover -s tests -v
```
