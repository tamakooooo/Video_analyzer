import glob
import os
import subprocess
import tempfile
import time
from typing import Optional

from astrbot.api import logger

from .base import Downloader
from models.audio_model import AudioDownloadResult


class DouyinDownloader(Downloader):
    """基于 douyin-downloader 项目的抖音下载适配器"""

    def __init__(
        self,
        data_dir: str,
        runner_path: str,
        python_bin: str = "python3",
        cookie_ttwid: str = "",
        cookie_odin_tt: str = "",
        cookie_ms_token: str = "",
        cookie_passport_csrf_token: str = "",
        cookie_sid_guard: str = "",
    ):
        super().__init__()
        self.data_dir = data_dir
        self.runner_path = (runner_path or "").strip()
        self.python_bin = (python_bin or "python3").strip()
        os.makedirs(self.data_dir, exist_ok=True)

        self.cookie_ttwid = cookie_ttwid
        self.cookie_odin_tt = cookie_odin_tt
        self.cookie_ms_token = cookie_ms_token
        self.cookie_passport_csrf_token = cookie_passport_csrf_token
        self.cookie_sid_guard = cookie_sid_guard

    def download(
        self,
        video_url: str,
        output_dir: Optional[str] = None,
        quality: str = "fast",
    ) -> AudioDownloadResult:
        if output_dir is None:
            output_dir = self.data_dir
        os.makedirs(output_dir, exist_ok=True)

        if not self.runner_path:
            raise RuntimeError("未配置 douyin_downloader_runner_path")
        if not os.path.exists(self.runner_path):
            raise RuntimeError(f"douyin-downloader 入口不存在: {self.runner_path}")

        start_ts = time.time()

        with tempfile.TemporaryDirectory(prefix="dy_cfg_") as tmp_dir:
            config_path = os.path.join(tmp_dir, "config.yml")
            self._write_config(config_path=config_path, output_dir=output_dir, video_url=video_url)

            cmd = [
                self.python_bin,
                self.runner_path,
                "-c",
                config_path,
                "--show-warnings",
            ]
            logger.info(f"[DouyinDownloader] 开始执行: {' '.join(cmd)}")
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=600,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"douyin-downloader 执行失败(code={proc.returncode}): {proc.stdout[-1200:]}")

        video_file = self._find_latest_video(output_dir, start_ts=start_ts)
        if not video_file:
            raise RuntimeError("未找到下载后的视频文件（mp4）")

        audio_path = self._extract_audio(video_file, output_dir)
        title = os.path.splitext(os.path.basename(video_file))[0]
        video_id = self._extract_aweme_id(video_file)

        return AudioDownloadResult(
            file_path=audio_path,
            title=title or "抖音视频",
            duration=0,
            cover_url=None,
            platform="douyin",
            video_id=video_id or "",
            raw_info={
                "source": "douyin-downloader",
                "video_file": video_file,
            },
        )

    def _write_config(self, config_path: str, output_dir: str, video_url: str):
        # 只保留单视频下载最小配置
        content = (
            f"link:\n"
            f"  - {video_url}\n\n"
            f"path: {output_dir}\n\n"
            f"music: false\n"
            f"cover: false\n"
            f"avatar: false\n"
            f"json: false\n\n"
            f"mode:\n"
            f"  - post\n\n"
            f"number:\n"
            f"  post: 1\n"
            f"  like: 0\n"
            f"  allmix: 0\n"
            f"  mix: 0\n"
            f"  music: 0\n"
            f"  collect: 0\n"
            f"  collectmix: 0\n\n"
            f"thread: 2\n"
            f"retry_times: 2\n"
            f"proxy: \"\"\n"
            f"database: false\n\n"
            f"progress:\n"
            f"  quiet_logs: true\n\n"
            f"transcript:\n"
            f"  enabled: false\n\n"
            f"browser_fallback:\n"
            f"  enabled: false\n\n"
            f"cookies:\n"
            f"  msToken: \"{self.cookie_ms_token}\"\n"
            f"  ttwid: \"{self.cookie_ttwid}\"\n"
            f"  odin_tt: \"{self.cookie_odin_tt}\"\n"
            f"  passport_csrf_token: \"{self.cookie_passport_csrf_token}\"\n"
            f"  sid_guard: \"{self.cookie_sid_guard}\"\n"
        )
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(content)

    @staticmethod
    def _find_latest_video(output_dir: str, start_ts: float) -> Optional[str]:
        candidates = []
        for fp in glob.glob(os.path.join(output_dir, "**/*.mp4"), recursive=True):
            try:
                mtime = os.path.getmtime(fp)
            except OSError:
                continue
            if mtime >= start_ts - 2:
                candidates.append((mtime, fp))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    @staticmethod
    def _extract_audio(video_file: str, output_dir: str) -> str:
        base = os.path.splitext(os.path.basename(video_file))[0]
        audio_path = os.path.join(output_dir, f"{base}.mp3")
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            video_file,
            "-vn",
            "-acodec",
            "libmp3lame",
            "-ab",
            "64k",
            audio_path,
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if proc.returncode != 0 or not os.path.exists(audio_path):
            raise RuntimeError(f"ffmpeg 提取音频失败: {proc.stdout[-1000:]}")
        return audio_path

    @staticmethod
    def _extract_aweme_id(file_path: str) -> str:
        import re

        name = os.path.basename(file_path)
        m = re.search(r"(\d{15,20})", name)
        return m.group(1) if m else ""
