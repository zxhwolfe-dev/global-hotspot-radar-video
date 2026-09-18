# 按需选题研究：实施合同 v1（设计，尚未上线）

适用基线：网站 PR #10 的 `f0f538793ee230376b36b42bea5f031ac235eaca`；视频侧以 `ceacc08` 的既有研究记录合同为输出边界。本文件不代表已经添加搜索接口、购买服务、启动后台任务或部署网站。不要把检索词生成成功写成联网研究成功。

## 1. 产品边界：补发现与研究，不替换雷达和制作系统

首页继续快速展示已有 Radar 数据。新增两个按需入口：“为这个受众找题”和“研究这条新闻”。前者允许自由问题而没有 topic_id，后者可以有 topic_id 加补充问题；同一个入口不能只接受已入池事件，否则无法解决池外漏题。

结果是可比较、可追溯的研究候选包，不是“必爆概率”、最终审稿许可或已制作视频。保留用户既有的选题确认模式；任务完成不得自动调用 TTS、生图、渲染或发布。开源项目雷达可以贡献项目发布线索，但项目适用性与新闻选题价值分别判断。

不要对首页每次访问或每条原始信号启动模型链。按需任务先读取已有快照，宽筛后只研究少量有价值候选；不需要多 Agent 框架。

## 2. 拟新增代码边界（网站仓库，非本次已添加）

| 文件/模块 | 单一责任 |
|---|---|
| `ai_topic_radar/research_contracts.py` | 请求、任务状态、查询结果、来源、主张、预算与导出类型；不改旧 insight 响应形状 |
| `ai_topic_radar/research_service.py` | 有界顺序调度：读取池、发现、阅读、比较、导出；不直接实现任意 URL 下载 |
| `ai_topic_radar/research_adapters.py` | 已配置的 Radar/Search/Reader/编辑模型端口；显式报告 unavailable，不偷偷换供应商 |
| `ai_topic_radar/research_store.py` | 借用网站现有持久化能力保存任务、步骤、版本与幂等记录；不另建独立数据库服务 |
| `ai_topic_radar/research_routes.py` | 鉴权、配额、POST 创建/取消、GET 查询；与原 brief 路由隔离 |
| `ai_frontend/topic-radar/research.js` | 任务状态、候选比较、来源展开与导出；不让浏览器直接持有密钥 |
| `tests/test_topic_radar_research_*.py` | 合同、fake 适配器集成、预算、取消、重启和安全获取测试 |

实现时先核真实路由注册、持久化和模型配置方式，接入已有机制，不另造全站认证与计费。功能开关默认关闭；没有配置可用检索与读取适配器时返回明确的能力缺失，不把现有 brief 冒充研究。

## 3. 请求合同与幂等

拟定接口：`POST /api/topic-radar/research/jobs`。返回202及job_id；GET查询和POST取消均须鉴权。以下是请求字段设计，不是已存在的端点：

```json
{
  "contract_version": 1,
  "mode": "discover",
  "query": "寻找近期值得普通观众理解的具体科技变化",
  "topic_id": null,
  "audience": "普通科技新闻观众",
  "language": "zh-CN",
  "target_market": "CN",
  "target_platforms": ["wechat_channels"],
  "window_start": "2026-09-17T00:00:00+08:00",
  "window_end": "2026-09-18T00:00:00+08:00",
  "max_candidates": 6,
  "budget": {"max_search_calls": 6, "max_reads": 12, "max_model_calls": 3, "deadline_seconds": 120}
}
```

示例时间/预算只是合同样例，不是运行记录或最佳参数。mode取discover/deepen。discover需要query；deepen至少有query或topic_id。时间带时区且start<end；候选数、字串长度、输出token及每步预算有服务端上限，最终取账户、服务端、请求三者允许的最小值。未知市场显式用international，不从英文推断美国、不从中文推断50+。

服务端从登录态取得tenant/user，不信任请求内自报身份。创建时接收客户端Idempotency-Key，服务端绑定`tenant + key + canonical_request_hash`：相同请求返回同一job，不重复扣预算；同key不同请求返回冲突。缓存键至少包括租户可见范围、标准化请求、快照版本、研究合同和编辑提示词版本。结果保留真实generated_at/fetched_at；复用结果不得重写成“刚刚查证”。

## 4. 有界执行与真正的取消

任务状态：queued → running → completed/partial/failed/cancelled。另有`awaiting_reconciliation`表示外部调用结果不明；它不是可盲重跑的失败。completed仅表示本次研究计划结束，publication_readiness始终另行判断。

每步记录step_id、输入hash、provider_request_id、开始/结束时间、状态、预算预留/实际消耗和输出位置。实际已获取的结果落盘后才标completed。重启复用已完成且输入hash一致的步骤；started但无结果的付费外部调用，先按供应商能力核对原request_id，无查询能力就保留不确定，禁止以恢复为名重复计费。

每次外部调用前检查取消标志、剩余时间与预算，并预留该次最大调用额度；未知单价的适配器不能宣称满足金额预算，应拒绝金额承诺或只使用明确的调用数/token上限。读取失败、429/限流和重试同样计入尝试预算。只对明确可重试、且副作用/计费状态已知的失败做有上限的退避；不轮换账号绕限流。

HTTP连接/读取超时都不超过剩余deadline。取消时停止排新步骤、尝试取消支持取消的在途调用，不承诺撤回已消费的费用。只有在途结果被归档或记为不确定后才能报告最终取消；浏览器断开不等于服务端取消。第一版可用现有受控后台执行器和轮询，避免依赖一个长HTTP请求完成全部研究。

