# 阶段 0：Codex + CodeBuddy 用量数据可得性

采集时间：2026-09-14。该阶段只读盘点，没有启动 CodeBuddy 或 Codex CLI 实验。

## 1. 额度机制

当前账户面板接口实测为 Plus，存在两个并行总量窗口：300 分钟窗口已用 3%，以及
10080 分钟窗口已用 68%。因此不是单一的单位时间请求频率限制；是否还有服务端未
展示的限制不可得。官方 App Server 文档把这些字段定义为配额窗口使用率、窗口时长
和重置时间。

来源：[Codex App Server：account/rateLimits/read](https://learn.chatgpt.com/zh-Hans/docs/app-server)。

## 2. 单次 token 数据

`codex exec --help` 只声明 `--json` JSONL 事件输出，没有在帮助中承诺 usage 字段。
但是本机 session JSONL 实测存在 `token_count` 事件，包含累计和最近一次调用的
input、cached input、output、reasoning output 与 total tokens。46 个 session 文件中
33 个含用量事件，共 8277 条，所有这些事件都有上述最近一次调用字段。

这些是 Codex 产品会话 token 计数，不等同于可直接换算的货币费用或账户配额扣减；
本阶段没有发现二者之间的官方换算关系。

对照来源：[OpenAI API Response usage 字段](https://developers.openai.com/api/reference/cli/resources/beta/subresources/responses/methods/retrieve)；
API usage 证明 API 响应可提供 token 细分，但不能替代本账户 Codex 配额的产品侧记录。

## 3. 当前编排器字段

现有 run 目录记录 task/run/branch/base/head、状态、退出结果、耗时、轮次、worktree、
HANDOFF/REVIEW 等工程字段，但没有 input/output/cached/reasoning token，也没有稳定的
逐次 Codex purpose/trigger/session 关联表。

## 4. 历史覆盖

- Codex token 事件：2026-07-12 至 2026-09-14；
- 协作 run：11 个，2026-09-10 至 2026-09-14；
- 协作 run 中含 token 字段的文件：0。

两类历史目前没有可靠 task/session join key，因此历史 `cost per accepted task` 不可得。
后续可以从新实验开始采集，不能事后补造关联。

## 阶段 0 结论

token 数据不是完全不可得，阶段 2具备重新设计后测量的基础；但 H1～H3、H5 当前均
不能仅凭现有协作日志判定。原始统计见 `raw/phase0-inventory.json`。
