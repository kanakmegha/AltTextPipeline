"""
AI Guardrails & Telemetry
-------------------------
Enforces application-level spending caps, concurrency limits, and tracking.
Prevents runaway spending on cloud inference providers.
"""

import threading
from typing import Optional, Dict, Any
from .config import AIConfig


class BudgetExceededError(Exception):
    """Raised when the configured monthly spending limit has been reached."""
    pass


class BudgetTracker:
    """
    Thread-safe tracker for estimated AI spending and token consumption.
    Enforces a strict application-level spending ceiling (AI_MAX_MONTHLY_SPEND_USD).
    """

    def __init__(self, max_monthly_spend_usd: float = 2.00):
        self.max_monthly_spend_usd = max_monthly_spend_usd
        self._lock = threading.Lock()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_spend_usd = 0.0
        self.request_count = 0

    def check_budget_available(self) -> None:
        """
        Raises BudgetExceededError if cumulative spend has reached or exceeded the ceiling.
        """
        with self._lock:
            if self.total_spend_usd >= self.max_monthly_spend_usd:
                raise BudgetExceededError(
                    f"AI spending limit of ${self.max_monthly_spend_usd:.2f} reached. "
                    f"Current estimated spend is ${self.total_spend_usd:.4f}. "
                    "New generation requests are blocked until budget is increased or reset."
                )

    def record_usage(self, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        """Records token consumption and cost for a completed inference call."""
        with self._lock:
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            self.total_spend_usd = round(self.total_spend_usd + cost_usd, 6)
            self.request_count += 1

    def get_summary(self) -> Dict[str, Any]:
        """Returns spend and token ledger summary."""
        with self._lock:
            remaining = max(0.0, round(self.max_monthly_spend_usd - self.total_spend_usd, 4))
            return {
                "max_monthly_spend_usd": self.max_monthly_spend_usd,
                "total_spend_usd": self.total_spend_usd,
                "remaining_spend_usd": remaining,
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_tokens": self.total_input_tokens + self.total_output_tokens,
                "request_count": self.request_count,
                "is_budget_exceeded": self.total_spend_usd >= self.max_monthly_spend_usd,
            }

    def reset(self) -> None:
        """Resets tracker metrics (primarily used in tests)."""
        with self._lock:
            self.total_input_tokens = 0
            self.total_output_tokens = 0
            self.total_spend_usd = 0.0
            self.request_count = 0


class ConcurrencyLimiter:
    """
    Limits the number of simultaneous active inference requests.
    """

    def __init__(self, max_concurrent: int = 2):
        self.max_concurrent = max(1, max_concurrent)
        self._semaphore = threading.Semaphore(self.max_concurrent)

    def acquire(self, timeout: Optional[float] = None) -> bool:
        return self._semaphore.acquire(timeout=timeout)

    def release(self) -> None:
        self._semaphore.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
