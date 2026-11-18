# 分层阅读改写台

分层阅读改写台用于处理本地合成教研文本：在降低句式难度的同时，保证历史人物、时间关系与学科术语在简化后仍然指向同一件事。系统把**原文材料、术语表与分层文本**三者关联保存，让教师能按年级查看改写内容及其依据，并在原文更新后定位需要重新复核的层级。

## 环境

需要 Python 3.11 或更新版本。应用运行不依赖第三方包；运行测试和构建前，安装固定版本的开发工具：`python -m pip install -r requirements-dev.txt`。

## 数据模型与关联

- **原文记录（records）**：导入时把正文切分为带稳定 ID 的句子（`s1`、`s2`…），每句保存内容指纹（哈希）。再次导入修订版时做句子级对齐，区分 `unchanged / moved / changed / added / removed`，变更清单保存在 `pending_changes`。
- **术语表（glossary）**：术语分 `person`（历史人物）、`time`（时间关系）、`domain`（学科术语）三类，支持别名。术语表带整数版本号；只有内容真正变化才升版本，重复导入不会产生新版本。
- **分层改写（adaptations）**：一份原文可对多个年级（`grade-3 / grade-5 / grade-7`）生成改写。每个片段（句子 × 年级）保存：
  - 改写文本与来源标记：`auto`（批处理生成）或 `teacher`（教师手工修订）；
  - 原句快照（句子 ID、原文、原文哈希）作为事实依据；
  - 术语命中清单 `mentions`，逐条标注 `provenance = source`（来自原句）或 `teacher`（教师新增），以及生成时使用的术语表版本；
  - 确认状态 `confirmed` 与操作历史 `history`。

## 三项核心保证

1. **术语表更新只影响后续处理**：已生成片段继续保留当时的术语表版本，状态视图以 `glossary_drift` 提示版本漂移；执行重新处理后才采用新版本。
2. **教师确认过的表达不会被批处理静默覆盖**：`reprocess` 自动跳过 `confirmed` 片段并在 `skipped_confirmed` 中列出；原句被删除时无法自动重建的片段进入 `blocked_missing`，交由教师处理。
3. **原文事实变动必然暴露过期状态**：片段状态依据原句哈希动态计算——原句修改或移除时相关片段变为 `stale` 并给出原因；未受影响的句子和年级保持 `current`，仍可继续使用。仅位置移动（`moved`）不影响有效性。

## 运行（演练数据）

```bash
export PYTHONPATH=src
# 1. 导入术语表与原文，按三个年级生成改写
python -m nlp_reading_level_adapter.cli glossary-import fixtures/glossary.json
python -m nlp_reading_level_adapter.cli ingest fixtures/lesson_v1.json
python -m nlp_reading_level_adapter.cli adapt reading-level-adapter-lesson-history grade-3 grade-5 grade-7

# 2. 教师手工修订并确认（确认后批处理不得覆盖）
python -m nlp_reading_level_adapter.cli revise reading-level-adapter-lesson-history grade-3 grade-5 grade-7 \
    --sentence s1 --grade grade-3 --text "公元前138年。张骞奉命出使西域。" --reviewer 王老师
python -m nlp_reading_level_adapter.cli confirm reading-level-adapter-lesson-history grade-3 grade-5 grade-7 \
    --sentence s1 --grade grade-3 --reviewer 王老师

# 3. 原文更新（第一句事实变动），定位受影响的原句与年级
python -m nlp_reading_level_adapter.cli ingest fixtures/lesson_v2.json
python -m nlp_reading_level_adapter.cli changes reading-level-adapter-lesson-history grade-3 grade-5 grade-7

# 4. 按年级查看状态；批处理重建（已确认片段自动跳过）
python -m nlp_reading_level_adapter.cli view reading-level-adapter-lesson-history grade-3 grade-5 grade-7
python -m nlp_reading_level_adapter.cli reprocess reading-level-adapter-lesson-history grade-3 grade-5 grade-7

# 5. 教师复核后解除过期、清除变更清单
python -m nlp_reading_level_adapter.cli resolve reading-level-adapter-lesson-history grade-3 grade-5 grade-7 \
    --sentence s1 --grade grade-3 --reviewer 王老师 --note "已核对时间与人名"
python -m nlp_reading_level_adapter.cli clear-changes reading-level-adapter-lesson-history
```

运行数据保存在 `var/records.json`（可通过 `--state` 指定其他路径），所有写入采用临时文件原子替换。

## 简化规则

简化是确定性的、不依赖第三方模型：按年级配置替换书面连接词（如「因此→所以」）并按长度阈值把长句拆成短句；术语片段按字符位置受保护、逐字保留，因此历史人物、时间与学科术语不会被改写或拆散。教师修订若丢失原句术语或引入原句没有的术语，片段会产生保全告警（`warnings`），提示人工核对。

## 测试

执行 `python -m pytest -q` 检查文本规范化与术语保护、句子对齐与稳定 ID、术语表版本化、教师修订溯源与确认保护、原文更新后的过期暴露与变更定位，以及本地持久化。
