# 自动化脚本与数据契约

## 通用渲染器

以后每期只编辑项目根目录的 `content_manifest.json` 和素材，不复制或修改渲染代码：

```bash
.venv/bin/python /home/zxhwolfe/.codex/skills/global-hotspot-radar-video/scripts/render_episode.py \
  --manifest /mnt/d/AIWorkstationData/creative_work/videos/<项目>/content_manifest.json \
  --mode release
```

推荐顺序：

```bash
# 低清快速校对
.../render_episode.py --manifest <path>/content_manifest.json --mode preview

# 全尺寸候选，复用已确认音轨
.../render_episode.py --manifest <path>/content_manifest.json --mode candidate --reuse-audio

# 正式质量，复用全尺寸中间缓存
.../render_episode.py --manifest <path>/content_manifest.json --mode release --reuse-audio
```

默认仍使用本机既有路径；换机器时可设置 `GHR_REPO_ROOT` 指向包含 TTS Provider 的 Akai 仓库，设置 `GHR_WORK_ROOT` 指向视频创作根目录，不需要修改脚本源码。

局部返工：

```bash
# 保留提供者合成的整段 TTS，重新裁段、混音、排时间线并渲染
.../render_episode.py --manifest <path>/content_manifest.json --reuse-tts

# 口播、字幕和时间线完全不变，只重做图片、片段布局或转场
.../render_episode.py --manifest <path>/content_manifest.json --reuse-audio
```

`--reuse-tts` 与 `--reuse-audio` 互斥。渲染器不再只核对卡片 ID：TTS 指纹包含正文、字幕、模型、音色和 instruction；完整音频指纹还包含停顿、原声音频时间码/音量、混音和0.8秒片尾规则。任一相关字段改变都会拒绝错误复用；只换图片、动画或原声视频布局仍可复用正确音轨。

渲染缓存保存在项目 `cache/render/`。键包含素材签名、时长、布局、动画清单、尺寸、帧率和中间编码参数；不得跨键强行复用。`preview` 使用独立低清缓存，`candidate` 与 `release` 共享全尺寸中间缓存。最终 xfade 和混音仍按每种模式重新生成，确保转场与总时长正确。

### `content_manifest.json` 核心结构

```json
{
  "project": "全球热点雷达｜日期与语言",
  "quality_contract_version": 3,
  "production_contract_version": 1,
  "language": "zh-CN",
  "voice": {
    "provider": "alibaba_qwen_tts",
    "model": "qwen-audio-3.0-tts-plus",
    "voice": "longanlingxin",
    "instruction": "中速偏快、利落流畅、句内推进感强、不拖长句尾；数字与专名清楚，不吞尾、不抢句"
  },
  "video": {
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "pre_roll_seconds": 0.35,
    "tail_hold_seconds": 0.8,
    "output_filename": "可选.mp4",
    "same_story_transition": "fade",
    "same_story_transition_seconds": 0.24,
    "cross_story_transitions": ["smoothleft", "smoothright"],
    "cross_story_transition_seconds": 0.36
  },
  "audio": {
    "bed_tone_volume": 0.016,
    "bed_noise_volume": 0.005,
    "speech_gap_seconds": 0.24,
    "chapter_cue_volume": 0.085,
    "chapter_cue_frequencies": [180, 920, 310]
  },
  "cards": []
}
```

- 卡片数量不固定。第一张必须是无口播封面；之后每个卡片是一段完整口播节拍。
- `story_id` 相同使用题内转场，不同使用跨题转场。题材数由非 `intro` 的唯一 `story_id` 自动计算。
- `caption_text` 必须等于去掉情绪/拟声标签后的 `tts_text`。
- 一段连续口播需要多次换字幕时，保留一个卡片和一条 TTS，在卡片增加 `caption_chunks` 字符串数组。数组按原文顺序拼接后必须与 `caption_text` 等价；质量合同 V2 会阻止任何单页超过两行。渲染器按文本权重在该口播的实测可听区间内分配字幕页，不重启画面、动画或语气。
- 若不配置 `output_filename`，按实际题材数和语言生成 `global-hotspot-radar-topN_ZH|EN.mp4`。
- 上例中的时长、音量、频率和转场都是起点，不是系列审美规则。按当期语音、题材气质和素材实测后覆盖。
- `audio.speech_gap_seconds` 是相邻口播段的默认呼吸缝；可在单张卡片用 `pause_after_seconds` 覆盖。它不写死为系列常量，应根据句意、情绪和实听动态调整；最后一张卡不追加该间隔，直接在完整末句后计算固定0.8秒片尾。
- 某个边界需要题材专属转场时，在前一张卡片配置 `transition_to_next: {"type": "fade", "duration_sec": 0.22}`；未配置时才使用全局默认。

### 原声视频窗口

任意口播卡片可增加：

