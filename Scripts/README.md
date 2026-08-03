# Scripts

存放自动化校验、报告生成、回放和回测辅助脚本。

脚本应能被命令行重复执行，并在失败时返回明确的非零退出状态。

## Graph 演示

```powershell
python Scripts\demo_graph.py
```

该脚本使用离线数据演示条件分支、节点状态、一次预期故障和 Checkpoint 恢复。默认 Checkpoint 写入 `checkpoints/demo_graph.json`，不会提交到 Git。

可选参数：

- `--route approved|rejected`：选择条件分支；
- `--no-failure`：关闭首次模拟故障；
- `--checkpoint PATH`：指定 Checkpoint 文件位置。
