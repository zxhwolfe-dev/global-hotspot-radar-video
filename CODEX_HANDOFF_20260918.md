# 给本地 Codex：热点视频 P0 + P1 接续复核与同步

请直接执行下列可在本地完成的检查和安全同步，不需要用户复述聊天。先读取真实代码再操作；不覆盖其他会话工作、不重做旧片、不启动自动发布、不调用付费模型，不合并远端 main、不部署/重启网站。

## 入口与范围

视频仓库：`https://github.com/zxhwolfe-dev/global-hotspot-radar-video`

- PR #1，分支 `gpt/p0-editorial-research-20260917-4c89bfc`。
- 本轮接续父提交 `c946080016516d9c2c46f74b7e95c5f3b372640d`；拉取该分支最新头，记录实际 SHA。先读 `P1_VALIDATION_20260918.md`，再读此前 P0 两份说明。
- 新增 `scripts/preflight_episode.py`、`scripts/ghr_renderer/subtitle_validation.py`，修改 `scripts/validate_episode.py`。配套两份新测试及制作流程。

网站仓库：`https://github.com/zxhwolfe-dev/akaiagents`

- PR #10，分支 `gpt/p0-topic-brief-quality-20260917-f0ac718`，已知头 `f0ac718231a0a4d89ce94ff1835c505f7448bd9d`，先核最新状态。
- 读 `docs/ai/topic_radar_p0_20260917.md`。提示词、3–7项合同、降级/locale和缓存版本作为整体；本轮网页端没有再次修改网站。

## 1. 保护当前工作区，核对实际使用的 Skill

在两个仓库分别记录 `git status --short`、分支和远端；使用独立 worktree 做复核，不在当前脏工作区直接切分支。不 `reset --hard`、不 `clean`、不强推、不整目录覆盖。

核对本机实际入口与 `readlink -f` 结果：通常涉及 `~/.codex/skills/global-hotspot-radar-video/`、独立视频仓库和 `akaiagents/skills/global-hotspot-radar-video/`。这些只是待确认位置，不假定三份都存在或完全相同。检查实际任务/快捷命令引用的是哪份脚本、`GHR_REPO_ROOT` 指向哪里、使用哪个 Python 环境。不得因 GitHub 已更新就报告本机已生效。

## 2. 视频代码与完整测试

先核本次 diff，重点看以下行为是否如说明：

- 预检无网络、无 TTS、无文件写入；显式坏研究版本不能降级成旧版跳过。
- 去标签口播、caption_text、caption_chunks 和 SRT 逐页一致，不能删除小数点/百分号来求一致。
- 字幕仅使用已有 subtitle_keep_terms；保护跨页、跨行，不自动替用户重写。
- V1 不新增两行限制，V2/V3 保留已有规则；新制作必须显式要求研究记录，旧项目仅报告未检查。
- 可选视觉扫描导入/运行失败只预警；研究错误、正文错误和时间轴错误会失败。不要为了绿测把真实错误改成 warning。
- 新字幕模块的 TTS 标签集合与完整 `render_episode.py` 的 SUPPORTED_TAGS 一致；原渲染器/缓存/音色/动效未改。

使用已有项目虚拟环境，不全局升级依赖；确认 pytest、ffmpeg/ffprobe 等已就绪。先执行：

```bash
python -m pytest -q tests/test_selection_research.py \
  tests/test_episode_preflight.py tests/test_episode_validation.py
```

网页本轮结果为130项通过，其中95项新增；这是本轮相关范围，不是全仓测试总数。然后在完整仓库运行既有 `tests/test_renderer_foundation.py` 及其余 `tests/`；TTS provider 的依赖应指向真实 Akai 仓库，先读测试确认不会调用线上模型。不要用生产密钥发请求来修补离线测试。遇到失败要区分新增回归、原有失败、环境缺依赖；可确定的小问题直接修、补测试、提交当前评审分支，报告具体 diff。

## 3. 历史中英各一份：先检查，不重做