```json
"original_clip": {
  "video": "references/topic_video.mp4",
  "audio": "references/topic_audio.mp4",
  "source_start_sec": 12.4,
  "duration_sec": 2.8,
  "presentation": {
    "mode": "center_window",
    "foreground_height_ratio": 0.323,
    "vertical_position": 0.5,
    "background_blur": 34
  },
  "audio_volume": 0.78,
  "audio_fade_in_sec": 0.08,
  "audio_fade_out_sec": 0.14,
  "label": "官方片段原声"
}
```

`video` 与 `audio` 可以指向同一文件，也可分别指向视频流和音频流。渲染器自动在本节拍口播结束后插入同时间码画面与原声，字幕同时收起；随后回到生成卡片再进入转场。每个窗口必须在 `source_manifest.json` 保存来源和权利说明。

`presentation.mode` 支持 `center_window`、`contain`、`cover_crop` 和 `scene_window`。应先看素材比例和主体位置再选择，不能所有新闻都固定为620像素中心窗。片段时长没有统一硬上限；音量与淡入淡出也要按原片响度实测。未填写的字段才使用渲染器默认值。

新项目还可使用 `scene_window`，让原声画面在动作完成后的生成场景中淡入淡出，并在结束后回到完全相同的场景状态：

```json
"presentation": {
  "mode": "scene_window",
  "foreground_width_ratio": 0.88,
  "foreground_height_ratio": 0.375,
  "horizontal_position": 0.5,
  "vertical_position": 0.48,
  "video_fade_in_sec": 0.16,
  "video_fade_out_sec": 0.18
}
```

原声窗优先使用 `foreground_width_ratio` 与 `foreground_height_ratio`，这样 `preview`、`candidate` 和 `release` 会保持同一相对几何位置。兼容字段 `foreground_height` 仍可填写设计画布像素值；渲染器会按 `video.height` 等比换算到当前 profile，禁止把预览像素直接写进清单。`scene_window` 的 `horizontal_position` / `vertical_position` 表示窗口在“剩余可移动距离”中的左上角位置，公式分别是 `x=(W-w)*horizontal_position`、`y=(H-h)*vertical_position`；它们不是窗口中心占整张画布的比例。若设计画布上的实测窗口为 `(x0,y0,w0,h0)`，应换算为 `w0/W`、`h0/H`、`x0/(W-w0)`、`y0/(H-h0)`，禁止直接填写 `(x0+w0/2)/W` 或 `(y0+h0/2)/H`。若生图本身已有纸框，片段前静帧、播放窗口和播放后 settled scene 必须共用同一实测矩形；可把 `border_color` 设为透明，避免渲染器再套一层边框，并分别抽查淡入前一帧、淡入中帧、稳定播放帧和淡出末帧是否发生位移。

`cover_crop` 支持 `focal_x` / `focal_y`；发布封面可在 `video.publish_cover_asset` 指定独立3:4素材，或用 `video.cover_focal_y` 控制现有首图裁切焦点。

### 透明图层语义动画

任意卡片可增加：

```json
"animation": {
  "layers": [
    {
      "asset": "transparent/subject.png",
      "effect": "rise_reveal",
      "position": [0.5, 0.58],
      "width_ratio": 0.62,
      "start_sec": 0.2,
      "reveal_sec": 0.65,
      "from_scale": 0.96,
      "to_scale": 1.0,
      "from_blur": 5,
      "drift": [0.0, -0.006]
    }
  ],
  "effects": [
    {"type": "scan_light", "start_sec": 0.7, "duration_sec": 1.2, "opacity": 0.1}
  ]
}
```

图层必须是带真实 alpha 的 PNG。可用动作包括 `focus_in`、`rise_reveal`、`slide_left`、`stamp` 和 `cursor_tap`；另可配置一次性 `pulse`、有明确时间窗的 `shake`、单向 `drift`，以及克制的 `scan_light` / `vignette_pulse`。配置不是动作菜单：只选择与当前题材语义一致的少数动作，并目视检查完成后是否稳定。

以上旧格式继续由 `legacy` 引擎兼容。新复杂卡使用 `scene_v2`，动作具备预备、行动、收势和稳定阅读阶段：

```json
"animation": {
  "engine": "scene_v2",
  "minimum_stable_read_sec": 0.55,
  "layers": [
    {
      "id": "subject",
      "asset": "transparent/subject.png",
      "role": "subject",
      "z": 20,
      "position": [0.52, 0.60],
      "anchor": [0.5, 0.88],
      "width_ratio": 0.58,
      "shadow": {
        "type": "contact",
        "offset": [7, 14],
        "blur": 20,
        "opacity": 0.30
      },
      "actions": [
        {
          "preset": "subject_reveal",
          "start": "card_start+0.18",
          "anticipation_sec": 0.10,
          "duration_sec": 0.82,
          "settle_sec": 0.30,
          "easing": "out_cubic"
        }
      ]
    },
    {
      "id": "foreground-paper",
      "asset": "transparent/foreground-paper.png",
      "role": "foreground",
      "z": 40,
      "position": [0.5, 0.82],
      "width_ratio": 1.0,
      "actions": [{"preset": "evidence_focus", "start": 0, "duration_sec": 0.12, "settle_sec": 0.10}]
    }
  ]
}
```

