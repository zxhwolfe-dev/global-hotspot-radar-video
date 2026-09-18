# 本地复核记录（2026-09-18，Claude/GLM 执行）

对应 PR #1 分支 `gpt/p0-editorial-research-20260917-4c89bfc`，复核基线 = 分支头 `e1c0f75d54dbe4b0df8653cacdfa097b494fbfdf`。独立 clone/worktree 执行，未动任何远端 main，未部署。

## 1. 代码与测试

- `python -m pytest -q tests/`（全量，akaiagents .venv）：**143 passed**（含 P1 新增 95；三份指定文件 130 passed，与 P1 文档一致）。
- TTS 标签集合：`ghr_renderer/subtitle_validation.TAGS` 与 `render_episode.SUPPORTED_TAGS` 逐项比对，13 项完全一致（无遗漏、无多余）。
- 未发现需要修改的生产代码缺陷；本分支零补丁。

## 2. 历史项目副本（隔离目录，原件未动，校验器输出只写副本）

| 项目 | 基线(main) | 本分支 | 说明 |
|---|---|---|---|
| 中文 09-12（fieldsmedal_pingpong_chiba_ZH） | PASS | PASS | 字幕契约 66/66 零错；本分支多 1 条"项目不在默认目录"警告（/tmp 运行的预期提示） |
| 中文 09-15（ai_qianfan_sim_ZH，已发布归档；从备份 tar 恢复 generated/references/transparent/audio 到副本后） | PASS | PASS | 字幕契约 57/57 零错 |
| 英文 09-12 脚手架（nvidia_lawyer_EN，素材未生成） | — | preflight FAIL（缺素材，预期） | **字幕契约 73 页零错**；暴露真实数据问题：math_1/math_2 残留 effects-only 动画（中文同期已修、英文暂停前未同步） |

历史副本副产物：英文 09-12 活项目 manifest 的 effects-only 动画已移除（备份 `content_manifest.json.bak_20260918`）——该项目属生产数据修复，不在本仓库。

英文完整成片样本（09-09 EN）的 final/manifest 已不在本机活动库（tar 仅含中间产物），含真实 MP4/SRT 的英文全链终检**无法执行，记为未验证**。

## 3. 实际安装路径同步

- 安装路径：`/home/zxhwolfe/.codex/skills/global-hotspot-radar-video/`（readlink -f 确认非软链；`akaiagents/skills/` 下副本不参与渲染）。
- 同步前与 main 基线 diff：内容完全一致，无本地私有改动 → 安全同步。
- 备份：`/mnt/d/WSL-Backups/CreativeWork/skill_global-hotspot-radar-video_backup_20260918_0145.tar`。
- 从本分支 `e1c0f75` rsync（排除仓库专属文档与 `__pycache__`；`preflight_episode.py`、`check_selection_research.py`、`ghr_renderer/subtitle_validation.py` 全部到位）。
- 安装路径验证：143 tests passed；`preflight_episode.py --help`、`validate_episode.py --help` 正常；对 09-12 副本预检 `STRUCTURE_OK`（research=NOT_CONFIGURED，旧项目预期）。
- 关键文件 SHA-256（前 16 位）：`SKILL.md 2930004bed123bf6`；`scripts/preflight_episode.py faf87de95e4a79ae`；`scripts/validate_episode.py f2f4e3ce6e48e241`；`scripts/check_selection_research.py 24904ff120bf3623`；`scripts/ghr_renderer/subtitle_validation.py df42d2bbc47f0bcb`；`references/production_workflow.md c28953b4d3f20511`。

## 4. 同轮网站 PR #10 复核（另一仓库，摘要）

- `test_topic_radar_p0_contracts.py` + `test_topic_radar_intelligence.py`：初跑 1 失败——**PR 漏改旧夹具**（`_rich_gpt_result` 缺 `angles[].fact_basis`，与 p0 contracts 显式断言的降级规则冲突）。本地补齐后 43 passed，修复已推 PR 分支（提交 `058f984e`）。
- 关联测试家族 149 passed；`test_topic_radar_frontend_refresh.py` 9 失败为**环境性**（worktree 缺 node_modules，`node -e` require jsdom 秒挂）；主工作区 8/9 过、1 失败源于本地未推送提交 `a2e09484`（小屏布局改动英文标题），与 PR 无关，未代改。

## 5. 未验证 / 未执行

- 真实 TTS、主观听感、网站真实模型生成质量、前端 UI 回放、线上服务（未部署未重启）。
- 09-15 尾静音 0.876s 在容差内，本轮未触碰裁剪逻辑；尾静音根治待真实音轨数据。
- 两仓库 main 均未合并（等待用户授权）。
