# IM Gateway 能力精简方案与语音能力对照

日期：2026-10-14。状态：用户已批准方案；本提交仅落盘方案与盘点，未实施、未部署。

## 1. 基线与范围

- 发布基线：`docker-20261014.1`。
- 应用源码：`b96eb18ab875d13bae61c818faa42bb46cf79863`。
- 独立任务分支：`work/laptop/im-capability-scope`。
- 独立工作区：`/home/agentdemo/hermes-releases/worktrees/app-im-capability-scope`。
- 仅调整 laptop Docker Gateway 的技能目录可见性、技能正文、SOUL 和技能工具组选择。
- 保持语音 CLI 的技能、提示、工具选择、镜像和 ASR/TTS/唤醒链路不变。
- 不改变通用路由、摘要、模型生成循环、工具执行循环或模型参数。
- 不同步 black/silver/white，不整合它们的并行开发改动。

用户明确要求 IM 继续承担：搜索型场景、家居控制、媒体遥控、明确请求的邮件发送、本地文件处理。
普通文字聊天仍由模型直接回答；无需技能才能理解或整理用户粘贴的文本。

## 2. 盘点依据及限制

实际部署根目录：
`/home/agentdemo/hermes-releases/runtime/laptop-docker-20261013.1`。
旧目录名不代表当前发布版本：laptop 已验收并标记为 docker-20261014.1。

| 项目 | 语音 CLI | IM Gateway |
|---|---|---|
| 配置 | `state/config.yaml` | `gateway-state/config.yaml` |
| 人格 | `state/SOUL.md` | `gateway-state/SOUL.md` |
| 技能 | `state/skills/` | `gateway-state/skills/` |
| 镜像 | `sha256:a09af8b7ae186a77df6a19fb49f8f552af2d6d9fb66a4b015358971df6dc14bc` | `sha256:59e4a6dde9406140e899da01b14d2e3b165f15cfc3d310a380ea82256836ecb4` |

Gateway 配置读取的是其独立 /state 挂载，不是语音 state，也不是旧原生 ~/.hermes。
IM 各平台在当前 Gateway 配置中使用相同工具组列表；本次未逐个平台发送测试消息。

盘点使用：非敏感 YAML 字段、技能 frontmatter/正文、SOUL、实际 Gateway 镜像中的
`skills_list()`、`_get_platform_tools()` 和 `get_tool_definitions()`。
没有调用 LLM、发送邮件、操作家居/媒体、读取聊天内容或重启服务。
这是当前配置和实际镜像的解析结果，不声称捕获了所有已存在会话的历史工具快照。

技能目录中有 17 份 SKILL.md，13 份启用。另 4 份已禁用：
`email-inbox-triage`、`youtube-content`、`maps`、`powerpoint`。
配置禁用列表还包含未安装的技能名，不能把禁用列表长度当成技能安装数量。
两套配置的 13 份可见技能一致；逐文件比较所有对应 SKILL.md，正文无差异；SOUL 当前也一致。
技能名称/描述用于能力发现，不代表所有技能正文每轮全部注入。

### cronjob 的证据边界

语音 CLI 的启用组不包含 cronjob，且 disabled_toolsets 明确包含 cronjob。
Gateway 的启用组包含 cronjob，未显式禁用。独立镜像解析进程中得到 13 项工具；
其 check_fn 因环境条件不满足而不暴露 cronjob_manage。
额外设置 HERMES_GATEWAY_SESSION=1 的条件探针得到 14 项工具。
所以应描述为“IM 配置启用、实际暴露有条件”，不能断言所有 IM 会话都已看到第 14 项，
也不能把它当作已被禁用。本轮保持该配置和门控行为不变。

## 3. 技能对照：当前与目标

“目标 IM”列是待实施的要求，而非本次已完成的结果。

| 技能 | 当前语音 | 当前 IM | 目标 IM |
|---|---|---|---|
| travel-advisor | 启用 | 启用 | 保留；详情直接回复 IM，搜索规则不变 |
| flight-search | 启用 | 启用 | 保留；详情直接回复 IM，搜索规则不变 |
| healthcare | 启用 | 启用 | 保留；安全边界和搜索规则不变 |
| shopping | 启用 | 启用 | 保留；搜索及交易边界不变 |
| home-assistant | 启用 | 启用 | 保留；仅控制模拟家居 |
| local-media-player | 启用 | 启用 | 保留；遥控本机媒体 |
| email-results | 启用 | 启用 | 保留；用户明确要求发邮件才调用 |
| document-to-action-items | 启用 | 启用 | 禁用，不删除文件 |
| meeting-action-items | 启用 | 启用 | 禁用，不删除文件 |
| weekly-review-planning | 启用 | 启用 | 禁用，不删除文件 |
| docx | 启用 | 启用 | 禁用，不删除文件 |
| pdf | 启用 | 启用 | 禁用，不删除文件 |
| xlsx | 启用 | 启用 | 禁用，不删除文件 |
| 合计 | 13 | 13 | 7 |

