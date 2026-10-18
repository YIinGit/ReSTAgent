"""审稿流水线：分块 → 多轮调用 → 校验定位 → 去重合并。"""
from __future__ import annotations

import concurrent.futures
import json
import math
import re

from . import prompts, quick_checks
from .. import config as config_mod
from .llm import Client, LLMError

STAGE_LABEL = {"zero": "零审", "first": "一审", "second": "二审", "third": "三审"}
LEVEL_ORDER = {"light": 1, "standard": 2, "strict": 3, "max": 4}
SEV_RANK = {"must": 0, "should": 1, "suggest": 2}
CAT_FALLBACK = {"zero": "学术错误", "first": "结构", "second": "病句", "third": "排版"}
QUALITY_MAP = {
    "high": "high", "medium": "medium", "low": "low",
    "高": "high", "中": "medium", "低": "low",
    "高（选用）": "high", "中（需修改并提出意见）": "medium", "低（不选用）": "low",
    "1级": "high", "特策": "high", "特别策划": "high",
    "2级": "medium",
    "3级": "low",
}


# ---------------- 工具 ----------------
def make_chunks(paras, budget: int):
    chunks, cur, n = [], [], 0
    for p in paras:
        if cur and n + len(p.text) > budget:
            chunks.append(cur)
            cur, n = [], 0
        cur.append(p)
        n += len(p.text)
    if cur:
        chunks.append(cur)
    return chunks


def sample_text(doc, budget: int = 11000) -> str:
    """全文抽样：标题全给，正文段截断，用于全局 pass。"""
    if doc.chars <= budget:
        return "\n".join("[%s] %s" % (p.pid, p.text) for p in doc.paras if p.text.strip())
    out, used = [], 0
    for p in doc.paras:
        text = p.text.strip()
        if not text:
            continue
        piece = text if p.level else (text[:150] + ("……" if len(text) > 150 else ""))
        line = "[%s] %s" % (p.pid, piece)
        if used + len(line) > budget:
            out.append("（后续段落略）")
            break
        out.append(line)
        used += len(line)
    return "\n".join(out)


def _shrink(text: str, after: str):
    """把「整句抄一遍只改一处」压到真正改动的最小区间。

    返回 (前缀长度, 最小原片段, 最小新片段)。纯插入时返回 (0, text, after) 保持原样。
    """
    n = min(len(text), len(after))
    i = 0
    while i < n and text[i] == after[i]:
        i += 1
    j = 0
    while j < n - i and text[len(text) - 1 - j] == after[len(after) - 1 - j]:
        j += 1
    q = text[i:len(text) - j] if j else text[i:]
    a = after[i:len(after) - j] if j else after[i:]
    if not q:
        return 0, text, after
    return i, q, a


def extract_json(text: str):
    s = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", s, re.S)
    if m:
        s = m.group(1).strip()
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i:j + 1]
    for candidate in (s, re.sub(r",\s*([}\]])", r"\1", s)):
        try:
            return json.loads(candidate)
        except Exception:
            continue
    raise ValueError("无法解析模型输出为 JSON")


def _norm_pid(pid: str) -> str:
    m = re.search(r"(\d+)", str(pid or ""))
    return "P%03d" % int(m.group(1)) if m else ""


def _sev(v) -> str:
    v = str(v or "").lower()
    return v if v in SEV_RANK else "should"


def _action(v) -> str:
    v = str(v or "").lower()
    return v if v in ("replace", "insert", "delete", "comment_only") else "comment_only"


def _emit(cb, msg, pct=None):
    """兼容只接收 message 的旧回调（测试里用的 lambda m: ...）。"""
    if cb is None:
        return
    try:
        cb(msg, pct)
    except TypeError:
        cb(msg)


class _Steps:
    """把「已处理步数 / 总步数」换算成 0–100 的百分比并回调。"""

    def __init__(self, cb, total: int):
        self.cb = cb
        self.total = max(1, int(total))
        self.done = 0

    def __call__(self, msg):
        # 百分比按「已完成步数」算：这一步刚开始时还没完成，别提前报满
        completed = min(self.total, self.done)
        self.done = min(self.total, self.done + 1)
        pct = int(round(completed * 100.0 / self.total))
        _emit(self.cb, msg, min(95, pct))   # 100% 只由任务真正结束时写入