当前 `scene_v2` 支持 `subject_reveal`、`evidence_focus`、`slide_reveal`、`paper_unfold`、`stamp_impact`、`cursor_press`、`target_response` 和 `compare_split`，并支持多条 easing、预乘 Alpha、按 `z` 合成、相对 `anchor` 旋转缩放以及 `subject` / `evidence` 的默认阴影。`foreground` 图层通过更高 `z` 形成真实遮挡。当前每个图层只执行 `actions` 中的一个主动作；需要目标响应时使用另一个图层错峰配合，不能假装已经实现任意关键帧序列。不要为炫技堆满预设；每张卡仍只选择与口播语义一致的主动作。

正式渲染生成 `final/motion_quality_report.json`。对 `scene_v2`，动作侵入固定0.8秒片尾或未达到配置的稳定阅读时间会阻断 release；legacy 只给迁移 warning，避免破坏历史项目。

## 系列选题历史索引

每次选题宽筛前增量合并索引：

```bash
.venv/bin/python /home/zxhwolfe/.codex/skills/global-hotspot-radar-video/scripts/build_topic_history.py
```

默认保留已有索引全部历史题、手工字段与稳定 topic_id，再扫描 `/mnt/d/AIWorkstationData/creative_work/videos/` 中全部全球热点项目，把历史不同 `source_manifest.json` 结构统一为：事件标签、标准化源 URL、Radar ID、中英文出现记录、首次/最近出现日期。输出为：

```text
/mnt/d/AIWorkstationData/creative_work/videos/global_hotspot_topic_history.json
```

对候选做标题和源 URL 双查重：

```bash
.../build_topic_history.py --query "候选标题" --url "https://原文地址"
```

`repeat_warning: true` 只表示需要编辑判断。旧事件有实质新进展时仍可做，但在 `selection_report.json` 写明新增事实；不得让脚本自动删除候选。

## 发布前视觉预警

独立运行：

```bash
.venv/bin/python /home/zxhwolfe/.codex/skills/global-hotspot-radar-video/scripts/scan_visual_risks.py \
  /mnt/d/AIWorkstationData/creative_work/videos/<项目>
```

它会用离线 OCR、二维码检测和来源域名提示扫描每张内容图、透明图层、封面和成片动态窗口，报告账号标识、关注/粉丝/榜单等平台界面、二维码及可识别平台水印。输出 `final/visual_risk_report.json`。静态卡只检查原图，不在成片里重复 OCR；视频只批量抽取透明动画和原声窗口的开始/中间/结束帧。只有专项复核才用 `--video-samples` 增加均匀抽帧。

该报告永久遵循三个边界：`advisory_only: true`、`blocking: false`、不自动修改任何素材。检测结果可能误报或漏报；必须人工查看对应画面，并区分平台审核风险与确认侵权。`validate_episode.py` 会自动运行这项扫描并把结果加入 warnings，但不会因此把 PASS 改成 FAIL。

## 编码器基准

```bash
.venv/bin/python /home/zxhwolfe/.codex/skills/global-hotspot-radar-video/scripts/benchmark_render_path.py \
  --output <项目>/final/encoder_benchmark.json
```

脚本实测当前 FFmpeg 暴露的 H.264 编码器，不因为机器存在 NVIDIA GPU 就假设 NVENC 可用。硬件编码器只有在 FFmpeg 列出且基准通过时才可启用；否则三档继续使用 `libx264`。不要为提速降低内容审核范围。

## 当前生产偏好合同

新制作必须设置 `production_contract_version: 1`（与质量V3、研究V1独立）。首次渲染前运行 `preflight_episode.py <项目> --require-research --require-production`；终检运行 `validate_episode.py <项目> --require-research --require-production`，避免缺失新合同却只按旧项目检查。

- `voice.instruction` 显式包含 `中速偏快、利落流畅`，英文配音也可用这段导演要求并补充自然英文表演说明。不能以默认值代替 manifest 的明确记录。
- 非 intro 口播卡增加 `visual_anchor` 字符串，写明遮住字幕仍能读到的事实、数字、对照或证据，例如“7秒与41秒的实测耗时对照，注明单次测试”。现场镜头写明动作及证据意义。该字段只是策划记录，不证明实际图像合格。
- TTS 后、视频编码前与终检读取实测 `timeline.json`：每卡起止时间包含停顿、原声窗口、末卡0.8秒片尾，均≤12秒；相邻同一静态图累计也≤12秒。字幕分页和换ID不能规避。预估不足不自动提速、裁断或重复付费，保存已有音频后停止，按语义拆卡。
- 新合同直接渲染会在TTS前强制研究/生产预检；旧文件不回写，未启用合同的旧项目可只读复核，但不代表满足新偏好。缺失/非法合同版本不会被当作已通过。
- 历史索引默认合并，`--no-write` 也按合并结果查重；`--catalogue` 自定义时输出默认随目录，不意外写入全局索引。现有索引损坏时拒绝覆盖，写入采用同目录原子替换。