在 Gateway 专属配置 skills.disabled 中追加上述 6 项，不改语音配置。
禁用技能不等于禁止普通文本分析。另一方面，本地 file 工具不等于完整 Office 执行环境；
当前 docx/pdf/xlsx 技能要求 terminal 执行 Python 脚本，但当前工具没有 terminal。
目标 IM 不再宣传这 3 项专门文件技能的完整能力。语音仍有该依赖缺口，本轮明确不修。

## 4. 工具能力对照

| 工具组/工具 | 当前语音 | 当前 IM | 目标 IM |
|---|---|---|---|
| web: web_search、web_extract | 保留 | 保留 | 不变 |
| file: read_file、search_files、write_file、patch | 保留 | 保留 | 不变；继续支持本地文件读写 |
| skills_list、skill_view | skills 组 | skills 组 | 改用 skills_readonly 组保留 |
| skill_manage | 可见 | 可见 | 不向模型暴露 |
| email_queue: email_send | 可见 | 可见 | 保留，明确邮件请求才使用 |
| demo_home: demo_home_status、demo_home_set | 可见 | 可见 | 保留 |
| local_media: local_media | 可见 | 可见 | 保留 |
| cronjob: cronjob_manage | 显式禁用 | 配置启用、条件暴露 | 不变，不在本轮关闭 |
| terminal、execute_code、computer_use | 不在启用工具集中 | 不在启用工具集中 | 不新增 |
| tts、memory、session_search、voice_delivery | 显式禁用 | 显式禁用 | 不变 |
| feishu_doc、feishu_drive、kanban | 显式禁用 | 显式禁用 | 不变 |

正常工具依赖可用时，语音 13 项；当前 IM 13 项加条件 cronjob；目标 IM 12 项加条件 cronjob。
计数用于本次盘点，不作为替代能力边界的测试策略：验收重点是具体允许/禁止集合及其行为。
当前解析没有引入 tool_search/tool_describe/tool_call 桥接，计划不新增该流程。

### skill_manage 的调查结论与实现选择

基线 toolsets.py 中 skills 固定包含 skills_list、skill_view、skill_manage。
model_tools.py 接受 enabled_toolsets/disabled_toolsets，按工具组解析；不能把 skill_manage
这个工具名直接写入 disabled_toolsets 并假定有效。
skills.write_approval 是审批门控，不是 schema 隐藏开关。
create_custom_toolset 是运行时 Python 能力，不是现成的 config.yaml 单工具禁用字段。

已批准方案：新增通用 skills_readonly 工具组，只列 skills_list、skill_view；保留原 skills。
Gateway 的平台列表使用 skills_readonly 替换 skills，语音仍使用 skills。
复用已有工具组解析和执行范围检查，不新增自定义工具过滤层、环境开关或场景关键词路由。
实现时须验证 _get_platform_tools 不把新组丢弃或恢复成完整 skills；若需配置目录元数据，
只补相应集成，不改变其他组默认选择。还须验证原生分发拒绝不可用 skill_manage，
不能仅凭“schema 中不显示”宣称完成权限隔离。

**重要边界：** 保留 write_file/patch，因此移除 skill_manage 并不构成技能资产不可修改的
安全沙箱。此次目标是缩小 LLM 工具入口，不新增只读挂载或文件权限政策。
若后续需要不可篡改技能，需另行设计资产只读挂载与允许写入的工作目录。

## 5. 场景与输出边界对照

| 行为 | 当前语音（保持） | 当前 IM | 目标 IM |
|---|---|---|---|
| 普通问答 | 简洁自然，语音规则 1–3 句 | SOUL 的语音限制有条件，但场景正文常强制短答 | 默认简洁；按请求直接展开，不强制 1–3 句 |
| 旅行/航班详情 | 场景将主动索要详情视为邮件授权 | 继承同样邮件授权规则 | 在聊天中回复；“详情/完整计划”不等于邮件授权 |
| 明确要求发邮件 | email_send 一次，subject/body | 同左 | 保留，使用 email-results |
| 普通 IM 回复 | 不适用 | Gateway 自动投递 | 不需邮件工具或新飞书发送工具 |
| 家居 | 查询新鲜模拟状态，明确授权才控制 | 同左 | 不变 |
| 媒体停止 | local_media stop 停本机 demo 媒体/TTS | 同左 | 不变；IM 遥控会影响同机播放，不是独立音频会话 |
| 文件处理 | 文本文件读/搜/写/补丁 | 同左 | 不变；不承诺缺少执行工具的 Office 工作流 |
| 邮件回执 | queued 不是 delivered，不轮询重试 | 同左 | 不变 |
| 长期记忆/后台 review | 关闭 | 关闭 | 不变 |
| 语言 | English-only | English-only | 不变 |

