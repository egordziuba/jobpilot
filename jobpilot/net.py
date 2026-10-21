"""HTTP через urllib: таймауты, повторы с паузой, единый User-Agent."""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("jobpilot.net")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) JobPilotAuto/1.0 (private job search)"


class FetchError(Exception):
    def __init__(self, url: str, status: int | None, message: str):
        super().__init__(f"{status or ''} {message} — {url}".strip())
        self.url = url
        self.status = status


class Fetcher:
    """Реальный клиент. В тестах подменяется FakeFetcher с тем же интерфейсом."""

    def __init__(self, pause: float = 0.6, retries: int = 3, timeout: float = 30.0):
        self.pause = pause
        self.retries = retries
        self.timeout = timeout
        self._last = 0.0

    def _wait(self) -> None:
        delta = time.monotonic() - self._last
        if delta < self.pause:
            time.sleep(self.pause - delta)
        self._last = time.monotonic()

    def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> tuple[int, bytes]:
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")}, safe=";")
            url = f"{url}?{query}"
        hdrs = {"User-Agent": UA, "Accept": "*/*"}
        hdrs.update(headers or {})
        last_err: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._wait()
            req = urllib.request.Request(url, headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.status, resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code in (404, 410):
                    return exc.code, b""
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    last_err = exc
                    time.sleep(2 ** attempt)
                    continue
                raise FetchError(url, exc.code, str(exc.reason)) from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_err = exc
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue
                raise FetchError(url, None, str(exc)) from exc
        raise FetchError(url, None, str(last_err))

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None):
        status, body = self.get(url, params, headers)
        if status != 200:
            return None
        return json.loads(body.decode("utf-8"))

    def get_text(self, url: str, params: dict | None = None, headers: dict | None = None) -> str | None:
        status, body = self.get(url, params, headers)
        if status != 200:
            return None
        return body.decode("utf-8", errors="replace")

    def post_json(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        hdrs = {"User-Agent": UA, "Content-Type": "application/json"}
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=hdrs, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
