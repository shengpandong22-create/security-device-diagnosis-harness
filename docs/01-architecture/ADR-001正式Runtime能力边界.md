# ADR-001：正式 Runtime 的四域、RAG 与真实 LLM 边界

> 状态：Accepted  
> 日期：2026-09-16  
> 决策原则：可重复评测能力不自动等同于正式服务能力。

## 1. 背景

仓库已经分别具备摄像头黑屏、录像缺失、门禁刷卡异常和报警误报的领域模型、只读工具、
确定性规则与固定评测；也具备 KnowledgeRepository、BGE Adapter、Hybrid Retriever 和低频
真实模型评测。但正式 `build_runtime_container()` 当前只装配一个摄像头资产、摄像头工具、
Static/Routed DeviceGateway 与 FakeLLM，KnowledgeSearchTool 也未注入 Hybrid Retriever。

如果直接把 Phase 能力总表当作正式 Runtime 能力，会把“组件存在”误报为“生产链路可达”。

## 2. 决策

### 2.1 四故障域：暂不默认全部开放

正式 Runtime 默认继续只开放 `camera_black_screen`。录像、门禁和报警保持“独立闭环与固定
评测已完成”，不声明为默认 API 可运行能力。

扩域必须逐域满足以下门禁，而不是扩大 allowlist：

1. 资产目录存在对应设备类型与必要 capability；
2. Adapter Registry 中存在 ready 且通过自检的只读 Adapter；
3. Tool Registry 覆盖该域全部必要工具；
4. 该域 responder/真实 LLM 能返回受控 candidate label；
5. Runtime 级 API、审计、持久化与降级用例通过；
6. Simulator 与真机结果分开标识。

设备到货后的第一目标仍是摄像头真机封版，不为了“看起来支持四域”引入三个没有真实设备
授权与 Adapter 证据的正式入口。

### 2.2 RAG：方向批准，接入暂缓

批准的目标链路是：

```text
confirmed KnowledgeRepository
  -> Keyword Retriever
  -> 可选 BGE Retriever
  -> Hybrid Retriever
  -> KnowledgeSearchTool
```

但在正式 Runtime 接入前，必须先补齐 HttpBgeEmbeddingAdapter 的 endpoint allowlist、输入/响应
大小限制、NaN/Infinity 拒绝、稳定错误映射和 close 生命周期。BGE 不可用时必须显式记录
`keyword_fallback`，不能静默把 keyword 结果冒充 hybrid。满足这些门禁后，RAG 可作为首个正式
扩展项，因为它不扩大设备写权限。

### 2.3 真实 LLM：不进入默认 Runtime

当前 DeepSeek/OpenAI-compatible 客户端只用于显式授权的低频评测，不实现正式 ToolLoopRunner
的 `LLMClient` 运行契约。默认 Runtime 继续使用 FakeLLM，不读取模型环境变量、不产生外部调用。

真实 LLM 正式接入必须另行完成：

1. 独立 LLM Adapter，而不是复用 evaluation client；
2. HTTP 响应字节上限、deadline、供应商 usage/token/cost 预算；
3. Tool Call 与 candidate label 严格 schema；
4. 日志与源码片段白名单、脱敏和逐次授权；
5. 低频 Validation 基线稳定后，仍以配置显式启用，不替换安全默认值。

## 3. 能力表述

| 能力 | 当前状态 | 对外允许表述 |
|---|---|---|
| 摄像头黑屏 | 正式本地 Runtime 可达 | 支持本地 Simulator/静态事实闭环，等待真机封版 |
| 录像/门禁/报警 | 独立闭环与固定评测 | 已完成规则、工具和评测，不是默认 Runtime 能力 |
| Keyword/Vector/Hybrid | 独立检索子系统 | 已完成可重复对照评测，尚未接入正式 Agent Tool |
| DeepSeek 真实模型 | 低频结构化评测 | 已完成受限 Validation 调用，不是正式 Agent Runner |

## 4. 后果

- 优点：正式能力声明与实际装配一致；真机风险面保持最小；不会为面试展示牺牲安全默认值。
- 代价：启动 API 仍不能直接演示四域和真实 RAG/LLM；需要按门禁逐项接入。
- 下一决策点：摄像头真机十轮稳定门禁通过后，优先评审 RAG Adapter 加固与正式注入；其它
  三域按真实资产/Adapter 可得性决定，不按 Phase 编号机械推进。