def _norm_verdict(v):
    """统一零审结论：quality ∈ high/medium/low，兼容旧的 suggested_grade。"""
    if not isinstance(v, dict):
        return None
    out = dict(v)
    raw = out.get("quality", out.get("suggested_grade"))
    grade = ""
    if raw is not None and str(raw).strip():
        txt = str(raw).strip()
        grade = QUALITY_MAP.get(txt.lower()) or QUALITY_MAP.get(txt) \
            or QUALITY_MAP.get(txt.replace(" ", "")) or ""
    out["quality"] = grade if grade in ("high", "medium", "low") else ""
    out.pop("suggested_grade", None)
    return out


# ---------------- 主类 ----------------
class Reviewer:
    def __init__(self, cfg: dict, stage: str, level: str, on_delta=None):
        self.cfg = cfg
        self.stage = stage
        self.level = level
        self.on_delta = on_delta
        self.review_cfg = cfg.get("review", {})
        self.client = Client(cfg)
        self.mock = bool(cfg.get("debug", {}).get("mock")) or not self.client.available()
        self.model = cfg.get("models", {}).get(level) or cfg.get("models", {}).get("standard")
        lv = cfg.get("levels", {}).get(level, {})
        self.temperature = float(lv.get("temperature", 0.2))
        raw_mt = (cfg.get("api") or {}).get("max_tokens", None)
        try:
            # api.max_tokens 显式配置时以它为准（0 = 不传，等于不设上限）；未配置则用档位默认
            self.max_tokens = int(raw_mt) if raw_mt not in (None, "") else int(lv.get("max_tokens", 16384))
        except (TypeError, ValueError):
            self.max_tokens = int(lv.get("max_tokens", 16384))
        self.budget = int(self.review_cfg.get("chunk_chars", 2800))
        self.max_findings = int(self.review_cfg.get("max_findings_per_chunk", 40))
        self.notes: list[str] = []
        self.unmatched = 0
        self.shrunk = 0

    # -------- 对外 --------
    def review_file(self, doc, progress, cancel) -> dict:
        if self.mock:
            return self._mock(doc, progress)
        try:
            if self.stage == "zero":
                return self._zero(doc, progress, cancel)
            if self.stage == "first":
                return self._first(doc, progress, cancel)
            return self._second(doc, progress, cancel)
        except LLMError as e:
            raise LLMError(str(e))

    # -------- 通用调用 --------
    def _call(self, messages, cancel, _retried=False):
        if cancel is not None and cancel.is_set():
            raise LLMError("已取消")
        try:
            return self.client.chat(messages, model=self.model, temperature=self.temperature,
                                    max_tokens=self.max_tokens, cancel=cancel,
                                    stream=bool(self.on_delta), on_delta=self.on_delta)
        except LLMError as e:
            # 被输出上限截断时，放开上限（不传 max_tokens）重试一次
            if not _retried and "截断" in str(e):
                self.notes.append("输出被上限截断，已放开上限重试")
                return self.client.chat(messages, model=self.model,
                                        temperature=self.temperature,
                                        max_tokens=0, cancel=cancel)
            raise

    def _ask(self, messages, cancel, retry_hint=True):
        raw = self._call(messages, cancel)
        try:
            return extract_json(raw)
        except ValueError:
            if not retry_hint:
                raise
            msgs = messages + [{"role": "assistant", "content": raw[:2000]},
                               {"role": "user", "content": "你上一条输出无法解析为 JSON。请**只输出**一个合法 JSON 对象，不要解释、不要代码块。"}]
            raw2 = self._call(msgs, cancel)
            return extract_json(raw2)

    # -------- 零审 --------
    def _zero(self, doc, progress, cancel):
        steps = _Steps(progress, 2 if self.level in ("strict", "max") else 1)
        sample = sample_text(doc, 12000)
        steps("零审：通读全文并做录用判定")
        data = self._ask([
            {"role": "system", "content": prompts.system_prompt("zero", self.level, self.cfg)},
            {"role": "user", "content": prompts.zero_user_message(doc, sample)},
        ], cancel)
        if self.level in ("strict", "max"):
            steps("零审：反向复核（挑漏洞）")
            try:
                data2 = self._ask([
                    {"role": "system", "content": prompts.system_prompt("zero", self.level, self.cfg)},
                    {"role": "user", "content": prompts.zero_user_message(doc, sample) +
                     "\n【第二轮任务】请以「审稿会质疑者」的身份，检查上一步判定可能忽略的问题："
                     "① 是否误判为可录用而实际有硬伤；② 是否有更适合的质量档（high/medium/low）；"
                     "③ 是否漏掉了敏感或体例问题。"
                     "只用 JSON 输出**新增**的 findings 与对 verdict 的修正建议（可用 verdict.reasons 补充）。"},
                ], cancel)
                data = _merge_zero(data, data2)
            except Exception as e:
                self.notes.append("零审第二轮失败：%s" % e)
        return self._finish(doc, findings=data.get("findings") or [],
                            verdict=data.get("verdict"), progress=progress)

    # -------- 一审 --------
    def _outline_first(self, doc, cancel):
        return self._ask([
            {"role": "system", "content": prompts.system_prompt("first", self.level, self.cfg)},
            {"role": "user", "content": prompts.outline_user_message(doc, sample_text(doc), "first")},
        ], cancel)

    def _first(self, doc, progress, cancel):
        paras = [p for p in doc.paras if p.text.strip()]
        chunks = make_chunks(paras, self.budget)
        total = len(chunks) + (0 if self.level == "light" else 1)
        total += 1 if self.level in ("strict", "max") else 0
        steps = _Steps(progress, total)
        all_findings, structures = [], []
        outline_fut, pool = None, None
        if self.level != "light":
            steps("一审：整体结构与逻辑通读")
            # 大纲通读与第 1 块并行发出，缩短首次出活的等待
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            outline_fut = pool.submit(self._outline_first, doc, cancel)
        for i, chunk in enumerate(chunks, 1):
            steps("一审：第 %d/%d 块" % (i, len(chunks)))
            prev_p = paras[paras.index(chunk[0]) - 1] if paras.index(chunk[0]) > 0 else None
            idx = paras.index(chunk[-1])
            next_p = paras[idx + 1] if idx + 1 < len(paras) else None
            try:
                data = self._ask([
                    {"role": "system", "content": prompts.system_prompt("first", self.level, self.cfg)},
                    {"role": "user", "content": prompts.chunk_user_message(doc, chunk, i, len(chunks), prev_p, next_p)},
                ], cancel)
                all_findings += data.get("findings") or []
                structures += data.get("structure_suggestions") or []
            except Exception as e:
                self.notes.append("第 %d 块审读失败：%s" % (i, e))
        if outline_fut is not None:
            try:
                data = outline_fut.result()
                all_findings += data.get("findings") or []
                structures += data.get("structure_suggestions") or []
            except Exception as e:
                self.notes.append("整体通读失败：%s" % e)
            finally:
                pool.shutdown(wait=False)
        if self.level in ("strict", "max"):
            all_findings += self._review_pass(doc, all_findings, steps, cancel)
        return self._finish(doc, findings=all_findings, progress=progress, structures=structures)

    # -------- 二审 --------
    def _second(self, doc, progress, cancel):
        paras = [p for p in doc.paras if p.text.strip()]
        chunks = make_chunks(paras, self.budget)
        all_findings = []
        perspectives = ["语言规范"] if self.level != "max" else ["语言规范", "读者理解"]
        total = len(perspectives) * len(chunks) + (1 if self.level in ("strict", "max") else 0)
        steps = _Steps(progress, total)
        for persp in perspectives:
            for i, chunk in enumerate(chunks, 1):
                steps("二审·%s：第 %d/%d 块" % (persp, i, len(chunks)))
                prev_p = paras[paras.index(chunk[0]) - 1] if paras.index(chunk[0]) > 0 else None
                idx = paras.index(chunk[-1])
                next_p = paras[idx + 1] if idx + 1 < len(paras) else None
                msg = prompts.chunk_user_message(doc, chunk, i, len(chunks), prev_p, next_p)
                if self.level == "max" and persp == "读者理解":
                    msg += ("\n【视角切换】请从「第一次读到这篇稿子的高中生读者」的视角再审一遍："
                            "哪里读不懂、哪里术语没解释、哪句话要读两遍才明白。只报确实影响理解的问题。")
                try:
                    data = self._ask([
                        {"role": "system", "content": prompts.system_prompt("second", self.level, self.cfg)},
                        {"role": "user", "content": msg},
                    ], cancel)
                    all_findings += data.get("findings") or []
                except Exception as e:
                    self.notes.append("第 %d 块审读失败：%s" % (i, e))
        if self.level in ("strict", "max"):
            all_findings += self._review_pass(doc, all_findings, steps, cancel)
        return self._finish(doc, findings=all_findings, progress=progress)

    def _review_pass(self, doc, existing, progress, cancel):
        progress("复核：跨段一致性与漏网问题")
        try:
            data = self._ask([
                {"role": "system", "content": prompts.system_prompt(self.stage, self.level, self.cfg)},
                {"role": "user", "content": prompts.review_user_message(doc, existing, sample_text(doc, 9000))},
            ], cancel)
            return data.get("findings") or []
        except Exception as e:
            self.notes.append("复核轮失败：%s" % e)
            return []

    # -------- 结果整理 --------
    def _finish(self, doc, findings, progress=None, verdict=None, structures=None):
        norm = self._normalize(doc, findings)
        norm = _dedupe(norm)
        for i, f in enumerate(norm, 1):
            f["id"] = "F%03d" % i
            f["status"] = "pending"
            f["final_text"] = None
            f["stage"] = self.stage
            para = doc.para(f["pid"])
            f["context"] = para.text if para else ""
        if self.unmatched:
            self.notes.append("有 %d 条意见因无法定位到原文被丢弃。" % self.unmatched)
        if self.shrunk:
            self.notes.append("其中 %d 条「整句改写」已收窄为最小改动片段。" % self.shrunk)
        return {
            "verdict": _norm_verdict(verdict),
            "findings": norm,
            "structure_suggestions": _clean_list(structures, ("issue", "advice", "severity")),
            "notes": self.notes,
            "usage": dict(self.client.usage),
            "model": self.model,
            "mock": False,
        }

    def _normalize(self, doc, raw_list):
        out = []
        for raw in raw_list or []:
            if not isinstance(raw, dict):
                continue
            pid = _norm_pid(raw.get("para_id") or raw.get("pid"))
            para = doc.para(pid)
            if para is None:
                self.unmatched += 1
                continue
            quote = str(raw.get("quote") or raw.get("before") or "").strip()
            if not quote:
                self.unmatched += 1
                continue
            resolved = self._locate(doc, pid, quote)
            if resolved is None:
                self.unmatched += 1
                continue
            start, end = resolved
            category = str(raw.get("category") or "").strip()
            if not category:
                category = CAT_FALLBACK.get(self.stage, "病句")
            action = _action(raw.get("action"))
            after = str(raw.get("after") or "")
            if action in ("replace", "insert") and not after:
                action = "comment_only"
            # 最小改动原则兜底：模型把整句抄一遍只改一处时，收窄到真正改动的片段
            cur = para.text[start:end]
            if action == "replace" and after and after != cur:
                pre, q2, a2 = _shrink(cur, after)
                if q2 and q2 != cur:
                    start, end, after = start + pre, start + pre + len(q2), a2
                    self.shrunk += 1
            if action in ("replace", "delete", "insert") and start == end and action != "insert":
                action = "comment_only"
            if after and after == para.text[start:end]:
                continue
            out.append({
                "pid": pid,
                "start": start,
                "end": end,
                "quote": para.text[start:end],
                "before": str(raw.get("before") or para.text[start:end]),
                "after": after,
                "action": action,
                "category": category,
                "subcategory": str(raw.get("subcategory") or ""),
                "severity": _sev(raw.get("severity")),
                "problem": str(raw.get("problem") or "").strip(),
                "suggestion": str(raw.get("suggestion") or "").strip(),
                "rule_ref": str(raw.get("rule_ref") or "").strip(),
                "confidence": _clamp(raw.get("confidence")),
                "needs_human_check": bool(raw.get("needs_human_check")),
            })
        return out

    def _locate(self, doc, pid, quote):
        for occ in (0, 1, 2):
            r = doc.resolve(pid, quote, occurrence=occ)
            if r:
                return r
        return None

    # -------- 离线演示（本地规则） --------
    def _mock(self, doc, progress):
        steps = _Steps(progress, 1)
        steps("本地规则预检（未调用模型）")
        raw = quick_checks.run(doc)
        norm = self._normalize(doc, raw)
        norm = _dedupe(norm)
        for i, f in enumerate(norm, 1):
            f["id"] = "F%03d" % i
            f["status"] = "pending"
            f["final_text"] = None
            f["stage"] = self.stage
            para = doc.para(f["pid"])
            f["context"] = para.text if para else ""
        verdict = None
        if self.stage == "zero":
            chars = doc.chars
            quality = "high" if chars >= 5000 else ("medium" if chars >= 4000 else "low")
            verdict = {
                "decision": "revise",
                "summary": "（本地预检模式）稿件共 %d 字，结构完整度需人工判断；质量档按篇幅粗判。" % chars,
                "quality": quality,
                "suggested_section": "待定",
                "word_count_est": chars,
                "reasons": ["本地预检只做文字层扫描，未做立论、难度与属性判断。",
                            "请配置 API Key 后使用完整零审。"],
            }
        self.notes.append("当前为本地规则预检（未配置 API Key 或开启了演示模式），结果仅覆盖硬性文字规则。")
        return {
            "verdict": _norm_verdict(verdict),
            "findings": norm,
            "structure_suggestions": [],
            "notes": self.notes,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "estimated": 0},
            "model": "local-rules",
            "mock": True,
        }


