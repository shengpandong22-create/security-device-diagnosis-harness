# 图解：一次诊断如何完成

- [SVG 成品](./learning-01-diagnosis-loop.svg)
- [Graphviz 语义源](./learning-01-diagnosis-loop.dot)
- [完整源码走读](../06-learning/01-一次诊断如何完成.md)

橙色实线表示主业务路径；紫色节点是 Agent 运行时；绿色节点负责工具与 Evidence；
虚线表示模型建议或失败返回，而不是领域状态写入。最重要的边界是：LLM 不直接修改
Case，失败的工具结果也不会被包装成 Evidence。
