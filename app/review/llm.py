"""OpenAI 兼容 Chat Completions 客户端（标准库实现）。"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

CHAT_PATH = "/chat/completions"


class LLMError(Exception):
    pass


class _StreamFallback(Exception):
    """流式解析/参数不被支持时抛出，由 chat() 回退到非流式请求。"""


def normalize_base(url: str) -> str:
    """把用户填的 Base URL 归一成「不含 /chat/completions 的前缀」。

    https://host/v1          → https://host/v1
    https://host/v1/         → https://host/v1
    https://host/v1/chat/completions → https://host/v1
    host/v1                  → https://host/v1
    """
    u = (url or "").strip()
    if not u:
        return ""
    if not u.lower().startswith(("http://", "https://")):
        u = "https://" + u
    u = u.rstrip("/")
    for suffix in (CHAT_PATH, "/completions", "/models"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
            break
    return u.rstrip("/")


class Client:
    def __init__(self, cfg: dict):
        api = cfg.get("api", {})
        self.base_url = normalize_base(api.get("base_url") or "")
        self.api_key = (api.get("api_key") or "").strip()
        self.timeout = int(api.get("timeout") or 240)
        self.max_retries = int(api.get("max_retries") or 2)
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "estimated": 0}
        self.stream_fallback = False

    @property
    def chat_url(self) -> str:
        return self.base_url + CHAT_PATH

    def available(self) -> bool:
        return bool(self.base_url and self.api_key)

    def chat(self, messages: list[dict], model: str, temperature: float = 0.2,
             max_tokens: int = 8192, cancel=None, stream: bool = False,
             on_delta=None) -> str:
        """stream=True 且给了 on_delta 时走增量解析；流式失败自动回退非流式。"""
        if stream and on_delta is not None:
            try:
                return self._chat_stream(messages, model, temperature, max_tokens, cancel, on_delta)
            except _StreamFallback:
                self.stream_fallback = True
        return self._chat_plain(messages, model, temperature, max_tokens, cancel)

    def _chat_plain(self, messages: list[dict], model: str, temperature: float = 0.2,
                    max_tokens: int = 8192, cancel=None) -> str:
        if not self.available():
            raise LLMError("未配置 API Key，请先在「设置」里填写。")
        url = self.chat_url
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens and int(max_tokens) > 0:
            payload["max_tokens"] = int(max_tokens)   # 0 = 不传，交给服务端默认（等于不设上限）
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_err = None
        for attempt in range(self.max_retries + 1):
            if cancel is not None and cancel.is_set():
                raise LLMError("已取消")
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", "Bearer " + self.api_key)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                data = json.loads(raw)
                self._count(data)
                choices = data.get("choices") or []
                if not choices:
                    raise LLMError("模型未返回内容：%s" % raw[:200])
                choice = choices[0] or {}
                msg = choice.get("message") or {}
                content = (msg.get("content") or "").strip()
                reasoning = (msg.get("reasoning_content") or "").strip()
                finish = choice.get("finish_reason") or ""
                if not content:
                    if finish == "length":
                        raise LLMError(
                            "模型输出被上限 %s tokens 截断（推理模型的思考过程也占额度）："
                            "请在设置里把「输出上限」调大或设为 0（不限制），或调小「单块字数」。"
                            % ("max_tokens=" + str(int(max_tokens)) if max_tokens else "服务端默认")
                        )
                    if reasoning:
                        # 纯推理模型：答案落在 reasoning_content 里
                        content = reasoning
                if not content:
                    raise LLMError("模型未返回内容：%s" % raw[:200])
                return content
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                last_err = LLMError(
                    "HTTP %s（POST %s）：%s" % (e.code, url, (detail or e.reason))
                )
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise last_err
            except urllib.error.URLError as e:
                last_err = LLMError("网络错误（POST %s）：%s" % (url, e.reason))
                if attempt < self.max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise last_err
            except json.JSONDecodeError:
                last_err = LLMError("返回内容不是合法 JSON（POST %s）" % url)
                if attempt < self.max_retries:
                    time.sleep(1.2)
                    continue
                raise last_err
        raise last_err or LLMError("调用失败")

    def _chat_stream(self, messages: list[dict], model: str, temperature: float,
                     max_tokens: int, cancel, on_delta) -> str:
        """解析 `data:` 行形式的 SSE 增量；任何解析问题都交给上层回退非流式。"""
        if not self.available():
            raise LLMError("未配置 API Key，请先在「设置」里填写。")
        url = self.chat_url
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if max_tokens and int(max_tokens) > 0:
            payload["max_tokens"] = int(max_tokens)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "text/event-stream")
        req.add_header("Authorization", "Bearer " + self.api_key)
        parts: list[str] = []
        usage = None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for raw in resp:
                    if cancel is not None and cancel.is_set():
                        raise LLMError("已取消")
                    line = raw.decode("utf-8", "replace").strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue          # id: / event: / 空行都跳过
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        raise _StreamFallback("流式数据不是合法 JSON")
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("usage"):
                        usage = obj["usage"]
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0] or {}).get("delta") or {}
                    piece = delta.get("content") or delta.get("reasoning_content") or ""
                    if piece:
                        text = str(piece)
                        parts.append(text)
                        try:
                            on_delta(text)
                        except Exception:  # noqa: BLE001 — 展示回调不能影响审稿
                            pass
        except LLMError:
            raise
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            if e.code in (401, 403):
                raise LLMError("HTTP %s（POST %s）：%s" % (e.code, url, (detail or e.reason)))
            raise _StreamFallback("HTTP %s" % e.code)
        except urllib.error.URLError as e:
            raise _StreamFallback("网络错误：%s" % e.reason)
        except (OSError, ValueError) as e:
            raise _StreamFallback(str(e))

        content = "".join(parts).strip()
        if not content:
            raise _StreamFallback("流式输出为空")
        if usage:
            self._count({"usage": usage})
        else:
            # 服务端没回 usage：按字符数粗估，并在界面标注「估算」
            prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
            self.usage["prompt_tokens"] += int(prompt_chars / 1.6)
            self.usage["completion_tokens"] += int(len(content) / 1.6)
            self.usage["calls"] += 1
            self.usage["estimated"] = int(self.usage.get("estimated") or 0) + 1
        return content

    def _count(self, data: dict):
        u = data.get("usage") or {}
        self.usage["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
        self.usage["completion_tokens"] += int(u.get("completion_tokens") or 0)
        self.usage["calls"] += 1


# ---------------- 连接诊断 ----------------

def _get(url: str, key: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, method="GET")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()[:400].decode("utf-8", "replace")
            return {"url": url, "status": resp.status, "note": body.strip()[:120]}
    except urllib.error.HTTPError as e:
        try:
            body = e.read()[:400].decode("utf-8", "replace").strip()
        except Exception:
            body = str(e.reason)
        return {"url": url, "status": e.code, "note": body[:120]}
    except Exception as e:  # noqa: BLE001
        return {"url": url, "status": 0, "note": str(e)[:120]}


def alternate_bases(base: str) -> list[str]:
    """常见写错的方式：多写/漏写 /v1。"""
    if not base:
        return []
    p = urllib.parse.urlsplit(base)
    origin = "%s://%s" % (p.scheme, p.netloc)
    path = p.path.rstrip("/")
    out = []
    if path.endswith("/v1"):
        out.append(origin + path[:-3])
    elif path == "":
        out.append(origin + "/v1")
    else:
        out.append(origin + path + "/v1")
        out.append(origin + "/v1")
    return [b for b in dict.fromkeys(out) if b and b != base]


def _hint(status: int, base: str, alt_ok: str | None) -> str:
    if status == 0:
        return "连不上服务器：检查网络、代理、域名是否写全（含 https://）。"
    if status == 404:
        msg = ("地址下没有 %s 接口（nginx 直接 404）。请对照服务商文档核对 Base URL，"
               "最常见的是 https://域名/v1。" % CHAT_PATH)
        if alt_ok:
            msg += " 探测到另一个可用前缀：%s，把它填进 Base URL 再试。" % alt_ok
        else:
            msg += " 如果服务商给的是完整接口地址，把结尾的 /chat/completions 去掉后填入。"
        return msg
    if status in (401, 403):
        return "鉴权失败：API Key 不正确、已过期，或该 Key 无权访问这个模型。"
    if status in (400, 422):
        return "请求被拒绝：多半是模型名不存在或参数不支持，请核对「各力度使用的模型」。"
    if status == 429:
        return "被限流：稍后再试，或降低「单块字数」减少调用次数。"
    if status >= 500:
        return "服务端错误（%d）：换个时间段再试。" % status
    return ""


def probe(cfg: dict, model: str) -> dict:
    """测试连接：把每一步的 URL 与状态码都列出来，失败时给可执行的建议。"""
    client = Client(cfg)
    base = client.base_url
    result = {
        "ok": False,
        "base": base,
        "chat_url": client.chat_url,
        "model": model,
        "attempts": [],
        "hint": "",
    }
    if not base:
        result["hint"] = "没填 API Base URL。"
        return result
    if not client.api_key:
        result["hint"] = "没填 API Key。"
        return result

    result["attempts"].append(dict(name="GET /models", **_get(base + "/models", client.api_key)))

    t0 = time.time()
    try:
        reply = client.chat(
            [{"role": "user", "content": "只回复两个字：正常"}], model=model, max_tokens=16
        )
        result.update(ok=True, ms=int((time.time() - t0) * 1000), reply=reply.strip()[:40])
    except LLMError as e:
        text = str(e)
        result.update(ms=int((time.time() - t0) * 1000), error=text)
        code = 0
        for token in text.split("HTTP ", 1)[1:]:
            digits = ""
            for ch in token:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            code = int(digits or 0)
            break
        result["status"] = code
    except Exception as e:  # noqa: BLE001
        result.update(error=str(e), status=0)

    if not result["ok"]:
        alt_ok = None
        for alt in alternate_bases(base):
            r = _get(alt + "/models", client.api_key, timeout=8)
            r["name"] = "备选 GET /models"
            result["attempts"].append(r)
            if r["status"] in (200, 401, 403) and alt_ok is None:
                alt_ok = alt
        result["hint"] = _hint(int(result.get("status") or 0), base, alt_ok)
        result["alt_ok"] = alt_ok
    return result