# ---------------- 辅助 ----------------
def _clamp(v):
    try:
        x = float(v)
    except Exception:
        return 0.7
    return max(0.0, min(1.0, x))


def _dedupe(items):
    best = {}
    for f in items:
        key = (f["pid"], f["start"], f["end"], f["category"])
        cur = best.get(key)
        if cur is None or SEV_RANK[f["severity"]] < SEV_RANK[cur["severity"]] or (
                SEV_RANK[f["severity"]] == SEV_RANK[cur["severity"]] and f["confidence"] > cur["confidence"]):
            best[key] = f
    out = sorted(best.values(), key=lambda f: (_pid_num(f["pid"]), f["start"], SEV_RANK[f["severity"]]))
    return out


def _pid_num(pid: str) -> int:
    try:
        return int(pid[1:])
    except Exception:
        return 0


def _clean_list(items, keys):
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        row = {}
        for k in keys:
            v = it.get(k)
            if isinstance(v, list):
                v = "、".join(str(x) for x in v)
            row[k] = str(v) if v is not None else ""
        if any(row.values()):
            out.append(row)
    return out


def _merge_zero(a: dict, b: dict) -> dict:
    out = dict(a or {})
    f = list(a.get("findings") or [])
    f += list((b or {}).get("findings") or [])
    out["findings"] = f
    v = dict(a.get("verdict") or {})
    v2 = (b or {}).get("verdict") or {}
    for k, val in v2.items():
        if val and k not in v:
            v[k] = val
    out["verdict"] = v
    return out


