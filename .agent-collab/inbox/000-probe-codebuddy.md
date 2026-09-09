# 000：CodeBuddy 连通性探针任务

> 任务类型：只读/报告探针  
> 目标：确认 CodeBuddy 能读取本仓库任务文件，并能把结果写回 `.agent-collab/outbox/`。  
> 禁止：不要修改业务代码、测试、README、docs；不要运行真实模型；不要访问真实设备；不要提交 Git。

请你只做以下事情：

1. 读取本文件；
2. 确认当前仓库名是 `security-device-diagnosis-harness`；
3. 新建或覆盖 `.agent-collab/outbox/000-probe-codebuddy-report.md`；
4. 报告中写入：
   - 你是否能读取任务文件；
   - 当前仓库路径；
   - 当前 git status；
   - 一句话说明你不会修改业务代码。

报告格式：

```markdown
# 000 CodeBuddy 连通性探针报告

- task_file_read: true/false
- repo_path: ...
- git_status: ...
- business_code_modified: false

备注：...
```

