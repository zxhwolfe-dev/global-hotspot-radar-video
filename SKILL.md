---
name: global-hotspot-radar-video
description: Select, verify, script, source, produce, and validate the “全球热点雷达” Chinese/English vertical news-video series from live global topics. Use when Codex needs to shortlist current stories, build a research-backed episode, adapt it for WeChat Channels, Douyin, TikTok, YouTube Shorts, or Reels, or revise a finished episode. Do not use for unrelated one-off news summaries, WeChat Official Account long-form articles, or other video series.
---

# 全球热点雷达短视频

把实时热点选择、新闻核查、分平台口播、真实素材、Imagen 视觉、情绪语音、音画同步和发布交付视为一个编辑流程。

开始任何选择、制作或修订前，先完整阅读 [references/series_rules.md](references/series_rules.md)。用户明确修改长期规则时，在本次任务结束前直接更新该文件中的现行规则；不要把单期选题或临时数据写入 Skill。

## 选择工作模式

自动推断满足请求的最小模式，不要求用户说模式名。

1. **先选题**：默认模式。扫描、筛选并列出中英文候选与推荐，停在确认点，不提前生成语音、图片或视频。
2. **制作已确认选题**：用户已确认题目或顺序时，保持题目身份，直接深研、写稿、取材和制作，不重新换题。
3. **选题后全自动制作**：仅在用户明确要求无需确认、自动完成时使用。
4. **修订成片**：先定位内容、视觉、语音、素材或发布契约的根因，只重做受影响阶段。
5. **文案专项审查**：按用户范围回看近期实际口播、事实底稿和反馈，举证根因，按任务做局改或完整对照与复核，再合并必要规则。材料身份、独立价值与编辑顺序见 `editorial_platforms.md`；缺文件或未完成的检查如实记录。此模式不启动选题扫描、不生成素材、不覆盖原成品，交付审查、对照稿和下一期检验记录。

选择或深研时读 [references/selection_and_research.md](references/selection_and_research.md)。写口播和平台版本时读 [references/editorial_platforms.md](references/editorial_platforms.md)。进入素材、生图、语音或渲染时读 [references/production_workflow.md](references/production_workflow.md)。交付前必须读 [references/quality_and_compliance.md](references/quality_and_compliance.md)。

需要运行固定生产脚本、查重索引或视觉预警时读 [references/automation_contracts.md](references/automation_contracts.md)。每期只改项目 manifest 和素材，不复制后再改渲染代码。

Skill 固定的是编辑流程、明确长期偏好和可验证底线，不固定单期创意答案。每期先查看最近成片、用户反馈、当期题材和现有素材，再动态决定视觉结构、图片数量、口播长度、情绪导演、原声窗口布局、声音比例与转场；脚本里的数值只是可覆盖默认值。单期表现良好不等于升级为永久模板，只有用户明确要求或多期验证稳定的经验才写入长期规则。

策划下一期改进或讨论个人解读特色时，读 [references/editorial_identity_and_trials.md](references/editorial_identity_and_trials.md)：其中保存用户确认的解读定位、当前试验顺序与评估方法。具体立场和未确认的形式建议不预设为固定人设，也不授权改动账号资料或重做旧片。

## 必要能力与边界

- 当前选题以本次实时 Radar 扫描为基线，同时执行站外主动发现与原文核查；Radar 不是候选白名单。站外题材注明来源，不伪造 Radar ID 或分数，不能用模型记忆或昨日缓存冒充今天。工具不可用时如实披露覆盖限制。
- Radar 只提供发现线索和趋势观测，事实必须回到新闻原文、官方公告、监管记录、论文、赛事或产品官方材料核实。
- 选题阶段优先使用 `topic-intelligence` 的实时证据合同；本系列已由用户明确要求“大样本扫描”，因此不要套用普通任务的前12或24条限制。
- 生图阶段必须使用 Codex 内置 `imagegen` / Imagen；不得调用 AI 工作站的生图功能。
- 音画阶段使用 `short-video-sync` 的逐段实测时长和同步原则；本系列最终片尾规则固定为0.8秒。
- 不使用收费生成视频功能。真实新闻视频、官方实机、现场片段和普通剪辑允许使用，但必须核对来源、权利状态和引用必要性。
- 创作产物只写入 `/mnt/d/AIWorkstationData/creative_work/videos/` 下的独立日期项目；默认不修改业务仓库、数据库或生产服务。
- 公众号长图文由独立的 `wechat-premium-article` 负责。两种产品可以共享已核验事实和适用素材，但不共享成品结构，也不把口播稿直接扩写成文章。

## 编辑完成标准

系列以有个人见解的新闻解读为主：观众既知道发生了什么，也听得出解读者的主要判断、理由和依据。判断参与整条叙事，不只是结尾附一句点评；事实与观点须分清，纯快讯不硬编立场。讨论邀请可以没有，不以提问或升华作为完成标志。视觉保持系列识别度，构图、证据和运动围绕本题事实与解读展开。

不要把“数量固定为3”当成硬规则。每种语言独立选择1–3条，信息密度、证据质量和讨论价值优先于凑数。中英文可以不同，也不能机械互译。

## 交付

以下是成片制作交付；只选题、文案专项审查或讨论方案时，交付对应范围的结果，不触发制作流程。

至少交付：

- 中英文最终 MP4（按请求范围）；
- 3:4 发布封面；
- SRT 旁挂字幕；
- `publish_info.md`；
- `content_manifest.json`、`episode_manifest.json`、`source_manifest.json`；
- `selection_report.json`，记录实时扫描、站外发现、查重、今日新增事实、观众收益与最终编辑切入点；新制作按 `selection_and_research.md` 保存研究合同与来源引用；
- 音画同步和技术验证报告；
- 完整内容审核记录 `final/content_audit.json`；
- 渲染性能与缓存报告；需要比较编码路径时附 `final/encoder_benchmark.json`；
- 使用内置 Imagen 的提示词与产物记录；
- 发布工作台识别结果。
- `final/visual_risk_report.json`（只预警，不拦截）。
- 使用 `scene_v2` 时的 `final/motion_quality_report.json`。

新制作在 TTS/生图前，按 `selection_and_research.md` 运行 [scripts/check_selection_research.py](scripts/check_selection_research.py) 的 `--require` 研究记录检查。它不联网、不核实事实、不评价创意，也不改变旧 manifest 或发布状态。

先运行 [scripts/build_topic_history.py](scripts/build_topic_history.py) 支持选题查重；制作时用 [scripts/render_episode.py](scripts/render_episode.py) 的预览、候选、发布三档和内容寻址缓存；需要测本机编码器时用 [scripts/benchmark_render_path.py](scripts/benchmark_render_path.py)；最后运行 [scripts/validate_episode.py](scripts/validate_episode.py) 做确定性终检，后者会调用 [scripts/scan_visual_risks.py](scripts/scan_visual_risks.py) 生成只读预警。终检通过不代表事实与审美自动合格，仍需完整阅读口播、抽查画面并听原声窗口。
