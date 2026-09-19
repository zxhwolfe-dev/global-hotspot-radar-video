# 全球热点雷达短视频 · 选题与制作系统

一套把「实时热点选题 → 新闻核查 → 双语口播 → 视觉/动效 → 原声素材嵌入 → 渲染交付 → 平台合规验证」当作完整编辑流程的短视频生产 Skill。面向微信视频号/抖音（中文）与 TikTok/Reels/Shorts（英文）的日更竖屏新闻解读栏目。

## 能力总览

- **选题**：接入实时全球热点 Radar 大样本扫描（目标 300–800 条/日），中英文独立出候选与推荐，历史查重索引防重复，比较站外热榜与官方/权威新闻网站，原文核验后才进推荐；没有合适Radar题可全部采用站外来源。
- **文案**：事实编辑 → 结构编辑 → 文字编辑三轮流程，材料身份归属（作者/助手/第三方/推断/模拟/未知），去模板化与"换题仍成立"筛查；直入主题的开场与固定 0.8 秒片尾，英文独立成稿不互译。
- **视觉**：高端国际新闻杂志 + 手撕纸 + 印刷肌理的系列识别；每题按题材重建构图与配色；每卡≤12秒并有可读信息，生图先复用、逐字视觉复核；按需采用语义动效（scene_v2：预备-行动-收势-稳定阅读）。
- **原声素材**：片源定位 → 入库水印门禁（全片联系表 + 四角放大）→ 真实首帧预填裱框窗口 → 播放回落同帧；引用时间码与权利状态全记录。
- **渲染**：共享渲染器 `scripts/render_episode.py`，每期只写 manifest 与素材；preview/candidate/release 三档 + 内容寻址缓存 + `--reuse-tts/--reuse-audio` 局部返工。
- **验证**：`scripts/validate_episode.py` 确定性终检（字幕、片尾、动效质量、视觉风险预警、审核文件齐全性）+ 发布工作台只读识别。

## 目录结构

```
SKILL.md                        # 入口：工作模式与必读地图
references/
  series_rules.md               # 现行长期规则（选题/口播/视觉/素材/发布）
  selection_and_research.md     # 大样本扫描、查重、深研与事实验证
  editorial_platforms.md        # 分平台口播与讨论设计、三轮编辑合同
  production_workflow.md        # 视觉策划、素材入库门禁、TTS、渲染与修订最小化
  automation_contracts.md       # manifest 数据契约、动效引擎、脚本用法
  quality_and_compliance.md     # 发布前质量与合规门槛
  editorial_identity_and_trials.md  # 解读定位与试验顺序
scripts/
  render_episode.py             # 统一渲染器（三档模式/缓存/复用）
  ghr_renderer/                 # 合成、动效、字幕、质量检查模块
  build_topic_history.py        # 选题历史查重索引
  scan_visual_risks.py          # 视觉风险只读预警（水印/账号/二维码）
  validate_episode.py           # 确定性终检
  benchmark_render_path.py      # 编码器基准
tests/                          # 渲染基础测试
```

## 快速使用

```bash
# 增量合并选题查重索引（保留手工恢复的历史）
python scripts/build_topic_history.py

# 新项目启用production_contract_version: 1，先只读预检
python scripts/preflight_episode.py <项目> --require-research --require-production

# 仅预检退出码为0后继续（先 preview 校对，再 candidate，最后 release）
python scripts/render_episode.py --manifest <项目>/content_manifest.json --mode preview
python scripts/render_episode.py --manifest <项目>/content_manifest.json --mode candidate --reuse-audio
python scripts/render_episode.py --manifest <项目>/content_manifest.json --mode release --reuse-audio

# 终检（会自动跑视觉风险扫描）
python scripts/validate_episode.py <项目目录> --require-research --require-production
```

新制作的 `voice.instruction` 显式写入“中速偏快、利落流畅”；非intro口播卡填写 `visual_anchor`。实测时间线每卡（含停顿、原声和末卡片尾）≤12秒，相邻同静态图累计也≤12秒。旧项目保持兼容，不自动回写。完整字段见 [automation_contracts.md](references/automation_contracts.md)。

验证不调用付费提供者：使用现有Python环境运行 `python -m pytest -q tests`。本仓库没有部署工作流，推送源码不发布视频。

依赖：Python 3.10+、ffmpeg/ffprobe、Pillow、numpy、jieba（字幕断行）、requests、python-dotenv；TTS 走阿里云 qwen-audio（环境变量供密钥）；换机器用 `GHR_REPO_ROOT` / `GHR_WORK_ROOT` 覆盖默认路径。

## 声明

本项目为新闻评论类内容生产工具链。引用外部音视频均为带来源与时间码记录的短引用；视觉风险扫描只预警不拦截，平台申诉与素材取舍由使用者决定。
