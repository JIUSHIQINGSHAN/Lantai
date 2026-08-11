# 02 技能结晶（SkillCrystallizer 窄版）

借鉴源：aiduMEI `ducky/skill_crystallizer.py`（v17.0 Mímir 铁律：候选 → 人工审核落地）。

## 设计

- `SkillCrystal` 表：id / skill_name(unique) / trigger_rule / procedure /
  source_lanes / sample_keys / hit_count / candidate_count / status
  (candidate|approved|archived) / created_at / updated_at。
- 检测复用 `autodream.cluster_memories`（同 lane + 共享关键词，min_size=3）：
  簇 → 候选 {skill_name, trigger_rule, procedure（步骤摘要不塞全文）, sample_keys,
  lanes, candidate_count}；噪声 lane（general/chat）排除；`run_crystal_detect_once`
  dry-run 或 upsert 落库（skill_name 冲突 hit_count+1，幂等）。
- 裁决 `decide_crystal`：approve 必须带非空 steps（宁 miss 不脏写）→
  `mem_command.create_skill` 落成 Skill 资产 + status=approved；reject → archived + reason。
- settings：CRYSTAL_ENABLED / CRYSTAL_MIN_CLUSTER / CRYSTAL_MAX_DAILY。
- REST：`GET /crystals`、`POST /crystals/detect`、`POST /crystals/{id}/decide`；
  MCP：`crystals_list` / `crystals_detect` / `crystal_decide`。

## 测试

`tests/test_crystals.py`：检测纯函数（聚类/噪声 lane/阈值）+ 真实 SQLite 直调
（detect 落候选、decide approve→skill 资产、decide reject→archived、approve 缺
steps 拒绝），仅 mock embedding/向量存储（create_skill 链路）。

## 状态

resolved（2026-08-12，随 feat(absorb) 提交推送）。
