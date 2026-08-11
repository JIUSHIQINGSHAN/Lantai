# 03 - 全量顺序污染排查（test_skill 偶发失败 → 测试进程内调度器线程）

Status: resolved
Type: bug
Blocked by: (none)

## 症状

全量 pytest 偶发 1 例失败：

    tests/test_skill.py::test_propose_from_candidate_persists_structure
    AssertionError: assert '上线部署步骤' == '部署手册'

即 `proposed_patch["structure"]["name"]` 走了兜底 `cand.summary[:40]`，说明
`patch("lantai.evolution.proposer.chat_json", ...)` 未拦截 → 真实 LLM 调用
抛异常 → 进 except 兜底。test_skill.py 单独运行 7/7 通过。

## 排查证据

- 二分定位无法复现：按字母序分批 + test_skill 均通过；加调试日志后再跑 4 轮全量
  （80s/180s/247s）全过，仅一轮出现 test_reflect.py 的 ImportError——那是用户
  并发新增测试所致，非污染。
- 首次失败轮耗时 348s（正常 80–250s）：真实库 memorycandidate 有 57 条 new 候选，
  api_server lifespan 会 start_scheduler()，后台 evolve worker 对真实库逐条做
  真实 LLM 调用——解释拖慢，也是顺序污染源。
- proposer.py 文件 mtime（18:02:31）晚于首次失败（~17:23）：失败时该文件处于
  用户进行中编辑状态，mock 未生效的最可能瞬时原因；当前代码已稳定，之后多轮全量
  均通过。

## 根因修复

11 个测试文件 `from api_server import app`，TestClient 进 lifespan → 真实
BackgroundScheduler 启动（evolve/ingest/forget/candidate_ttl/param_advice/
coalesce），且 `stop_scheduler(wait=False)` 不等待在跑任务，留下僵尸线程继续
对真实库做真实 LLM 调用——污染后续测试、写脏真实库、拖慢全量。

修复：`tests/conftest.py` 新增 autouse fixture `_no_background_scheduler`，
统一置空 `api_server.start_scheduler`，测试进程内永不启动真实调度器。零生产
代码改动；测试只验证 HTTP/业务行为，不依赖调度器。

## 验收（2026-08-11 已通过）

1. ✅ 全量测试下无真实调度器线程：conftest autouse fixture 置空 api_server.start_scheduler，
   12 个 TestClient 用例文件不再启动后台 worker。
2. ✅ test_skill.py 单独（7/7）与全量顺序下均通过。
3. ✅ 全量 518 passed（77s），此前失败轮 348s——不再被真实 LLM 调用拖慢。
4. ✅ 附带修复：test_mcp.py 工具数断言随用户新增工具同步（12→14→15：scene_get/scenes_list、
   recall_report）；db.py v3 迁移（用户 scene 层）在空库上 CREATE INDEX 无表抛错
   中断迁移链，已加 sqlite_master 表存在性防御；api_server.py 移除重复的
   routes_conflicts_router 导入与注册（旧瑕疵）。
5. ✅ 真实库污染审计：修复前各轮测试运行期间（08-11 16:00 后）真实库零新增提案/记忆
   （调度线程未触发 evolve 落库）；真实库 user_version==4 与用户最新代码一致，
   memoryscene/scene_id 齐全。
6. ✅ 最终全量 534 passed（127s）。

## 相关文件

tests/conftest.py（新增 fixture）、lantai/evolution/proposer.py（清理临时调试日志）