def estimate(cfg: dict, stage: str, level: str, chars: int) -> dict:
    """粗略预估调用轮次与 token，并按设置里的单价折算费用（元）。"""
    cfg = cfg or {}
    budget = int(cfg.get("review", {}).get("chunk_chars", 2800))
    chunks = max(1, math.ceil(chars / max(1, budget)))
    rule_chars = len(prompts.rules(stage, cfg))
    per_call_in = int(chars / chunks * 1.35) + rule_chars + 400
    calls = chunks
    if stage == "zero":
        calls = 1
        per_call_in = min(chars, 12000) + rule_chars + 400
    elif stage == "first":
        calls = chunks + (0 if level == "light" else 1)
    if level in ("strict", "max"):
        calls += 1
    if level == "max" and stage == "second":
        calls += chunks
    out_tokens = calls * int(cfg.get("levels", {}).get(level, {}).get("max_tokens", 4096) * 0.25)
    in_tokens = calls * per_call_in / 1.6
    in_tokens, out_tokens = int(in_tokens), int(out_tokens)
    pin, pout = config_mod.prices(cfg)
    cost = round((in_tokens * pin + out_tokens * pout) / 1000000.0, 4)
    return {"calls": calls, "in_tokens": in_tokens, "out_tokens": out_tokens,
            "cost": cost, "price_in": pin, "price_out": pout, "currency": "CNY"}

