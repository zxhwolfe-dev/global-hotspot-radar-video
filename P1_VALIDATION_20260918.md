# P1：把可确定的返工问题挡在制作前和终检时

接续基线：`c946080016516d9c2c46f74b7e95c5f3b372640d`，视频 PR #1。用户已要求继续优化，本轮做 P1 的有限子集，不做 P2 重排，不启动日更、付费模型或生产部署。旧 `P0_REVIEW.md` 和 `P0_FOLLOWUP_20260918.md` 保留为历史记录；本说明描述最新增量。

## [P1][字幕] 不能把“字幕条数相同”当作字幕一致

**问题引用**：`scripts/validate_episode.py / validate` 原来以 `len(re.findall(r"(?m)^\d+\s*$", srt_text))` 数 cue，再与 manifest 页数比较。数字字幕也被算作序号；正文替换、时间重叠、倒序、超出片长均可能漏检。

**修改**：新增 `scripts/ghr_renderer/subtitle_validation.py`，终检调用 `check_srt`。按 SRT 块解析序号、毫秒时间戳和正文；检查顺序、正时长、重叠、媒体时长、逐页正文及 V2/V3 两行限制。保留原有 `srt_cue_count` 等报告字段，新增详细 `subtitle_contract`。

**理由与实证**：同一组合成项目上直接运行原版与修复版校验器，结果如下。两边均排除可选视觉扫描，音轨是合成音调，不是人声或新闻：

| 合成反例 | 原版 | 修复版 |
|---|---|---|
| 一条正文为 `2026` 的数字字幕 | 错误报成 2 条，FAIL | 正确计为 1 条，PASS |
| manifest 为“三点四”，SRT 被改成“三点五”，条数不变 | PASS | FAIL，正文不一致 |
| 两条字幕时间重叠，条数不变 | PASS | FAIL，时间重叠 |

**风险**：此检查针对 SRT 旁挂文件，不证明烧录像素、人声发音或字幕与真实音素对齐。旧项目之前被容忍的正文差异可能被揭示，不应靠放宽检查掩盖。只忽略排版空白并规范 Unicode NFC，不删除小数点、百分号或改变大小写；空白规范化不是语义等价判断。

## [P1][拆词与准确数字] 在已有字段上检查，不再另建词库

**问题引用**：`scripts/render_episode.py / validate_manifest` 使用 `normalized` 比较字幕，它会删除小数点等符号；`wrap_srt_caption` 的 `subtitle_keep_terms` 只保护页内折行，无法挽救上游已拆开的 `caption_chunks`。

**修改**：共享字幕模块对去标签口播、`caption_text`、拼接后的 `caption_chunks` 做保留标点的比较。沿用已有 `video.subtitle_keep_terms`，检查该词是否跨字幕页，成品 SRT 再检查是否跨行。例如 `三点/四`、`Open/AI` 会指出具体位置。没有提供保护词时不虚构一份全局词库。

**理由**：防止 `3.4` 被当作 `34`，以及先拆坏再试图靠折行恢复专名。

**风险**：不自动重写口播、拆页或缩小字号，也不做 ASR。TTS 标签集合与现有渲染器一致；未来增加标签时须同步这一轻量检查并回归。V1 不新增两行限制，V2/V3 延续现有要求。

## [P1][制作前检查] 在付费 TTS 和重渲染前暴露确定性错误

**问题引用**：此前研究检查只接在 Skill 文档流程，直接终检没有执行研究记录校验；制作前没有独立于 Akai TTS/视觉依赖的统一入口。

**修改**：新增只读 `scripts/preflight_episode.py`。在素材及 manifest 准备后、首次渲染或修订重渲染前执行。检查基础结构、字幕一致性、已有素材路径/非空、路径越界，以及 P0 研究记录。它不导入 renderer/TTS/OpenCV，不联网、不创建文件、不改输入；输出输入 JSON 的 SHA-256 供定位版本，不把该哈希当成并发快照或全部素材内容签名。

