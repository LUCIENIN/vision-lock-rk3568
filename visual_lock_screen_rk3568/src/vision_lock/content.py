from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import time
from pathlib import Path
from typing import List, Optional

import requests

from .config import ContentConfig


@dataclass
class LockContent:
    title: str
    subtitle: str
    lines: List[str]


def build_demo_lines() -> List[str]:
    return [
        "天气：小雨转阴，26℃ ~ 28℃，风力3级",
        "运势：今日宜静不宜躁，适合处理清单类任务",
        "待办：1）更新锁屏配置 2）检查摄像头画面",
        "新闻：今日无更新，可在后续版本接入本地新闻源",
    ]


def local_lines() -> List[str]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return [
        f"当前时间：{now}",
        "系统：RK3568 视觉识别锁屏服务已就绪",
        "提示：有人体经过时将自动触发锁屏",
    ]


def load_cached(content_cfg: ContentConfig) -> List[str]:
    path = Path(content_cfg.cache_file)
    if not path.exists():
        return local_lines() + build_demo_lines()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        lines = payload.get("lines")
        if isinstance(lines, list) and lines:
            return lines
        return local_lines() + build_demo_lines()
    except Exception:
        return local_lines() + build_demo_lines()


def _parse_api_payload(payload) -> Optional[List[str]]:
    if isinstance(payload, list) and payload:
        parsed: List[str] = []
        for item in payload:
            if isinstance(item, str):
                value = item.strip()
                if value:
                    parsed.append(_normalize_line(value))
            elif isinstance(item, dict):
                if "title" in item:
                    parsed.append(_normalize_line(str(item.get("title", ""))))
                if "body" in item and item["body"]:
                    parsed.append(_normalize_line(str(item["body"])))
                elif "content" in item and item["content"]:
                    parsed.append(_normalize_line(str(item["content"])))
                elif not parsed and payload:
                    parsed.append(_normalize_line(str(item)))
            else:
                value = str(item).strip()
                if value:
                    parsed.append(_normalize_line(value))
        if parsed:
            return parsed
        return None
    if isinstance(payload, dict):
        lines = payload.get("lines")
        if isinstance(lines, list) and lines:
            return [_normalize_line(str(item)) for item in lines if str(item).strip()]
        msg = payload.get("message")
        if isinstance(msg, str) and msg.strip():
            return [_normalize_line(msg)]
        text = payload.get("text") or payload.get("content")
        if isinstance(text, str) and text.strip():
            return [_normalize_line(line) for line in text.strip().splitlines() if line.strip()]
        data = payload.get("data")
        if isinstance(data, list) and data:
            return [_normalize_line(str(item)) for item in data if str(item).strip()]
        if isinstance(data, dict):
            nested = data.get("lines") or data.get("text") or data.get("content")
            if isinstance(nested, list) and nested:
                return [_normalize_line(str(item)) for item in nested if str(item).strip()]
            if isinstance(nested, str) and nested.strip():
                return [_normalize_line(line) for line in nested.strip().splitlines() if line.strip()]
    return None


def _append_api_log(line: str) -> None:
    try:
        with Path("/tmp/vision_lock_api_error.log").open("a", encoding="utf-8", errors="ignore") as fp:
            fp.write(f"{line}\n")
            fp.flush()
    except Exception:
        pass
    try:
        with Path("/tmp/vision_lock_runtime.log").open("a", encoding="utf-8", errors="ignore") as fp:
            fp.write(f"api_log: {line}\n")
            fp.flush()
    except Exception:
        pass


_api_disabled_logged = False
_api_error_note = ""
_api_source = ""


def _normalize_line(value: str) -> str:
    normalized = " ".join(value.strip().split())
    return normalized


def _set_api_error(msg: str) -> None:
    global _api_error_note
    _api_error_note = msg


def _safe_read_json_or_text(path_text: str) -> list[str] | None:
    try:
        payload_path = path_text.strip()
        if payload_path.startswith("file://"):
            payload_path = payload_path[len("file://"):]

        if not payload_path:
            return None

        data = Path(payload_path).read_text(encoding="utf-8", errors="ignore")
        text = data.strip()
        if not text:
            return None

        if text.startswith("{") or text.startswith("["):
            try:
                payload = json.loads(text)
                return _parse_api_payload(payload)
            except Exception:
                pass

        return [_normalize_line(line) for line in text.splitlines() if line.strip()]
    except Exception:
        return None