从本机已有项目选择一份中文、一份英文，包含真实 manifest、素材、SRT、MP4、审核记录。复制到隔离测试目录，保留原始文件；不要让校验器生成的视觉报告覆盖历史证据。原 manifest 如有绝对路径，仅在副本映射到副本素材，记录映射，不改原项目。

在副本上比较接续基线和当前版本的检查结果。旧项目不加 `--require-research`，缺研究合同应显示 NOT_CONFIGURED；不要补写虚构来源、扫描量、首读或授权来凑通过。发现原有字幕正文、小数点、专名或时间问题，先定位具体 cue/卡片，再判断是以前漏检还是真兼容回归。

检查实际 `video.subtitle_keep_terms` 与数字词组使用情况。新模块不会自动识别所有专有词，也不能凭 SRT 证明烧录像素相同；抽查真实1080p字幕帧，至少包含数字、两行、cue切换和原声窗口。没有正常倍速听过实际音轨就记未听检，不用 ASR/波形/文本检查冒充听感通过。

本轮没有修改尾静音裁剪。对已有0.87–0.91秒问题，记录末句 WAV 的末音素、保护段、混音尾部与 AAC 解码后静音；不要调大验收上限、不要盲目减去固定毫秒或压缩整段语音。没有充分证据先保留问题，不声称根治。

## 4. 网站 PR #10 单独回归

在网站独立 worktree 运行：

```bash
python -m pytest -q tests/test_topic_radar_p0_contracts.py tests/test_topic_radar_intelligence.py
```

再按真实文件位置补相关 routes/frontend 测试。检查完整 brief 不误降级，缺项和自报 degraded 不变 complete，英文占位不混中文，推荐非零角度的 handoff 正确，旧七项结构合法，3–7项前端正常，新旧缓存版本隔离。未请求真实模型时，报告离线/mock测试，不宣称已验证 GPT/GLM 的实际生成质量。

本次没有网站自主联网研究服务。不要把“检索词生成成功”写成“已搜索核实”，也不要为本轮同步自动引入新付费搜索服务、模型链或抓取任务。

## 5. 通过后同步本地生效路径

先备份实际已安装 Skill，对比其本地额外改动；无冲突时同步审核通过的变更，有本地新增则合并保留，不做 `rsync --delete` 式整体替换。记录源 commit、目的路径与关键文件 SHA-256。确认 validator 的新导入文件及旧 P0 的 check_selection_research.py 一起到位，不只复制文档或单个脚本。

优先用评审 worktree 的脚本验证，再切到实际已安装路径重跑 CLI 帮助和一个合成项目预检，确认调用的是新代码。网站只准备经过回归的集成改动；没有部署授权不动线上服务。两个仓库各自记录，不宣称同步视频 Skill 就完成网站部署。

今后新一期在素材及 manifest 准备好后、第一次渲染前执行：

```bash
# PROJECT 指真实当期目录；SKILL_DIR 指刚核实的安装目录。
python "$SKILL_DIR/scripts/preflight_episode.py" "$PROJECT" --require-research && \
python "$SKILL_DIR/scripts/render_episode.py" --manifest "$PROJECT/content_manifest.json" --mode preview
```

修订也先预检，之后按原音频指纹选择 reuse-audio 或 reuse-tts；不要自动重试付费 TTS。新制作的最终检查：

```bash
python "$SKILL_DIR/scripts/validate_episode.py" "$PROJECT" --require-research \
  --json-output "$PROJECT/final/episode_validation.json"
```

研究阶段原有 check_selection_research.py 仍可在生图前单独运行。STRUCTURE_OK、技术 PASS 与事实正确、实际听感、画面美感、发布资格分开记录。

## 交回给用户的结果

形成一份本地复核记录，给出两仓库实际 SHA、完整测试命令/结果、中文和英文副本检查差异、安装前后路径与哈希、你补修的提交，以及仍需真实音频/后台/服务接入才能验证的事项。已同步、未同步、未部署分别写明。不要再只回“建议继续测试”或把网页端未执行的项目写成已经完成。