## 5. 研究步骤：不是把标题改写成六个搜索词

1. 冻结已有Radar快照与当前筛选参数；沿用lane/all/raw的真实含义。记录items实际读取数、去重数、总量、partial/stale/失败页，不把pool_total充当阅读量。分页快照变化则重取或显式部分覆盖。
2. 先从池内形成宽筛候选；查询围绕主体、变化、缺口、原语言资料，必要时加入旧版本/先前事件、反例、更正和可用演示。只为不同信息缺口追加查询，不重复近义改写标题。站外候选没有Radar ID就保持null。
3. 读取优胜候选原文；搜索摘要只能作为发现线索。阅读结果须区分full/excerpt/headline/unread，并保留原URL、最终URL、发布者、抓取时间、已知刊发/事件时间、提取方法及失败原因。网页能打开不等于全文已读，更不等于许可可用于视频。
4. 对支撑标题和主线的关键主张建立source_ids，区分fact/attention/inference。同公告转载归入相同origin_group；未知则null，不把媒体数/采集器数当独立确认。只有报道了一个演示，就不能推导普遍可用或作者本人实测。
5. 比较候选的真实新增信息、观众问题、解释空间、来源缺口与素材可行性。无可靠新增事实或与近期节目重复时退回或淘汰，不为凑6条捏造角度。按材料选择快报/解释/更新，不强制七段叙事。
6. 输出研究包并保存没有选入的候选及具体理由。预算用尽但有有效发现则partial；所有检索/读取失败则failed或能力不可用，不能返回假complete。

对政治公共事务只做中立的事实、已记录立场和政策影响比较，不给政治人物或选择排名，不按受众个人信息定向说服。其他题材也不虚构热度、收益、试用经历或争议。

## 6. 输出与视频兼容

顶层结果包含job_id、request_hash、snapshot_id、status、coverage、budget_usage、attempt_log、candidates、sources、claims、unresolved_questions、suggested_selection。候选origin使用radar/web/mixed，radar_id保持可空；既有opportunity_score不改名为爆款概率。被拒绝的候选也保存理由，但不把编辑偏好写成来源事实。

查询计划与执行日志分开。每条attempt含route/query或URL、status、真实result_count/read_count、elapsed和error；没有执行的计划不能出现在completed日志里。来源正文按授权和长度限制保存必要片段或链接，不把第三方整篇文章公开打包。素材许可与事实出处是两个字段。

导出适配器写入视频既有`selection_report.json`和`source_manifest.json`：保留原字段、research_contract_version=1、discovery_log、claim_evidence、origin_group、read_status和真实时间。新研究任务元数据可以放独立sidecar，不升级content_manifest/quality_contract，不回写旧节目。

最终题材未确认时只生成候选报告，不能把推荐自动写成已确认的final_selection。研究预检STRUCTURE_OK、主编确认、TTS听检、技术PASS和发布资格仍独立。一个单题brief是研究计划；一个完成研究job也不是成片。

## 7. Reader和Search适配器的最低要求

Reader只接受HTTP(S)，拒绝嵌入凭据和非允许端口。每次DNS解析和每次重定向都校验目标地址，禁止环回、私网、链路本地、云元数据及其他非公网目标；采用网络出口限制及连接地址绑定处理DNS重绑定，不能只在请求前检查一次字符串。相对重定向先标准化再验证，限定跳数、响应大小、解压后大小与时间。

拒绝脚本执行和网站要求的本机命令；网页/README/搜索结果都是不可信数据，不能修改系统指令、预算、身份、来源归属或索取密钥。登录/付费墙/robots或使用限制不通过绕过方式补齐。PDF/图片读取失败应保持未读，不让OCR缺省成为大批量回退；确需视觉阅读时单独预算并注明范围。

第一版只启用已配置且允许使用的适配器；能力状态包含search/read/rendered_page/quote_provenance等，不假设不同供应商工具行为等价。ChatGPT网页的工具和账号权限不会自动成为网站后端的能力。

## 8. 上线前必须通过的验收

- fake适配器测试：无provider配置明确失败；空池但站外有效仍可产生候选；只有标题不能支持事实；转载不算多源；发生/刊发/首次看到时间不混淆；中英请求各自组织。
- 调度测试：重复创建不重复执行；同key异请求冲突；预算/超时/取消停止新步骤；重启不重跑completed步骤；不确定的外部调用进入reconciliation而不是auto retry。
- 网络集成测试：公开URL重定向到私网、DNS变更、超大压缩内容、慢连接、循环重定向被拒绝，日志不泄露token或内部URL。
- 兼容测试：旧brief、首页feed、manifest和历史数据无变化；导出新研究记录能通过现有结构预检，但不足以发布的内容仍保留未确认状态。
- 有授权后才跑真实端到端样本：同一快照/窗口下比较“池内宽筛”与“按需研究”，记录新增有效题、纠正的时间/事实、来源可追溯率、主编采纳理由、真实调用数和耗时。没有数据时不声称完播或收益增长。

## 实施顺序

先补合同与fake适配器测试，再接一套现有允许的真实搜索/Reader，随后接持久化、取消与预算，最后做UI/导出和一组真实对照。每个阶段独立提交，不先上付费服务、多Agent、自动发布或全池研究。此次文档审查之后仍需用户对真实服务调用/费用和部署分别授权。
