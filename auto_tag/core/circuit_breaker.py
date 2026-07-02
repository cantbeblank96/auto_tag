"""
Circuit Breaker 熔断器。

内存状态，后端重启后重置。线程安全。
"""
from __future__ import annotations

import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Dict, List

logger = logging.getLogger(__name__)


@dataclass
class CircuitBreakerConfig:
    """熔断配置（可从 config.json 读取）。"""
    time_window_seconds: int = 300        # 监控窗口（秒）
    failure_rate_threshold: float = 0.5   # 失败率阈值（0.0 ~ 1.0）
    cooldown_seconds: int = 600           # 熔断停用时长（秒）


@dataclass
class ModelCircuitState:
    """单个模型的熔断运行时状态。"""
    tripped: bool = False
    tripped_until: float = 0.0
    failures: List[float] = field(default_factory=list)
    total_calls: int = 0
    last_error: str = ""


class CircuitBreaker:
    """熔断器：跟踪各模型调用状态，按配置判定是否熔断。"""

    def __init__(self, config: CircuitBreakerConfig | None = None):
        self._config = config or CircuitBreakerConfig()
        self._states: Dict[str, ModelCircuitState] = {}
        self._lock = threading.Lock()

    @property
    def config(self) -> CircuitBreakerConfig:
        return self._config

    def update_config(self, config: CircuitBreakerConfig) -> None:
        with self._lock:
            self._config = config

    def _ensure_state(self, model_name: str) -> ModelCircuitState:
        if model_name not in self._states:
            self._states[model_name] = ModelCircuitState()
        return self._states[model_name]

    def _prune_old(self, state: ModelCircuitState) -> None:
        """移除时间窗口之外的失败记录。"""
        cutoff = time.time() - self._config.time_window_seconds
        state.failures = [t for t in state.failures if t > cutoff]

    def record_success(self, model_name: str) -> None:
        with self._lock:
            state = self._ensure_state(model_name)
            state.total_calls += 1
            state.last_error = ""
            # 成功即退出熔断（半开 → 关闭）
            state.tripped = False
            state.tripped_until = 0.0

    def record_failure(self, model_name: str, error: str = "") -> None:
        with self._lock:
            state = self._ensure_state(model_name)
            state.total_calls += 1
            state.failures.append(time.time())
            state.last_error = error[:200]
            self._prune_old(state)
            # 判断是否触发熔断：至少 min_calls 次调用才启用熔断判定，避免首次调用误判
            min_calls = max(3, int(1 / max(self._config.failure_rate_threshold, 0.01)))
            if state.total_calls >= min_calls:
                rate = len(state.failures) / state.total_calls
                if rate >= self._config.failure_rate_threshold:
                    state.tripped = True
                    state.tripped_until = time.time() + self._config.cooldown_seconds
                    logger.warning(
                        f"Circuit breaker TRIPPED for '{model_name}': "
                        f"failure rate {rate:.2%} >= threshold {self._config.failure_rate_threshold:.0%}"
                    )

    def is_tripped(self, model_name: str) -> bool:
        """检查某模型是否处于熔断状态。若已超过停用时长则自动恢复。"""
        with self._lock:
            state = self._ensure_state(model_name)
            if not state.tripped:
                return False
            if time.time() >= state.tripped_until:
                state.tripped = False
                state.tripped_until = 0.0
                logger.info(f"Circuit breaker reset for '{model_name}': cooldown expired.")
                return False
            return True

    def reset(self, model_name: str) -> None:
        """手动重置某模型的熔断状态。"""
        with self._lock:
            self._states[model_name] = ModelCircuitState()
            logger.info(f"Circuit breaker manually reset for '{model_name}'.")

    def reset_all(self) -> None:
        """重置全部模型熔断状态。"""
        with self._lock:
            self._states.clear()

    def get_state(self, model_name: str) -> ModelCircuitState:
        with self._lock:
            state = self._ensure_state(model_name)
            self._prune_old(state)
            return ModelCircuitState(
                tripped=state.tripped,
                tripped_until=state.tripped_until,
                failures=list(state.failures),
                total_calls=state.total_calls,
                last_error=state.last_error,
            )

    def get_all_states(self) -> Dict[str, dict]:
        with self._lock:
            result = {}
            for name, state in self._states.items():
                self._prune_old(state)
                result[name] = {
                    "tripped": state.tripped,
                    "tripped_until": state.tripped_until,
                    "failures_in_window": len(state.failures),
                    "total_calls": state.total_calls,
                    "failure_rate": round(len(state.failures) / max(state.total_calls, 1), 4),
                    "last_error": state.last_error,
                }
            return result

    def get_config_dict(self) -> dict:
        return {
            "time_window_seconds": self._config.time_window_seconds,
            "failure_rate_threshold": self._config.failure_rate_threshold,
            "cooldown_seconds": self._config.cooldown_seconds,
        }


# 全局单例
_global_circuit_breaker: CircuitBreaker | None = None


def get_circuit_breaker() -> CircuitBreaker:
    global _global_circuit_breaker
    if _global_circuit_breaker is None:
        _global_circuit_breaker = CircuitBreaker()
    return _global_circuit_breaker


def init_circuit_breaker(config: CircuitBreakerConfig) -> CircuitBreaker:
    global _global_circuit_breaker
    _global_circuit_breaker = CircuitBreaker(config)
    return _global_circuit_breaker