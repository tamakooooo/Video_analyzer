"""
OpenClaw 入口：复用 astrbot_plugin_video_analyzer 的核心能力。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from astrbot.api import logger
from services.bilibili_login import BilibiliLogin
from services.feishu_wiki import FeishuWikiPusher
from services.note_service import NoteService
from utils.md_to_image import render_note_image
from utils.url_parser import detect_platform


SKILL_DIR = Path(__file__).resolve().parent
DATA_DIR = SKILL_DIR / "data"
IMAGES_DIR = DATA_DIR / "images"
LOGIN_DIR = DATA_DIR / "douyin_login_sessions"
BILI_LOGIN_DIR = DATA_DIR / "bilibili_login_sessions"
PUBLIC_MEDIA_DIR = Path("/tmp/openclaw_media_share")


def _load_config(config_path: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if config_path:
        p = Path(config_path)
        if not p.is_absolute():
            p = (SKILL_DIR / p).resolve()
        if p.exists():
            try:
                config = json.loads(p.read_text(encoding="utf-8-sig"))
            except Exception as e:
                logger.warning(f"读取配置失败: {e}")
    return config


def _resolve_config_path(config_path: str | None) -> Path:
    p = Path(config_path or "./config.json")
    if not p.is_absolute():
        p = (SKILL_DIR / p).resolve()
    return p


def _save_config(config_path: str | None, config: dict[str, Any]):
    p = _resolve_config_path(config_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_media_payload(paths: list[str]) -> dict[str, Any]:
    valid = _to_public_media_paths(paths)
    tokens = [f"MEDIA: {p}" for p in valid]
    return {
        "media_paths": valid,
        "media_tokens": tokens,
        "media_message": "\n".join(tokens),
    }


def _to_public_media_paths(paths: list[str]) -> list[str]:
    PUBLIC_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    for p in paths or []:
        src = str(p or "").strip()
        if not src:
            continue
        if src.startswith("http://") or src.startswith("https://"):
            out.append(src)
            continue
        try:
            src_path = Path(src)
            if not src_path.exists() or not src_path.is_file():
                continue
            uploaded_url = _upload_public_image(src_path)
            if uploaded_url:
                out.append(uploaded_url)
                continue
            if PUBLIC_MEDIA_DIR in src_path.parents:
                out.append(str(src_path))
                continue
            suffix = src_path.suffix or ".png"
            dst = PUBLIC_MEDIA_DIR / f"{int(time.time() * 1000)}_{uuid.uuid4().hex}{suffix}"
            shutil.copy2(src_path, dst)
            out.append(str(dst))
        except Exception:
            continue
    return out


def _upload_public_image(src_path: Path) -> str:
    """上传二维码到公网图床，失败返回空字符串。"""
    if not src_path.exists() or not src_path.is_file():
        return ""
    if src_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return ""

    # 方案 A：优先 0x0.st（免鉴权），失败回退 catbox
    providers = [
        ("https://0x0.st", _upload_to_0x0),
        ("https://catbox.moe/user/api.php", _upload_to_catbox),
    ]
    for _, fn in providers:
        try:
            url = fn(src_path)
            if url and url.startswith(("http://", "https://")):
                return url.strip()
        except Exception:
            continue
    return ""


def _upload_to_0x0(src_path: Path) -> str:
    with src_path.open("rb") as f:
        resp = requests.post(
            "https://0x0.st",
            files={"file": (src_path.name, f, "application/octet-stream")},
            timeout=20,
        )
    if resp.status_code != 200:
        return ""
    return str(resp.text or "").strip()


def _upload_to_catbox(src_path: Path) -> str:
    with src_path.open("rb") as f:
        resp = requests.post(
            "https://catbox.moe/user/api.php",
            data={"reqtype": "fileupload"},
            files={"fileToUpload": (src_path.name, f, "application/octet-stream")},
            timeout=25,
        )
    if resp.status_code != 200:
        return ""
    return str(resp.text or "").strip()


def _attach_openclaw_media_blocks(payload: dict[str, Any], paths: list[str]) -> dict[str, Any]:
    valid = _to_public_media_paths(paths)
    if not valid:
        return payload
    content = [{"type": "text", "text": "\n".join([f"MEDIA: {p}" for p in valid])}]
    content.extend({"type": "image"} for _ in valid)
    payload["content"] = content
    payload["details"] = {"path": valid[0]}
    return payload


def _build_llm_caller(config: dict[str, Any]):
    llm_cfg = config.get("llm") or {}
    api_key = (
        str(llm_cfg.get("api_key", "")).strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
    )
    base_url = (
        str(llm_cfg.get("base_url", "")).strip()
        or os.environ.get("OPENAI_BASE_URL", "").strip()
        or "https://api.openai.com/v1"
    ).rstrip("/")
    model = (
        str(llm_cfg.get("model", "")).strip()
        or os.environ.get("OPENAI_MODEL", "").strip()
        or "gpt-4o-mini"
    )
    if not api_key:
        raise RuntimeError("缺少 LLM API Key（config.llm.api_key 或 OPENAI_API_KEY）")

    endpoint = f"{base_url}/chat/completions"

    async def _ask(prompt: str) -> str:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是专业的视频总结助手，请输出结构化 Markdown。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        def _post() -> str:
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=180)
            if resp.status_code != 200:
                raise RuntimeError(f"LLM 请求失败: HTTP {resp.status_code}, {resp.text[:400]}")
            data = resp.json()
            content = (
                ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            ) or ""
            return str(content).strip()

        return await asyncio.to_thread(_post)

    return _ask


def _build_runtime_config(
    file_config: dict[str, Any],
    *,
    output_image: bool,
    note_style: str,
    enable_link: bool,
    enable_summary: bool,
    download_quality: str,
    max_note_length: int,
    enable_feishu_wiki_push: bool,
    feishu_push_on_manual: bool,
    douyin_downloader_runner_path: str | None,
    douyin_downloader_python: str | None,
) -> dict[str, Any]:
    config = dict(file_config)
    config["output_image"] = output_image
    config["note_style"] = note_style
    config["enable_link"] = enable_link
    config["enable_summary"] = enable_summary
    config["download_quality"] = download_quality
    config["max_note_length"] = int(max_note_length)
    config["enable_feishu_wiki_push"] = bool(enable_feishu_wiki_push)
    config["feishu_push_on_manual"] = bool(feishu_push_on_manual)
    if douyin_downloader_runner_path:
        config["douyin_downloader_runner_path"] = douyin_downloader_runner_path
    if douyin_downloader_python:
        config["douyin_downloader_python"] = douyin_downloader_python
    return config


def _start_douyin_login(config_path: str | None, timeout_seconds: int = 180) -> dict[str, Any]:
    LOGIN_DIR.mkdir(parents=True, exist_ok=True)
    session_id = uuid.uuid4().hex
    session_file = LOGIN_DIR / f"{session_id}.json"
    worker = SKILL_DIR / "services" / "douyin_login_worker.py"
    python_bin = os.environ.get("VIDEO_ANALYZER_PYTHON_BIN", "python3")
    cmd = [
        python_bin,
        str(worker),
        "--session-id",
        session_id,
        "--session-file",
        str(session_file),
        "--data-dir",
        str(LOGIN_DIR),
        "--timeout",
        str(int(timeout_seconds)),
        "--headless",
    ]
    subprocess.Popen(
        cmd,
        cwd=str(SKILL_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 等待二维码生成（最多 25 秒）
    for _ in range(25):
        if session_file.exists():
            try:
                payload = json.loads(session_file.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            status = payload.get("status")
            if status in {"qrcode_ready", "error"}:
                break
        time.sleep(1)

    if not session_file.exists():
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "error": "登录 worker 启动超时",
        }

    payload = json.loads(session_file.read_text(encoding="utf-8"))
    if payload.get("status") == "error":
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "action": "douyin_login_start",
            "session_id": session_id,
            "error": str(payload.get("message") or "生成二维码失败"),
        }
    media = _build_media_payload([str(payload.get("qr_path") or ""), str(payload.get("debug_path") or "")])

    payload_resp = {
        "status": "qrcode_ready",
        "completed": True,
        "success": True,
        "action": "douyin_login_start",
        "session_id": session_id,
        "qr_path": str(payload.get("qr_path") or ""),
        "debug_path": str(payload.get("debug_path") or ""),
        "page_url": str(payload.get("page_url") or ""),
        "page_title": str(payload.get("page_title") or ""),
        "qr_mode": str(payload.get("qr_mode") or ""),
        "message": (
            "请将二维码发给用户扫码，然后调用 douyin_login_poll\n"
            + (media.get("media_message") or "")
        ).strip(),
        **media,
    }
    return _attach_openclaw_media_blocks(
        payload_resp,
        media.get("media_paths") or [],
    )


def _poll_douyin_login(session_id: str, config_path: str | None) -> dict[str, Any]:
    session_file = LOGIN_DIR / f"{session_id}.json"
    if not session_file.exists():
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "action": "douyin_login_poll",
            "session_id": session_id,
            "error": "session 不存在或已失效",
        }

    try:
        payload = json.loads(session_file.read_text(encoding="utf-8"))
    except Exception as e:
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "action": "douyin_login_poll",
            "session_id": session_id,
            "error": f"session 读取失败: {e}",
        }

    status = str(payload.get("status") or "")
    if status == "success":
        cookies = payload.get("cookies") or {}
        config = _load_config(config_path)
        config["douyin_cookie_ttwid"] = str(cookies.get("ttwid") or "")
        config["douyin_cookie_odin_tt"] = str(cookies.get("odin_tt") or "")
        config["douyin_cookie_ms_token"] = str(cookies.get("msToken") or "")
        config["douyin_cookie_passport_csrf_token"] = str(cookies.get("passport_csrf_token") or "")
        config["douyin_cookie_sid_guard"] = str(cookies.get("sid_guard") or "")
        if not config.get("douyin_downloader_runner_path") and os.path.exists("/opt/douyin-downloader/run.py"):
            config["douyin_downloader_runner_path"] = "/opt/douyin-downloader/run.py"
        if not config.get("douyin_downloader_python") and os.path.exists("/mnt/AstrBot/.venv/bin/python"):
            config["douyin_downloader_python"] = "/mnt/AstrBot/.venv/bin/python"
        _save_config(config_path, config)
        return {
            "status": "completed",
            "completed": True,
            "success": True,
            "action": "douyin_login_poll",
            "session_id": session_id,
            "login_status": "success",
            "message": "抖音登录成功，Cookie 已写入配置",
        }

    if status in {"timeout", "error"}:
        return {
            "status": "completed",
            "completed": True,
            "success": False,
            "action": "douyin_login_poll",
            "session_id": session_id,
            "login_status": status,
            "error": str(payload.get("message") or "登录失败"),
            "qr_path": str(payload.get("qr_path") or ""),
            "debug_path": str(payload.get("debug_path") or ""),
        }

    media = _build_media_payload([str(payload.get("qr_path") or ""), str(payload.get("debug_path") or "")])
    payload_resp = {
        "status": "waiting",
        "completed": True,
        "success": True,
        "action": "douyin_login_poll",
        "session_id": session_id,
        "login_status": status or "waiting",
        "message": (
            str(payload.get("message") or "等待扫码确认")
            + ("\n" + media.get("media_message") if media.get("media_message") else "")
        ).strip(),
        "qr_path": str(payload.get("qr_path") or ""),
        "debug_path": str(payload.get("debug_path") or ""),
        **media,
    }
    return _attach_openclaw_media_blocks(
        payload_resp,
        media.get("media_paths") or [],
    )


def _start_bilibili_login() -> dict[str, Any]:
    import segno

    BILI_LOGIN_DIR.mkdir(parents=True, exist_ok=True)
    session_id = uuid.uuid4().hex
    session_file = BILI_LOGIN_DIR / f"{session_id}.json"

    async def _gen():
        bili = BilibiliLogin(str(DATA_DIR))
        return await bili.generate_qrcode()

    qr_data = asyncio.run(_gen())
    if not qr_data:
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "action": "bili_login_start",
            "error": "B站二维码生成失败",
        }

    qrcode_url = str(qr_data.get("url") or "")
    qrcode_key = str(qr_data.get("qrcode_key") or "")
    if not qrcode_url or not qrcode_key:
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "action": "bili_login_start",
            "error": "B站二维码数据不完整",
        }

    qr_path = BILI_LOGIN_DIR / f"bili_login_qr_{session_id}.png"
    segno.make(qrcode_url).save(str(qr_path), scale=10)
    payload = {
        "session_id": session_id,
        "status": "qrcode_ready",
        "qrcode_key": qrcode_key,
        "qrcode_url": qrcode_url,
        "qr_path": str(qr_path),
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
    }
    session_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    media = _build_media_payload([str(qr_path)])

    payload_resp = {
        "status": "qrcode_ready",
        "completed": True,
        "success": True,
        "action": "bili_login_start",
        "session_id": session_id,
        "qr_path": str(qr_path),
        "qrcode_url": qrcode_url,
        "message": (
            "请将二维码发给用户扫码，然后调用 bili_login_poll\n"
            + (media.get("media_message") or "")
        ).strip(),
        **media,
    }
    return _attach_openclaw_media_blocks(payload_resp, media.get("media_paths") or [])


def _start_bilibili_login_link() -> dict[str, Any]:
    """仅返回可点击登录链接（不依赖图片发送）。"""
    async def _gen():
        bili = BilibiliLogin(str(DATA_DIR))
        return await bili.generate_qrcode()

    qr_data = asyncio.run(_gen())
    if not qr_data:
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "action": "bili_login_link",
            "error": "B站登录链接生成失败",
        }

    qrcode_url = str(qr_data.get("url") or "")
    qrcode_key = str(qr_data.get("qrcode_key") or "")
    if not qrcode_url or not qrcode_key:
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "action": "bili_login_link",
            "error": "B站登录链接数据不完整",
        }

    BILI_LOGIN_DIR.mkdir(parents=True, exist_ok=True)
    session_id = uuid.uuid4().hex
    session_file = BILI_LOGIN_DIR / f"{session_id}.json"
    session_file.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "status": "qrcode_ready",
                "qrcode_key": qrcode_key,
                "qrcode_url": qrcode_url,
                "created_at": int(time.time()),
                "updated_at": int(time.time()),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "status": "qrcode_ready",
        "completed": True,
        "success": True,
        "action": "bili_login_link",
        "session_id": session_id,
        "qrcode_url": qrcode_url,
        "message": "请在手机打开 qrcode_url 完成登录，然后调用 bili_login_poll",
    }


def _poll_bilibili_login(session_id: str) -> dict[str, Any]:
    session_file = BILI_LOGIN_DIR / f"{session_id}.json"
    if not session_file.exists():
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "action": "bili_login_poll",
            "session_id": session_id,
            "error": "session 不存在或已失效",
        }

    try:
        payload = json.loads(session_file.read_text(encoding="utf-8"))
    except Exception as e:
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "action": "bili_login_poll",
            "session_id": session_id,
            "error": f"session 读取失败: {e}",
        }

    qrcode_key = str(payload.get("qrcode_key") or "")
    if not qrcode_key:
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "action": "bili_login_poll",
            "session_id": session_id,
            "error": "qrcode_key 丢失",
        }

    async def _poll():
        bili = BilibiliLogin(str(DATA_DIR))
        return await bili.poll_login(qrcode_key)

    result = asyncio.run(_poll())
    status = str(result.get("status") or "")
    payload["status"] = status
    payload["updated_at"] = int(time.time())
    session_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if status == "success":
        return {
            "status": "completed",
            "completed": True,
            "success": True,
            "action": "bili_login_poll",
            "session_id": session_id,
            "login_status": "success",
            "message": "B站登录成功，Cookie 已写入 data/bili_cookies.json",
            "qr_path": str(payload.get("qr_path") or ""),
        }
    if status in {"expired", "error"}:
        return {
            "status": "completed",
            "completed": True,
            "success": False,
            "action": "bili_login_poll",
            "session_id": session_id,
            "login_status": status,
            "error": "二维码已过期，请重新获取" if status == "expired" else "登录失败",
            "qr_path": str(payload.get("qr_path") or ""),
        }
    media = _build_media_payload([str(payload.get("qr_path") or "")])
    payload_resp = {
        "status": "waiting",
        "completed": True,
        "success": True,
        "action": "bili_login_poll",
        "session_id": session_id,
        "login_status": status or "waiting",
        "message": (
            "等待扫码确认"
            + ("\n" + media.get("media_message") if media.get("media_message") else "")
        ).strip(),
        "qr_path": str(payload.get("qr_path") or ""),
        **media,
    }
    return _attach_openclaw_media_blocks(payload_resp, media.get("media_paths") or [])


async def _run_async(
    *,
    url: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    start = time.time()
    llm_ask = _build_llm_caller(config)

    note_service = NoteService(
        data_dir=str(DATA_DIR),
        cookies=BilibiliLogin(str(DATA_DIR)).get_cookies() or None,
        config=config,
    )

    result = await note_service.generate_note_with_artifacts(
        video_url=url,
        llm_ask_func=llm_ask,
        style=str(config.get("note_style", "professional")),
        enable_link=bool(config.get("enable_link", True)),
        enable_summary=bool(config.get("enable_summary", True)),
        quality=str(config.get("download_quality", "fast")),
        max_length=int(config.get("max_note_length", 3000)),
    )

    note_text = str(result.note_text or "").strip()
    if not note_text or note_text.startswith("❌"):
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "error": note_text or "总结生成失败",
            "url": url,
        }

    artifacts = result.artifacts or {}
    screenshot_paths = artifacts.get("screenshot_paths") or []
    mindmap_mermaid = str(artifacts.get("mindmap_mermaid") or "")

    note_image = ""
    if bool(config.get("output_image", True)):
        img_name = f"note_{int(time.time() * 1000)}.jpg"
        img_path = IMAGES_DIR / img_name
        rendered = render_note_image(note_text, str(img_path))
        if rendered and os.path.exists(rendered):
            note_image = str(rendered)

    # 飞书发布为必做项
    pusher = FeishuWikiPusher(
        app_id=str(config.get("feishu_app_id", "")),
        app_secret=str(config.get("feishu_app_secret", "")),
        space_id=str(config.get("feishu_wiki_space_id", "")),
        parent_node_token=str(config.get("feishu_parent_node_token", "")),
        title_prefix=str(config.get("feishu_title_prefix", "VideoAnalyzer纪要")),
        domain=str(config.get("feishu_domain", "feishu")),
    )
    if not pusher.is_config_ready():
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "error": "飞书发布为必做项，但飞书配置不完整（缺少 app_id/app_secret/space_id）",
            "url": url,
        }

    ok, message, detail = await pusher.push_note(
        note_text=note_text,
        video_url=url,
        screenshot_paths=screenshot_paths,
        mindmap_mermaid=mindmap_mermaid,
    )
    feishu_result = {
        "attempted": True,
        "success": bool(ok),
        "message": str(message or ""),
        "detail": detail or {},
    }
    if not ok:
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "error": f"飞书发布失败: {message}",
            "url": url,
            "feishu_publish": feishu_result,
        }

    feishu_doc_url = str((detail or {}).get("doc_url") or "")
    card_title = str((detail or {}).get("title") or "视频总结已完成")
    feishu_interactive_card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": card_title},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "✅ 视频总结已生成并发布到飞书知识库"}},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "打开飞书知识库文档"},
                        "type": "primary",
                        "url": feishu_doc_url or "https://open.feishu.cn/",
                    }
                ],
            },
        ],
    }

    elapsed = round(time.time() - start, 3)
    payload_resp = {
        "status": "completed",
        "completed": True,
        "success": True,
        "platform": detect_platform(url) or "unknown",
        "url": url,
        "processing_seconds": elapsed,
        "note_text": note_text,
        "note_image": note_image,
        "artifacts": {
            "screenshot_paths": screenshot_paths,
            "mindmap_mermaid": mindmap_mermaid,
        },
        "feishu_publish": feishu_result,
        "feishu_doc_url": feishu_doc_url,
        "notify_message": f"✅ 视频总结完成，已发布飞书知识库：{feishu_doc_url}",
        "feishu_interactive_card": feishu_interactive_card,
        **_build_media_payload([note_image] if note_image else []),
    }
    return _attach_openclaw_media_blocks(payload_resp, [note_image] if note_image else [])


def skill_main(
    action: str = "summarize",
    url: str = "",
    session_id: str | None = None,
    login_timeout_seconds: int = 180,
    config_path: str | None = "./config.json",
    output_image: bool = True,
    note_style: str = "professional",
    enable_link: bool = True,
    enable_summary: bool = True,
    download_quality: str = "fast",
    max_note_length: int = 3000,
    enable_feishu_wiki_push: bool = True,
    feishu_push_on_manual: bool = True,
    douyin_downloader_runner_path: str | None = None,
    douyin_downloader_python: str | None = None,
) -> dict[str, Any]:
    if action == "douyin_login_start":
        return _start_douyin_login(config_path=config_path, timeout_seconds=login_timeout_seconds)
    if action == "douyin_login_poll":
        sid = str(session_id or "").strip()
        if not sid:
            return {
                "status": "failed",
                "completed": True,
                "success": False,
                "error": "action=douyin_login_poll 时必须提供 session_id",
            }
        return _poll_douyin_login(session_id=sid, config_path=config_path)
    if action == "bili_login_start":
        return _start_bilibili_login()
    if action == "bili_login_link":
        return _start_bilibili_login_link()
    if action == "bili_login_poll":
        sid = str(session_id or "").strip()
        if not sid:
            return {
                "status": "failed",
                "completed": True,
                "success": False,
                "error": "action=bili_login_poll 时必须提供 session_id",
            }
        return _poll_bilibili_login(session_id=sid)

    if not str(url or "").strip():
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "error": "缺少视频链接",
        }

    if note_style not in {"concise", "detailed", "professional"}:
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "error": "note_style 仅支持 concise/detailed/professional",
        }

    if download_quality not in {"fast", "medium", "slow"}:
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "error": "download_quality 仅支持 fast/medium/slow",
        }

    file_config = _load_config(config_path)
    runtime_config = _build_runtime_config(
        file_config,
        output_image=output_image,
        note_style=note_style,
        enable_link=enable_link,
        enable_summary=enable_summary,
        download_quality=download_quality,
        max_note_length=max_note_length,
        enable_feishu_wiki_push=enable_feishu_wiki_push,
        feishu_push_on_manual=feishu_push_on_manual,
        douyin_downloader_runner_path=douyin_downloader_runner_path,
        douyin_downloader_python=douyin_downloader_python,
    )

    try:
        return asyncio.run(_run_async(url=url, config=runtime_config))
    except Exception as e:
        logger.exception("skill_main 执行异常")
        return {
            "status": "failed",
            "completed": True,
            "success": False,
            "error": str(e),
            "url": url,
        }
