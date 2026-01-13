from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass
from typing import Any, Optional

import requests


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenAICompatClient:
    base_url: str
    api_key: str
    timeout_s: int = 120

    def _url(self, path: str) -> str:
        base = self.base_url.rstrip("/")
        suffix = path if path.startswith("/") else f"/{path}"
        if base.endswith("/v1"):
            return f"{base}{suffix}"
        return f"{base}/v1{suffix}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _raise_http_error(self, resp: requests.Response, err: requests.HTTPError) -> None:
        detail = resp.text
        try:
            detail = json.dumps(resp.json(), ensure_ascii=False)
        except Exception:
            pass
        logger.warning("openai_compat_error status=%s detail=%s", resp.status_code, detail)
        raise requests.HTTPError(f"{err}\nResponse: {detail}", response=resp) from err

    def _extract_max_batch_size(self, resp: requests.Response) -> Optional[int]:
        try:
            data = resp.json()
        except Exception:
            return None
        msg = ((data.get("error") or {}).get("message") or "").strip()
        m = re.search(r"not be larger than\s+(\d+)", msg, flags=re.IGNORECASE)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def _embeddings_request(self, *, model: str, inputs: list[str]) -> list[list[float]]:
        url = self._url("/embeddings")
        payload = {"model": model, "input": inputs}
        resp = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout_s)
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            self._raise_http_error(resp, e)
        data = resp.json()
        items = data.get("data") or []
        items = sorted(items, key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in items]

    def embeddings(self, *, model: str, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []

        try:
            return self._embeddings_request(model=model, inputs=inputs)
        except requests.HTTPError as e:
            resp = getattr(e, "response", None)
            if resp is not None and resp.status_code == 400:
                max_batch = self._extract_max_batch_size(resp)
                if max_batch and max_batch > 0 and len(inputs) > max_batch:
                    out: list[list[float]] = []
                    for start in range(0, len(inputs), max_batch):
                        out.extend(self._embeddings_request(model=model, inputs=inputs[start : start + max_batch]))
                    return out
            raise

    def chat_completions(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
    ) -> str:
        url = self._url("/chat/completions")
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        resp = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout_s)
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            self._raise_http_error(resp, e)
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"empty chat response: {data}")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError(f"unexpected chat response: {data}")
        return content