```bash
python scripts/preflight_episode.py "$PROJECT" --require-research && \
python scripts/render_episode.py --manifest "$PROJECT/content_manifest.json" --mode preview
```

后续按现有 `candidate/release` 及 `--reuse-audio/--reuse-tts` 流程处理。最终 `validate_episode.py` 内部自动重跑预检，不信任旧的 `preview/*check.json`。新制作的终检也使用 `--require-research`，旧项目默认得到明确的 `NOT_CONFIGURED`，不回写升级。存在研究版本字段，即使值为 null/错误版本，也不能伪装成旧项目跳过。

**理由**：坏研究记录和字幕错误尽量提前暴露；来源改动后会重新检查，不能靠旧 PASS 混过去。`&&` 的失败阻断已用合成哨兵进程验证；没有运行真正 TTS。

**风险**：预检是确定性子集，不检查素材解码、alpha、美感、素材许可或事实真假，也不代替 renderer 的动画校验。直接调用 renderer 仍是兼容入口，不会自动执行新预检；本次没有替换渲染器。入口输出 `STRUCTURE_OK` 不是发布资格。没有增加数据库、任务锁、自动重试或强制迁移。

## [P1][失败报告] 损坏输入和可选依赖故障不再混淆

**问题引用**：`validate_episode.py` 顶层导入可选扫描器；其依赖导入失败会发生在原 advisory try/except 之前。JSON 格式错误可直接抛异常，`--json-output` 指向的旧 PASS 因而未更新。`trailing_silence` 原来不检查 ffmpeg 退出码就解析日志。

**修改**：扫描器延迟到现有 advisory-only 边界内导入；可选扫描故障继续只 warning。损坏 JSON、错误容器、工具执行失败等返回结构化 FAIL；CLI 写出对应失败报告。拒绝非有限片尾值、无效媒体时长/帧率；ffmpeg 非零退出或尾部边界异常不作为有效静音测量。

**风险**：这是已捕获错误的报告改进，不保证进程被强杀、磁盘写满或并发写入后仍有最新报告。没有调大 0.8 秒片尾容忍区间，没有改语音裁剪算法或自动裁掉尾音。视觉扫描未实际完成时明确记 UNAVAILABLE，不伪报扫描通过。

## 验证

```bash
python -m pytest -q tests/test_selection_research.py \
  tests/test_episode_preflight.py tests/test_episode_validation.py
# 130 passed：原研究检查 35 项 + 新增 95 项
```

新增测试直接导入实际模块，不用 AST 抽取方法代替集成。包含 `python -S` 无第三方包预检、CLI 非零退出、旧 PASS 更新、旧 V1/V2 与中文/英文 V3、失败日志、符号/专名保护、路径越界、实际 FFmpeg 生成与 FFprobe 读取 1080×1920/30fps H.264 + AAC 合成素材。合成素材只有音调和色底，不是完整新闻样片；可选视觉扫描的成功/失败在测试中显式模拟，不调用 OCR。

网页环境无法克隆全仓，已通过 GitHub 读取并还原本次所需文件。原 validator、研究检查、原研究测试、共享路径工具及被修改参考文件均先核对 Git blob SHA。没有运行未取得的完整 renderer 基础测试、真实 TTS、历史中英项目、网站全链路或真实后台数据。本次未重跑网站 PR #10 的29项历史测试。

## 合并顺序与仍待本地验证

1. 整体审查本提交的3个生产代码文件、2个新测试文件、制作流程与两份说明；不能只更新 validator 而漏掉它的新导入模块。
2. 按 `CODEX_HANDOFF_20260918.md` 在独立 worktree 补全完整仓库测试、历史中英文副本检查与实际 Skill 同步。没有合并或部署授权，不自动合并远端 main 或重启网站。
3. 尾静音 0.91 秒问题需要在真实音轨上测清末音素、保护段与编码延迟后再修；不以这次合成测试冒充已经根治。CDN 断点下载、配额恢复和网站按需联网研究服务仍未实现，不用无来源样片或无真实查询记录冒充完成。
