# A–D 团队验收后台

## 1. 目标

这个页面是给开发、答辩和验收人员使用的后台，不是客户分析页面。它把已经完成的 A–D 功能统一成一个可以逐项操作、查看摘要和检查 trace 的工程入口。面向客户的分析成果展示在 `/`，说明见 `docs/client-app.md`。

## 2. 启动

在项目根目录执行：

```powershell
D:\Anaconda\python.exe Scripts\run_dashboard.py
```

浏览器默认打开客户前台 `http://127.0.0.1:8765/`。团队验收后台地址为 `http://127.0.0.1:8765/admin`。如不希望自动打开浏览器，可添加 `--no-browser`；如端口被占用，可添加 `--port 8766`。

安装项目后也可以使用 `agent-platform dashboard`。服务只绑定本机地址，不对局域网或公网开放。

## 3. 后台怎么用

页面左侧是 A → B → C → D 执行轨道：

- A：Harness、Graph、Loop/记忆、Model Gateway 和非金融研究；
- B：金融 Data Hub，以及技术、基本面、行业和宏观四类 Agent；
- C：四 Agent 联合研究、Trader、Risk Manager 和完整金融 Graph；
- D：回测、工业 Harness、量化对照实验、本地模拟交易和最终交付验收。

中间区域显示当前阶段的功能卡。选择“离线复现”时使用项目固定样本，结果稳定，适合答辩；选择“真实数据 / 模型”时，只有标明支持的功能可以运行，可能需要网络、Tushare Token 或 DeepSeek Key。

运行完成后，页面底部先展示核心摘要，再通过“展开完整运行记录”保留原始输出和 trace。客户不需要进入这里；它专门保留工程验收证据。

## 4. DeepSeek 如何接入

推荐直接运行启动命令。当前进程没有 Key 时，终端会隐藏提示输入：

```text
请输入 DeepSeek API Key（输入不显示，直接回车使用固定格式）:
```

输入 Key 后，本次服务的客户智能解读、动态多空辩论和后台助手都会使用 DeepSeek；直接回车则使用本地安全解释和固定辩论。Key 只放在当前进程内存中，关闭服务后失效，不会写入 `.env`、配置文件或仓库。

原来的环境变量方式仍然支持。启动时如果已经检测到 `DEEPSEEK_API_KEY`，会直接复用，不重复询问：

```powershell
$env:DEEPSEEK_API_KEY = "你的 Key"
$env:DEEPSEEK_MODEL = "deepseek-v4-flash"
D:\Anaconda\python.exe Scripts\run_dashboard.py
```

自动化脚本或无人值守启动时，可用 `--no-key-prompt` 跳过询问；没有环境变量时会直接使用固定格式：

```powershell
D:\Anaconda\python.exe Scripts\run_dashboard.py --no-key-prompt
```

如果 Key 已经保存为 Windows 用户环境变量，需要重新打开一个 PowerShell，再从这个新窗口启动控制台。页面右上角显示 `DeepSeek · 模型名`，右侧显示 `LIVE API`，就代表真实助手已启用。

DeepSeek 接收的是用户问题和当前运行结果的有界摘要，通过项目已有 `ModelGateway` 调用；响应必须满足固定 JSON Schema，并记录模型、Token 和耗时。它可以：

- 用通俗中文解释当前结果；
- 根据 A–D 白名单推荐下一项功能；
- 提醒结果边界和答辩重点。

它不能：

- 修改技术指标、财务数值、评分、仓位或风控结论；
- 自动执行推荐的功能；
- 创建订单、调用券商或声称保证收益。

没有 Key 时控制台自动使用本地规则助手，功能按钮和所有离线验收仍然可用。

## 5. 后端边界

前端不能提交任意命令。`DashboardRuntime.run_action()` 只接受代码中登记的 action id，后端再映射到固定脚本和固定参数。真实模式也只能追加每个功能预先登记的只读参数。

所有交易相关结果固定显示：

```text
simulation_only=true
order_created=false
real_trading_allowed=false
```

D4 的“真实模式”表示使用真实只读行情运行本地模拟账本，不表示真实交易。

## 6. 验收

```powershell
D:\Anaconda\python.exe -m unittest tests.test_dashboard -v
```

验收覆盖 A–D 功能登记、脚本存在、命令白名单、真实模式参数、助手只建议不执行、HTTP 页面/API、前端资源和安全字段。浏览器验收还需要实际切换阶段、点击 B1、查看结果摘要、让助手基于结果推荐 C1，并检查桌面和手机宽度没有横向溢出。

如需一次检查客户前台、后台和 A–D 核心交付，运行：

```powershell
D:\Anaconda\python.exe Scripts\demo_product_acceptance.py
```