def _api_status_lines() -> List[str]:
    if not _api_error_note:
        if _api_source == "local_file":
            return ["接口状态：本地文案源"]
        if _api_source == "remote":
            return ["接口状态：远端接口正常"]
        return ["接口状态：离线文案模式"]
    return [f"接口状态：{_api_error_note}"]


def load_remote_lines(content_cfg: ContentConfig) -> Optional[List[str]]:
    global _api_disabled_logged
    global _api_source
    if not content_cfg.tip_api:
        _api_source = "offline"
        if not _api_disabled_logged:
            _append_api_log("api_offline_tip_api_empty")
            _api_disabled_logged = True
        _set_api_error("tip_api 未配置，使用离线文案")
        return None

    if isinstance(content_cfg.tip_api, str) and content_cfg.tip_api.startswith("file://"):
        lines = _safe_read_json_or_text(content_cfg.tip_api)
        if lines:
            _api_source = "local_file"
            _append_api_log(f"api_local_file ok path={content_cfg.tip_api} lines={len(lines)}")
            _set_api_error("")
            return lines

        _append_api_log(f"api_local_file_error path={content_cfg.tip_api}")
        _set_api_error("本地 API 文件读取失败，切换离线文案")
        return None

    headers = {
        "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
        "User-Agent": "RK3568-VisualLock/1.0",
    }

    for verify in (True, False):
        try:
            start = time.time()
            resp = requests.get(
                content_cfg.tip_api,
                timeout=4,
                headers=headers,
                verify=verify,
            )
            if resp.encoding is None or "charset" not in (resp.headers.get("Content-Type") or "").lower():
                resp.encoding = resp.apparent_encoding or "utf-8"
            resp.raise_for_status()

            try:
                payload = resp.json()
            except ValueError:
                text = resp.text.strip()
                if text:
                    _append_api_log(
                        f"api_nonjson_text verify={verify} url={content_cfg.tip_api} chars={len(text)} elapsed_ms={int((time.time() - start) * 1000)}"
                    )
                    _set_api_error("")
                    return [_normalize_line(line) for line in text.splitlines() if line.strip()]
                raise

            parsed = _parse_api_payload(payload)
            if parsed:
                _append_api_log(
                    f"api_ok verify={verify} url={content_cfg.tip_api} elapsed_ms={int((time.time() - start) * 1000)}"
                )
                _api_source = "remote"
                _set_api_error("")
                return parsed
            _append_api_log(
                f"api_empty_or_unexpected verify={verify} code={resp.status_code} url={content_cfg.tip_api}"
            )
            _set_api_error(f"接口返回异常 (status={resp.status_code})")
            return None
        except requests.exceptions.Timeout:
            _append_api_log(f"api_timeout verify={verify} url={content_cfg.tip_api} timeout=4s")
            _set_api_error("接口超时（2s）")
            if verify:
                continue
            return None
        except requests.exceptions.SSLError as exc:
            _append_api_log(f"api_ssl_error verify={verify} url={content_cfg.tip_api} msg={exc}")
            _set_api_error("证书校验失败，已尝试降级重试")
            if verify:
                continue
            return None
        except requests.RequestException as exc:
            _append_api_log(f"api_request_error={type(exc).__name__} verify={verify} url={content_cfg.tip_api} msg={exc}")
            _set_api_error("接口请求失败")
            return None
        except ValueError as exc:
            _append_api_log(f"api_payload_parse_error={type(exc).__name__} verify={verify} url={content_cfg.tip_api} msg={exc}")
            _set_api_error("返回数据解析失败")
            return None
        except Exception as exc:
            _append_api_log(f"api_unknown_error={type(exc).__name__} verify={verify} url={content_cfg.tip_api} msg={exc}")
            _set_api_error("接口异常")
            return None

    return None


def build_content(title: str, subtitle: str, content_cfg: ContentConfig) -> LockContent:
    lines = load_remote_lines(content_cfg)
    offline_banner = "提示：tip_api 未配置，当前为离线文案"
    if not lines:
        lines = load_cached(content_cfg)
        if not content_cfg.tip_api and offline_banner not in lines:
            lines = [offline_banner] + lines
        lines = lines + _api_status_lines()
    else:
        lines = lines + [_api_status_lines()[0]]
    return LockContent(title=title, subtitle=subtitle, lines=lines)
