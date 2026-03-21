## Plan: Wiki 正文并发生成

目标是把当前串行的 Wiki 正文生成改为受控并发执行，并让日志能够清楚反映任务级与章节级进度，同时消除并发场景下本地输出目录、R2 路径和章节 JSON 文件名的覆盖风险。推荐方案是保守固定并发 2-3，采用线程池执行章节生成，文件名尽量保持现有可读性，只在规范化后发生冲突时追加稳定后缀，并把所有本地与远端输出都切到 task_id 级隔离路径。

**Steps**
1. Phase 1 - 现状收口与配置入口
   在 docker/src/core/wiki_pipeline.py 中梳理共享输出路径，把当前全局共享的 wiki_structure.json 与 wiki_section_json 改为任务级输出目录设计，避免不同任务同时运行时互相覆盖。同步确定并发配置入口，优先放入 docker/config/repo_config.json 的新字段中，并由 docker/src/config.py 现有配置加载链路读取，默认值设为 2 或 3。
2. Phase 2 - 任务级输出隔离，先于并发改造
   调整 docker/src/core/wiki_pipeline.py 中 execute_generation_task、run_structure_generation、run_wiki_content_generation 的参数与路径构造方式，使每次任务生成独立的结构文件与章节目录，例如按 task_id 建立专属工作目录。该步骤依赖 Step 1，并阻塞后续所有并发改造，因为不先隔离路径就无法安全并发。
3. Phase 3 - 正文生成器并发化
   在 docker/src/wiki/content_gen.py 中将 WikiContentGenerator.generate 从串行 for 循环改为受控线程池执行，最大并发取 min(配置值, 章节数)。执行模型调用时不要直接复用一个共享客户端实例，应改为每个工作线程独立获取客户端，或在生成器中引入 client_factory 延迟创建，降低 OpenAI/OpenRouter SDK 在多线程复用下的未知风险。该步骤依赖 Step 2。
4. Phase 4 - 章节级日志与进度聚合
   在 docker/src/wiki/content_gen.py 与 docker/src/core/wiki_pipeline.py 中增加并发可观测性：任务开始时打印总章节数与并发上限；每个章节开始时打印 task_id、section_id、标题和当前 active/completed/total；章节结束时打印耗时、结果文件名、累计完成数；章节失败时打印异常与累计失败数。与此同时，把当前固定从 50 直接跳到 85 的正文阶段进度，改为按章节完成比例线性推进到目标区间，保证前端与任务表能反映真实进度。该步骤依赖 Step 3。
5. Phase 5 - 文件名规范化与冲突去重
   在 docker/src/wiki/content_gen.py 中把章节文件名处理改成两段式：先保留现有 safe filename 规则生成基础名，再在单线程预处理中建立规范化名称映射，对重复名称追加稳定后缀，例如 -2、-3。这样既保持现有可读性，也避免 API/AI 产生相近 section_id 时在并发写盘时互相覆盖。需要同时覆盖空字符串、全非法字符、大小写折叠和特殊符号折叠后的碰撞情形。该步骤可与 Step 4 并行设计，但落地时建议在 Step 3 后一起实现。
6. Phase 6 - 远端路径与仓库路径并发安全
   更新 docker/src/storage/r2_client.py 与 docker/scripts/setup_repository.py、docker/src/utils/repo_utils.py 中的命名规则，避免并发任务落到同一天同仓库同前缀路径下。推荐把 R2 base path 从 repo_name/date 升级为 repo_hash/task_id 或 repo_hash/timestamp_taskid；本地克隆目录继续使用仓库标识，但要避免不同任务针对同一仓库并发时直接删掉彼此目录，必要时改为 task_id 级 clone 目录或先明确同仓库串行化策略。该步骤与 Step 3、Step 5 有耦合，建议在正文并发化之后立即完成。
7. Phase 7 - 回归与补充测试
   为 docker/src/wiki/content_gen.py 增加针对文件名规范化和重复去重的单元测试；为正文生成流程补一个使用 mock client 的并发生成测试，验证输出文件数、输出文件名唯一性、失败章节不影响其他章节、进度日志与结果收集行为。最后验证 cleanup_local_files 只清理本任务目录，不误删其他任务产物。该步骤依赖前面所有改造完成。

