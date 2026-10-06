"""
src/client.py
Resilient HTTP Client with 5x Circuit Breaker, Exponential Backoff, and Retry-After Compliance.
"""
import time
from typing import Optional, Tuple
from curl_cffi import requests

class CircuitBreakerTripped(Exception):
    """Raised when 5 consecutive 403 or 429 errors occur."""
    pass

class CricinfoClient:
    def __init__(self, breaker_threshold: int = 5):
        self.session = requests.Session()
        self.breaker_threshold = breaker_threshold
        self.consecutive_blocked = 0

    def get(
        self,
        url: str,
        headers: Optional[dict] = None,
        max_retries: int = 3,
        timeout: int = 15,
        delay_sec: float = 0.5
    ) -> Tuple[Optional[requests.Response], Optional[str]]:
        """
        Execute GET request with exponential backoff and circuit breaker tracking.
        Returns: (Response, error_message)
        """
        if self.consecutive_blocked >= self.breaker_threshold:
            raise CircuitBreakerTripped(
                f"Circuit breaker tripped: {self.consecutive_blocked} consecutive 403/429 errors."
            )

        # Inter-request polite jitter
        if delay_sec > 0:
            time.sleep(delay_sec)

        for attempt in range(max_retries):
            try:
                r = self.session.get(
                    url,
                    headers=headers,
                    impersonate="chrome124",
                    timeout=timeout
                )

                # Check for rate-limiting or anti-bot blocks
                if r.status_code in (403, 429):
                    self.consecutive_blocked += 1
                    if self.consecutive_blocked >= self.breaker_threshold:
                        raise CircuitBreakerTripped(
                            f"Circuit breaker tripped after {self.consecutive_blocked} consecutive blocked requests."
                        )

                    retry_after = r.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        sleep_time = float(retry_after)
                    else:
                        sleep_time = (2 ** attempt) * 1.5

                    time.sleep(sleep_time)
                    continue

                if r.status_code == 200:
                    self.consecutive_blocked = 0
                    return r, None

                if r.status_code in (500, 502, 503, 504):
                    time.sleep((2 ** attempt) * 1.0)
                    continue

                return r, f"HTTP {r.status_code}"

            except CircuitBreakerTripped:
                raise
            except Exception as e:
                if attempt == max_retries - 1:
                    return None, str(e)
                time.sleep((2 ** attempt) * 1.0)

        return None, "Max retries exceeded"
