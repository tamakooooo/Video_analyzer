<div align="center">
  <img src="logo.png" alt="Video Analyzer" width="96" />
  <h1>Video Analyzer</h1>
  <p><b>B站 / 抖音视频一键总结 + 必剪转写兜底 + 飞书知识库发布 + 互动卡片回传</b></p>

  <img src="https://img.shields.io/badge/python-3.10%2B-blue" />
  <img src="https://img.shields.io/badge/platform-Bilibili%20%7C%20Douyin-ff69b4" />
  <img src="https://img.shields.io/badge/license-MIT-green" />
</div>

---

## 1. 项目简介

`Video Analyzer` 是一个可在 **AstrBot 插件** 和 **OpenClaw Skill** 场景运行的视频分析工具，核心流程：

> 视频链接 → 下载音频/字幕 → 必剪转写兜底 → LLM 结构化总结 →（可选）图片渲染 → 发布飞书知识库 → 返回互动卡片

当前已支持：
- B站视频总结
- 抖音视频总结（通过 `douyin-downloader`）
- B站/抖音扫码登录流程
- 飞书知识库富文本发布（含截图、思维导图）
- OpenClaw `MEDIA:` 图片消息回传

---

## 2. 主要特性

- **双平台支持**：B站 + 抖音
- **转写兜底**：无平台字幕时自动走必剪转写
- **结构化输出**：按时间轴与主题生成可读纪要
- **飞书强发布（OpenClaw 模式）**：总结完成后必须发布飞书并返回文档链接
- **互动卡片回传**：返回可点击“打开飞书知识库文档”的按钮卡片
- **下载优化**：截图视频下载改为 **720P 优先**，降低超时概率

---

## 3. 目录说明

- `main.py`：AstrBot 插件入口
- `openclaw_main.py`：OpenClaw skill 主逻辑
- `run.py`：OpenClaw CLI 入口
- `services/`：总结、登录、飞书发布等服务
- `downloaders/`：B站/抖音下载器
- `transcriber/`：必剪转写
- `utils/`：URL 解析、Markdown 渲染、截图等工具
- `skill.yaml` / `SKILL.md`：OpenClaw skill 描述

---

## 4. 环境要求

### 系统依赖

```bash
apt install -y ffmpeg wkhtmltopdf
```

### Python 依赖

```bash
python3 -m pip install -r requirements.txt
```

### 抖音支持（可选）

需要单独部署：
- https://github.com/jiji262/douyin-downloader

---

## 5. 配置说明（config.json）

可参考 `config.example.json`。

### 5.1 LLM（必填）

```json
{
  "llm": {
    "api_key": "xxx",
    "base_url": "https://api.openai.com/v1",
    "model": "gpt-4o-mini"
  }
}
```

### 5.2 飞书（OpenClaw 模式下必填）

支持两种写法（推荐扁平写法）：

```json
{
  "feishu_app_id": "cli_xxx",
  "feishu_app_secret": "xxx",
  "feishu_wiki_space_id": "xxxx",
  "feishu_parent_node_token": "xxxx",
  "feishu_domain": "feishu"
}
```

或：

```json
{
  "feishu": {
    "app_id": "cli_xxx",
    "app_secret": "xxx",
    "space_id": "xxxx",
    "parent_node_token": "xxxx",
    "domain": "feishu"
  }
}
```

### 5.3 抖音下载器（抖音链接时必填）

```json
{
  "douyin_downloader_runner_path": "/opt/douyin-downloader/run.py",
  "douyin_downloader_python": "/usr/bin/python3"
}
```

---

## 6. OpenClaw 使用

### 6.1 总结视频

```bash
python3 run.py --url "https://www.bilibili.com/video/BVxxxx"
```

### 6.2 登录流程

#### 抖音

```bash
python3 run.py --action douyin_login_start --config ./config.json
python3 run.py --action douyin_login_poll --session-id "<SESSION_ID>" --config ./config.json
```

#### B站（二维码）

```bash
python3 run.py --action bili_login_start
python3 run.py --action bili_login_poll --session-id "<SESSION_ID>"
```

#### B站（链接模式，通道不支持发图时）

```bash
python3 run.py --action bili_login_link
python3 run.py --action bili_login_poll --session-id "<SESSION_ID>"
```

---

## 7. 输出结果说明

成功时会返回结构化 JSON，常用字段：

- `success`：是否成功
- `note_text`：总结正文（Markdown）
- `note_image`：总结图路径
- `feishu_publish`：飞书发布结果
- `feishu_doc_url`：飞书文档链接
- `feishu_interactive_card`：互动卡片（带跳转按钮）
- `media_tokens`：如 `MEDIA: https://...`（用于发送图片）

---

## 8. AstrBot 说明

本仓库同样保留 AstrBot 插件实现能力（`main.py`）。

在 AstrBot 中：
- 可配置插件参数（总结风格、下载质量、飞书参数、抖音参数等）
- 可通过命令触发总结与登录流程
- 可结合订阅推送能力做自动化纪要

> 注意：不同运行环境（AstrBot / OpenClaw）对“是否强制飞书发布”的策略可能不同，请以当前代码逻辑为准。

---

## 9. 常见问题

### Q1：提示“飞书配置不完整”
补齐 `app_id/app_secret/space_id`（扁平或 `feishu` 嵌套格式都可）。

### Q2：下载很慢或超时
- 已默认优化截图视频为 720P 优先
- 建议检查网络质量
- 可先关闭截图相关链路进行定位

### Q3：抖音下载失败
优先检查：
- `douyin_downloader_runner_path` 是否正确
- Python 路径是否可执行
- Cookie 是否可用（或先走扫码登录）

---

## 10. 开发与调试

```bash
# 本地运行
python3 run.py --url "<VIDEO_URL>" --config ./config.json

# 查看日志（OpenClaw skill 场景）
# ~/.openclaw/skills/video-analyzer/logs/video_analyzer.log
```

---

## 11. 许可证

MIT