**Relevant files**
- /Users/rainyfan/Documents/GitHub/FAN-Tianrui-FYP/docker/src/core/wiki_pipeline.py — 核心任务编排入口，当前共享 DEFAULT_OUTPUT_PATH 与 DEFAULT_WIKI_SECTION_JSON_OUTPUT，需改为 task_id 级工作目录，并在正文生成阶段接入细粒度进度更新。
- /Users/rainyfan/Documents/GitHub/FAN-Tianrui-FYP/docker/src/wiki/content_gen.py — 正文生成核心，当前 generate 串行调用 _generate_section，且 _write_section_json 只按 safe filename 落盘，是并发、日志和文件名去重的主修改点。
- /Users/rainyfan/Documents/GitHub/FAN-Tianrui-FYP/docker/src/clients/ai_client_factory.py — 并发下客户端创建策略建议统一从这里收口，避免线程间直接复用同一 client 实例。
- /Users/rainyfan/Documents/GitHub/FAN-Tianrui-FYP/docker/src/clients/openrouter_client.py — 需要确认多线程使用时的实例边界，并按计划改成每 worker 独立实例的安全模式。
- /Users/rainyfan/Documents/GitHub/FAN-Tianrui-FYP/docker/src/storage/r2_client.py — 当前 base path 为 repo_name/date，存在同日并发覆盖风险，需要引入 repo_hash 与 task_id 或更细时间戳。
- /Users/rainyfan/Documents/GitHub/FAN-Tianrui-FYP/docker/scripts/setup_repository.py — 当前本地仓库目录名为 repo_name + 8 位 hash，且同名目录存在时会直接删除，需评估并发同仓任务的目录冲突。
- /Users/rainyfan/Documents/GitHub/FAN-Tianrui-FYP/docker/src/utils/repo_utils.py — 若统一使用 repo_hash 作为远端命名空间，应与 setup_repository 的哈希策略保持一致。
- /Users/rainyfan/Documents/GitHub/FAN-Tianrui-FYP/docker/config/repo_config.json — 推荐新增正文生成并发配置项和必要的输出命名策略开关。
- /Users/rainyfan/Documents/GitHub/FAN-Tianrui-FYP/docker/src/wiki/struct_gen.py — 当前只对 toc 节点做 strip，不保证 section_id 在规范化后唯一；需要作为文件名冲突来源参考。

**Verification**
1. 使用 mock AI client 运行正文生成流程，验证 10 个章节在并发 2-3 下全部产出且文件名唯一，没有覆盖丢失。
2. 人工构造 section_id 冲突样例，例如 API Reference、api-reference、API_reference，验证最终文件名保持可读且稳定去重。
3. 人工并发启动两个生成任务，验证本地输出目录、R2 key、cleanup 行为互不干扰。
4. 检查任务日志，确认能看到正文阶段总章节数、并发上限、章节开始、章节结束、累计完成数和失败信息。
5. 检查 Supabase 任务进度，确认正文阶段从 50 到 85 会随着章节完成持续推进，而不是一次性跳变。
6. 如果可运行集成流程，使用真实仓库执行一次端到端生成，确认 R2 上传后的 content_urls 数量与本地产出一致。

**Decisions**
- 并发策略采用保守固定并发 2-3，不做激进吞吐优化。
- 文件名优先保持现有 section_id 的可读语义，只在规范化后冲突时追加稳定后缀。
- 日志粒度采用任务级汇总加章节开始/结束，不默认引入重试级别日志。
- 本次范围包含正文生成并发、日志可观测性、文件名与路径并发安全。
- 本次范围暂不包含 RAG 索引并发优化、前端展示改造、任务重试机制重构。

**Further Considerations**
1. 如果后续发现 OpenRouter SDK 单实例在多线程下不稳定，应直接切换为 worker 内创建 client，而不是为共享 client 加锁，因为加锁会抵消正文并发收益。
2. 如果同一仓库 URL 可能被高频重复触发，建议把本地克隆目录也切到 task_id 级，避免 setup_repository 先删目录再 clone 的行为影响正在运行的旧任务。
3. 如果需要后续观察正文瓶颈，可以在章节结束日志中附加上下文长度与 LLM 耗时，但这一项可以作为第二阶段优化，不必阻塞首版并发落地。
