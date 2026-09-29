"""协作取消契约（Ports 层，只依赖标准库）。

**为什么没有"强制终止"**：Python 无法抢占式终止一个正在运行的线程。任何"超时后
强制停止"的说法，只要不依赖被调用方的配合，都是不成立的。因此本模块只提供
**协作取消**原语，并把"谁承诺配合"变成可检查的显式声明：

- :class:`RunCancellation`：一次运行的取消令牌，任何线程可读、可幂等置位；
- :func:`current_cancellation`：当前运行上下文可见的令牌（``ContextVar``，不跨线程继承）；
- :func:`raise_if_cancelled`：底层实现的长阻塞步骤之间的检查点；
- :func:`supports_cooperative_cancellation`：参与者是否**显式声明**遵守该契约。

声明的形式是类属性 ``cancellation_aware = True``。这是显式契约而不是能力探测：
声明者承诺在长阻塞步骤之间检查取消、且不执行忽略取消的无界阻塞调用。声明了却
违反契约的实现会被 Runner 检测并报告为 ``cancellation_unconfirmed``，而不会被静默
接受。

本模块位于 Ports 层且无上层依赖，因此工具、模型 Adapter 与 Agent 都可以引用它，
不产生 tools → agent 的循环依赖。
"""

from __future__ import annotations

from contextvars import ContextVar
from threading import Event

__all__ = [
    "CANCELLATION_AWARE_ATTRIBUTE",
    "OperationCancelledError",
    "RunCancellation",
    "current_cancellation",
    "raise_if_cancelled",
    "supports_cooperative_cancellation",
]

#: 参与者声明"遵守协作取消契约"的类属性名。
CANCELLATION_AWARE_ATTRIBUTE = "cancellation_aware"


class OperationCancelledError(RuntimeError):
    """底层操作观察到协作取消后主动放弃时抛出的受控信号。

    异常文本不含任何外部内容，可安全向上传递。
    """

    def __init__(self) -> None:
        super().__init__("操作已因运行超时被协作取消")


class RunCancellation:
    """一次运行的**协作取消令牌**。

    令牌本身不停止任何东西：它只是让遵守契约的实现能够观察到"应该停止"。置位是
    幂等的，可在任意线程调用。
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        """置位取消：幂等，可重复调用。"""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        """已取消时抛出 :class:`OperationCancelledError`，供底层操作快速退出。"""
        if self._event.is_set():
            raise OperationCancelledError()


#: 当前运行上下文可见的协作取消令牌。``ContextVar`` 不会被新线程继承，因此 Runner
#: 在 worker 内显式 set/reset，保证底层操作一定读到本次运行的令牌。
_CURRENT_CANCELLATION: ContextVar[RunCancellation | None] = ContextVar(
    "tool_loop_cancellation", default=None
)


def current_cancellation() -> RunCancellation | None:
    """返回当前上下文可见的协作取消令牌；不在 Runner worker 内时为 ``None``。"""
    return _CURRENT_CANCELLATION.get()


def raise_if_cancelled() -> None:
    """检查点：当前运行已取消时抛出 :class:`OperationCancelledError`。

    不在 Runner worker 内时是安全的空操作，因此工具与 Adapter 可以无条件调用。
    """
    token = _CURRENT_CANCELLATION.get()
    if token is not None:
        token.raise_if_cancelled()


def set_current_cancellation(token: RunCancellation | None) -> object:
    """设置当前上下文的取消令牌，返回可用于 reset 的 token。"""
    return _CURRENT_CANCELLATION.set(token)


def reset_current_cancellation(reset_token: object) -> None:
    """恢复 :func:`set_current_cancellation` 之前的状态。"""
    _CURRENT_CANCELLATION.reset(reset_token)  # type: ignore[arg-type]


def supports_cooperative_cancellation(participant: object) -> bool:
    """参与者是否**显式声明**遵守协作取消契约。

    只要类上存在 ``cancellation_aware = True`` 即视为已声明（子类默认继承）。没有
    该声明的实现被 Runner 视为"不可控后台工作"，在严格模式下会被拒绝执行。
    """
    return getattr(participant, CANCELLATION_AWARE_ATTRIBUTE, False) is True
