"""
OpenClaw 入口：复用 astrbot_plugin_video_analyzer 的核心能力。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import requests

from astrbot.api import logger
from services.feishu_wiki import FeishuWikiPusher
from services.note_service import NoteService
from utils.md_to_image import render_note_image
from utils.url_parser import detect_platform


SKILL_DIR = Path(__file__).resolve().parent
DATA_DIR = SKILL_DIR / "data"
IMAGES_DIR = DATA_DIR / "images"


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
        cookies=None,
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

    feishu_result = {
        "attempted": False,
        "success": False,
        "message": "",
        "detail": {},
    }
    if bool(config.get("enable_feishu_wiki_push", True)) and bool(
        config.get("feishu_push_on_manual", True)
    ):
        pusher = FeishuWikiPusher(
            app_id=str(config.get("feishu_app_id", "")),
            app_secret=str(config.get("feishu_app_secret", "")),
            space_id=str(config.get("feishu_wiki_space_id", "")),
            parent_node_token=str(config.get("feishu_parent_node_token", "")),
            title_prefix=str(config.get("feishu_title_prefix", "VideoAnalyzer纪要")),
            domain=str(config.get("feishu_domain", "feishu")),
        )
        feishu_result["attempted"] = True
        ok, message, detail = await pusher.push_note(
            note_text=note_text,
            video_url=url,
            screenshot_paths=screenshot_paths,
            mindmap_mermaid=mindmap_mermaid,
        )
        feishu_result["success"] = bool(ok)
        feishu_result["message"] = str(message or "")
        feishu_result["detail"] = detail or {}

    elapsed = round(time.time() - start, 3)
    return {
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
    }


def skill_main(
    url: str,
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