删除技能目录摘要只减少能力发现负担，不抹掉已加载正文的旧会话历史。
验收使用新 IM 会话，避免把旧提示残留误判为新配置失效；不擅自删除历史数据库。

### 保持一致的搜索规则

| 场景 | 来源 | 搜索/提取预算 | 其他边界 |
|---|---|---|---|
| 旅行 | site:en.wikivoyage.org | 1 次搜索；必要时最多提取 1 页 | 默认 2–3 highlights；失败后停止搜索，未核实建议明确标注 |
| 航班 | site:expedia.com | 1 次搜索；必要时最多提取 1 页 | 默认 1 条单程选项；不虚构航班/票价，不预订登录支付 |
| 健康 | site:medlineplus.gov | 1 次搜索并读取 1 页 | 保留安全提醒、紧急求助、非诊断边界；不把搜索片段当验证内容 |
| 购物 | site:walmart.com | 1 次搜索并读取 1 页 | 默认 1 商品；无购物车/购买，不编造库存价格 |

IM 回复可包含链接、列表、表格；请求详情不自动增加搜索次数、不开放额外网站。
信息不足时说明验证限度，不以邮件替代无法验证的内容。
搜索技能本身不发送邮件：用户明确请求邮件时由 email-results 承担投递。
若原技能写有“No email”，需明确其不主动发邮件的场景边界，不能与独立显式邮件请求冲突。
除调整信息呈现和交接投递职责外，不放宽搜索、安全、控制或付款边界。

## 6. 后续实现与发布步骤（尚未执行）

1. 从本任务分支继续；另建基于相应发布标签的 ops 任务工作区。
2. 修改 Gateway 专属配置模板的 skills.disabled 和平台工具组，新增 skills_readonly 组及必要元数据。
3. 修改 demo-runtime/gateway-state/skills 中四个搜索场景正文/描述及
   demo-runtime/gateway-state-SOUL.md，明确聊天详情与显式邮件请求的区别。
4. 保留 email-results、家居、媒体和 file 能力；语音 state 及其 SOUL/技能不变。
5. 更新版本化发布配置模板（ops normalized/profiles/gateway-state.template.json）及构建清单，
   不能只编辑运行目录；构建独立 Gateway 候选镜像，固定源码、父镜像、配方与摘要。
6. 在隔离状态中验证目录、schema、分发拒绝、技能行为和渠道隔离。
7. 部署前备份 laptop Gateway Compose/控制入口、配置、SOUL 和技能；记录原图像摘要。
   只切换 Gateway，不重建 CLI、邮件 worker、ASR/TTS、OVMS 或唤醒服务。
8. 新 IM 会话验收；通过后由用户确认是否发布新基线。其他设备仍需单独授权同步。

## 7. 验收门槛

- Gateway 的技能目录和 skill_view 拒绝路径均尊重 6 项禁用设置；保留 7 项正常加载。
- 原生工具解析后无 skill_manage，有 skills_list/skill_view；不能通过残留桥接恢复管理工具。
- Gateway 禁用项不影响 CLI；CLI 技能和工具集与基线保持一致。
- “给我完整旅行计划/航班详情”直接回复 IM，不产生邮件队列任务。
- “请把详情发邮件”只提交一次 email_send，保留失败和 queued 状态语义。
- 四个搜索场景仍遵守同样站点/次数/验证约束；失败不升级为额外浏览或终端操作。
- 普通聊天自然回答；家居只读查询/授权控制、媒体列举/停止、临时目录文件读写可完成。
- 自动测试用隔离邮件队列/模拟家居/媒体替身，避免发送真实邮件或打断生产播放。
- 按仓库规范通过 scripts/run_tests.sh 执行相邻测试；最后由用户做真实 IM 验收。
- 已关闭 background review，不通过本次变更重新启用。

## 8. 回滚与非目标

无数据格式迁移。回滚时停止候选 Gateway，恢复旧 Gateway 镜像与配套配置/SOUL/技能，
使用新会话重新验证；不回放邮件、不复制历史或队列、不清除用户数据。
旧镜像和备份保留至人工验收结束。单独回退镜像而留新 skills_readonly 配置是不完整回滚。

本次文档落盘不包含代码实现、镜像构建、生产配置改写、服务重启或远端设备部署。
