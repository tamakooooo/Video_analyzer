#!/usr/bin/env python3
"""OpenClaw CLI 入口"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))

from openclaw_main import skill_main  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze Bilibili/Douyin video and optionally publish to Feishu wiki."
    )
    parser.add_argument(
        "--action",
        default="summarize",
        choices=[
            "summarize",
            "douyin_login_start",
            "douyin_login_poll",
            "bili_login_start",
            "bili_login_link",
            "bili_login_poll",
        ],
        help="执行动作：总结 / 抖音登录开始 / 抖音登录轮询",
    )
    parser.add_argument("--url", default="", help="视频链接（action=summarize 时必填）")
    parser.add_argument("--session-id", default="", help="action=douyin_login_poll 时必填")
    parser.add_argument("--login-timeout-seconds", type=int, default=180, help="抖音扫码超时秒数")
    parser.add_argument("--config", default="./config.json", help="配置文件路径")
    parser.add_argument(
        "--note-style",
        default="professional",
        choices=["concise", "detailed", "professional"],
        help="总结风格",
    )
    parser.add_argument(
        "--download-quality",
        default="fast",
        choices=["fast", "medium", "slow"],
        help="下载音频质量",
    )
    parser.add_argument("--max-note-length", type=int, default=3000, help="总结最大长度")
    parser.add_argument("--no-output-image", action="store_true", help="禁用总结图片渲染")
    parser.add_argument("--no-enable-link", action="store_true", help="禁用时间戳跳转标记")
    parser.add_argument("--no-enable-summary", action="store_true", help="禁用 AI 总结段")
    parser.add_argument("--no-feishu", action="store_true", help="兼容参数（已忽略，飞书发布为必做项）")
    parser.add_argument(
        "--douyin-runner-path",
        default=None,
        help="douyin-downloader run.py 绝对路径",
    )
    parser.add_argument(
        "--douyin-python",
        default=None,
        help="执行 douyin-downloader 的 python 路径",
    )

    args = parser.parse_args()

    result = skill_main(
        action=args.action,
        url=args.url,
        session_id=args.session_id or None,
        login_timeout_seconds=args.login_timeout_seconds,
        config_path=args.config,
        output_image=not args.no_output_image,
        note_style=args.note_style,
        enable_link=not args.no_enable_link,
        enable_summary=not args.no_enable_summary,
        download_quality=args.download_quality,
        max_note_length=args.max_note_length,
        # 飞书发布是必做项，此参数保留仅为兼容旧调用
        enable_feishu_wiki_push=True,
        feishu_push_on_manual=True,
        douyin_downloader_runner_path=args.douyin_runner_path,
        douyin_downloader_python=args.douyin_python,
    )

    # OpenClaw 媒体发送约定：stdout 中输出以 "MEDIA:" 开头的独立行
    # 注意：必须是独立行，不能放在 JSON 字符串里。
    media_tokens = result.get("media_tokens") or []
    if isinstance(media_tokens, list):
        for token in media_tokens:
            token = str(token or "").strip()
            if token.startswith("MEDIA:"):
                print(token)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
