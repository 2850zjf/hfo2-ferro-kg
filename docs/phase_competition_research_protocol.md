# HfO2/HZO 跨文献相竞争研究协议（第一阶段）

版本：v0.1（候选发现与协议冻结前）  
状态：研究脚手架；不得将候选、模型结果或计划计算写成已验证结论

## 1. 核心问题与主任务

研究问题是：为什么名义相似的 HfO2/HZO 样品会报告不同的 monoclinic（m）、orthorhombic（o）和 tetragonal（t）相结构，以及不同的铁电结果？

主任务按优先级固定为：

1. 样品级相结构结果识别与证据追溯；
2. 跨文献相竞争边界建模；
3. 证据一致性判别；
4. Pr/2Pr 仅作为相结果的下游辅助目标，不再作为第一主任务。

任何“矛盾”首先是待复核候选。不能仅因两篇文章相标签不同就认定物理矛盾。

## 2. 第一阶段现状审计

### 2.1 Ontology

现有本体已有 `ThinFilmSample`、`PhaseStructure`、`Evidence`、`ComputationalObservation` 和 `CONTRADICTS`，并覆盖 composition、厚度、退火、电极、strain、phase fraction、表征方法和计算 reference state。缺口是：

- `CONTRADICTS` 只有关系名，没有矛盾类型、比较前提、裁决状态和两侧条件差异；
- PhaseStructure 缺少 dominant/minority/local/interfacial 等观测尺度、定量不确定度和检测限；
- 电学测量状态缺少统一字段，如 pristine/wake-up/fatigued、循环数、测量温度/频率/场强；
- 缺少显式 `SampleComparison` / `EvidenceConsistencyAssessment` 中间实体。

本阶段不直接修改生产本体。下一版候选扩展应包含 `comparison_id`、`outcome_axis=phase_structure`、`classification`、`matched_conditions`、`differing_conditions`、`evidence_pair`、`adjudication_status` 和 `uncertainty_notes`。

### 2.2 Benchmark 与验证

现有 benchmark 主要是 Pr/2Pr，tier 规则强调样品链接和证据，但没有相结构分类/多标签任务，也没有已冻结 DOI 验证清单。模型代码可做 paper-group holdout/K-fold，但 DOI 不存在时退回 paper_id，组数不足时甚至退回 row split；这不满足最终发布协议。

本研究要求：

- group key 优先使用规范化 DOI，否则使用稳定 paper_id；
- 先人工冻结 `locked_validation` DOI/论文清单，再开始训练与阈值调优；
- 同一 group 的全部样品、图表、段落和重复 PDF 只能在一个 partition；
- 最终指标禁止 row-split fallback；组数不足时应停止并报告数据不足；
- 相任务报告 macro-F1、每类 precision/recall、mixed-phase 多标签指标和 calibration；一致性任务单独报告三分类混淆矩阵；Pr/2Pr 只作辅助回归指标。

### 2.3 Sample linking

现有 `sample_linker-v0.1` 保留了证据和较多工艺字段，但其样品签名包含 phase，因此结果变量会参与 `sample_id`。这会让相不同但条件相同的记录天然得到不同 ID，也会使跨文献直接按 sample_id 比较失效。此外，稀疏条件记录可能被错误合并或产生名义相似假象。

本脚手架因此不使用 `sample_id` 作为相矛盾判据，而是重新比较不含 phase/property 的条件字段，并同时输出 `nominal_similarity` 与 `comparable_coverage`。低覆盖不能被高相似度掩盖。

### 2.4 Physical constraints

现有约束覆盖相先验、氧空位非单调效应、界面和证据完整性，但目前主要是设计推荐打分；固定的 phase prior 不能作为跨文献真值，也不能预先把 m/t 报告判错。候选裁决必须回到原文的相表征范围、空间尺度和样品状态。

### 2.5 Model validation

现有模型已统计 group overlap，是正确基础；不足是验证集非锁定、主目标仍为 Pr/2Pr、组不足时允许 row fallback。相竞争模型应先做可解释基线（规则、multinomial/logistic 或树模型），在样品数和论文数足够后再考虑复杂模型。候选发现阈值只能在训练集上调整。

### 2.6 Computation workflow

现有 workflow 能准备 HfO2 多相 VASP 包、导入相对能量并保留收敛状态，但相关函数会写项目数据库。因此本阶段只审计、不调用生产库写入路径。计算结果导入还需增加跨应变点的一致 k-point density、相同 POTCAR/functional、应力收敛、体积/公式单元归一化、结构未坍塌到其他相等质量门槛。

## 3. 候选矛盾的操作定义

输入单元是带 DOI/论文、PDF、页码或 chunk、原文证据、材料、样品条件、相结果和表征方法的 `PhaseObservation`。

候选仅比较不同 DOI/论文 group，且只考虑 HfO2/HZO 的 o/t/m 结果。相集合不相交才算 outcome conflict，例如 `{o}` 对 `{m}`；`{o,t}` 对 `{t}` 不算。`ferroelectric`、`polar phase` 等无法唯一映射到 o/t/m 的标签不能自动进入科学矛盾类。

三类输出为：

- `scientific_contradiction_candidate`：记录条件高度相似、覆盖达到门槛、两侧证据可追溯、相结果不兼容，且当前字段没有已知的表征或状态差异。它仍需人工裁决。
- `condition_explained_difference`：存在相冲突，但有明确的厚度/退火/电极/应变等条件差异，或 XRD/TEM 等表征尺度差异、pristine/cycled 状态差异。
- `extraction_review_needed`：缺 evidence、缺 locator、上下文弱、相标签无法解析，或后续人工发现样品绑定错误。先修抽取，不进入科学统计。

所有输出必须包含：两侧 group、相标签、相似度、可比字段覆盖率、差异字段、分类理由、页码/chunk 和 evidence text。

## 4. 数据切分与标注协议

1. 先按 DOI 去重；无 DOI 的论文使用稳定 paper_id，并人工检查重复题名/PDF。
2. 在不知道最终标签分布和模型结果的前提下，按材料族、年份、器件形态和数据来源分层选择锁定验证论文。
3. 将 DOI/论文清单版本化；脚手架只接受人工提供的 locked list，不自动挑选。
4. 双人独立标注相结果、条件可比性与一致性类别；分歧由第三人或共识会议裁决。
5. 锁定集仅用于最终一次主结果评估；开发期误差分析不得反向调阈值后继续宣称同一锁定集。

最小标注字段：material/composition、film thickness、deposition、substrate/electrodes/stack、anneal、strain、phase label/fraction、characterization method/scope、measurement/cycling state、Pr/2Pr（如有）、evidence locator、primary/secondary source、annotator、adjudication。

## 5. 单一可计算机制：面内应变

本研究只选择“面内双轴应变如何改变 o/t/m 相对稳定性”作为第一性原理机制。原因是应变直接对应相竞争边界，现有本体已有 `strain_state`，且比带电氧空位的化学势、charge correction 和构型枚举更适合作为第一条可复现路径。

最小计算矩阵：同一理论设置下，对 m、t 和候选极性 o-HfO2 结构施加一组预注册的面内应变点，弛豫允许的晶格自由度与离子位置，计算每公式单元能量并报告 `ΔE_o-m(ε)`、`ΔE_t-m(ε)`、`ΔE_o-t(ε)`。只有 HfO2 smoke test 通过后，才讨论 HZO；HZO 需要先冻结有序构型或 SQS/枚举协议，不能用随意 Hf→Zr 替换。

VASP 结果只能支持“在指定 0 K、泛函、结构与边界条件下的相对稳定性趋势”。它不能直接验证具体退火温度、时间、相分数或实验 Pr/2Pr，也不能把动力学路径等同于平衡能量。

## 6. 第一阶段脚手架边界

`pipelines/62_analyze_phase_contradictions.py`：

- 使用 SQLite URI `mode=ro&immutable=1` 与 `PRAGMA query_only=ON`；
- 不调用会初始化表或记录 pipeline run 的项目数据库连接；
- 只向用户指定的 worktree 输出目录写 JSONL/CSV/summary；
- 不读取 `.env`，不调用 LLM，不运行或提交 VASP；
- 输出是人工复核队列，不是论文结论。

示例（输出目录必须位于本研究 worktree）：

```bash
python pipelines/62_analyze_phase_contradictions.py \
  --db-path /path/to/read-only-snapshot.sqlite3 \
  --output-dir reports/phase_contradiction_candidates \
  --locked-validation-groups annotations/locked_validation_dois.txt
```

## 7. 下一阶段决策门槛

只有同时满足以下门槛才进入模型训练：

- 至少 30 个独立 DOI/论文 group 有人工确认的样品级相标签；
- o/t/m 每个主类在训练集至少 15 个独立 paper groups，或明确降级为探索性分析；
- 至少 80% 记录有页码/asset/chunk 与可复核 evidence；
- 抽样审计的样品链接 precision ≥ 0.90，相标签 precision ≥ 0.90；
- 双标注一致性 Cohen's κ ≥ 0.80，未达标先修指南；
- locked validation 清单已冻结且 group overlap = 0；
- 不允许 row-split fallback。

只有同时满足以下门槛才进入应变计算主矩阵：

- m/o/t 三个起始结构来源和空间群已冻结并可追溯；
- 同设置零应变 smoke test 全部电子/离子收敛，能量归一化和 k-point density 检查通过；
- 应变定义、约束自由度、应变点和结构相变判据已预注册；
- 计算任务仅解释相对能量趋势，不承诺验证退火工艺。

若生产库候选经人工抽查后大多数属于抽取错误或条件缺失，则下一阶段优先修 ontology/extraction/sample linking，而不是训练模型或扩大 VASP 计算。

## 8. 第二阶段只读修复层

`phase-contradiction-candidates-v0.2` 增加两个不回写生产库的中间产物：

- `phase_observation_annotation_queue.csv`：同论文同 chunk 同相去重的人工标注任务；优先保留当前 evidence 直接相证据，其次为同 chunk 恢复句。计算、综述、多相塌缩和无直接支持条目保留质量标记并降权。
- `paper_group_inventory.csv`：按 DOI/paper_id 汇总材料族、o/t/m 数量、直接/恢复证据数量与质量门槛通过数；partition 固定为 `unassigned`，必须由人工分层选择验证论文。

缺失样品条件只输出 `candidate_needs_human_review` 建议，不修改当前条件。恢复证据只允许来自同一 chunk，不能跨页面或跨论文拼接。

## 9. 第三阶段 benchmark 就绪门

`pipelines/63_build_phase_benchmark.py` 只接受 `adjudicated`、`primary_experiment`、人工确认 o/t/m 标签与 observation scope、结构化 `review_sample_conditions`、annotator 和可定位相证据。任何一项缺失都会进入 exclusions，不会用自动标签代替人工金标准。

没有锁定 DOI/论文清单时，所有 accepted 行保持 `unassigned`，readiness 必须为 `not_ready`。`locked_validation_selection_frame.csv` 只提供材料/相/质量分层信息，`selected_partition` 留空，由人工选择。只有总论文组、训练集每相论文组、锁定集每相论文组和零泄漏门槛全部满足，才可返回 `ready_for_training`。该流程只冻结协议，不计算模型指标。

独立本体提案位于 `ontology/phase_competition_extension.yaml`，在人工评审前不合并生产本体。

## 10. 统一工作流入口

`pipelines/64_run_phase_competition_workflow.py` 串联只读候选分析、人工标注队列生成和 benchmark readiness。自动阶段正常完成但尚无人工裁决时，状态必须是 `completed_waiting_for_human_annotation`；这表示工作流可运行，不表示 benchmark、模型或科学结论已经完成。

```bash
python pipelines/64_run_phase_competition_workflow.py \
  --db-path /path/to/hfo2_ferrokg.sqlite3 \
  --output-dir reports/phase_competition_workflow_run
```

人工标注和锁定 DOI 清单完成后，使用 `--annotations` 与 `--locked-validation-groups` 重跑；需要在自动任务中严格阻止未就绪状态时再加 `--require-ready`。该入口不读取 API key、不调用 LLM、不训练模型、不运行 VASP，且强制输出目录位于研究 worktree。

## 11. 可选阿里云机器预标注层

`pipelines/65_preannotate_phase_queue.py` 是与统一只读工作流隔离的可选辅助阶段。它只读取已经导出的 `phase_observation_annotation_queue.csv`，在运行时从用户提供的 DOCX 读取阿里云百炼兼容端点、模型名和 API key，并把机器建议写入独立 sidecar。API key 不写入源码、`.env`、输出文件、summary 或日志；端点必须是 HTTPS 且 host 固定为 `dashscope.aliyuncs.com`，HTTP 重定向与系统代理一律禁用，响应上限为 256 KB。模型只允许当前已验证的 `qwen3.7-max` 或 `qwen3.8-max`，pilot 默认零重试，发给模型的内容不含内部 annotation ID。DOCX 本身仍是明文凭据载体，只适合一次性迁移；若包含多个不同 key，程序必须停止，不能猜选。

机器预标注必须遵守以下硬门槛：

- sidecar 中不出现 `review_*`、`adjudication_status`、`annotator` 或 `review_notes`；
- 不把 Pr/2Pr、回线或单独的“ferroelectric”表述反推为 o 相；
- supporting quote 必须能在本条输入 evidence 中逐字定位，否则自动降级为 `abstained`；
- 输入中的论文文本按不可信数据处理，不能覆盖系统规则或指挥模型执行任务；
- DOI、标题、自动相标签、priority、quality flags 和条件建议不发送给模型，重复 evidence 先去重；
- 输出即使传给 benchmark，也因没有 `adjudicated` 人工状态而被排除；
- 先做单次连通性测试和小批量 pilot，再由人工抽查决定是否扩大，不能把机器置信度写成已校准指标。

示例：

```bash
python pipelines/65_preannotate_phase_queue.py \
  --input-queue reports/phase_competition_workflow_run/01_candidate_analysis/phase_observation_annotation_queue.csv \
  --output-dir reports/phase_llm_preannotation_pilot \
  --credential-docx /path/to/runtime_config.docx \
  --max-rows 5
```

该阶段不访问生产数据库，不改变统一工作流原有的 `api_credentials_used=false` / `llm_invoked=false` 审计语义；它是后置、显式且可删除的机器辅助产物。

## 12. 计算机制与真实结果门禁

本分支只选择一个可计算机制：HfO2 m/o/t 相在共同 `(001)` 方形面内约束下的双轴应变响应。HZO、氧空位、界面、有限温度和相场模拟均不在首轮主矩阵内，避免同时增加不可辨识变量。

### 12.1 已完成的真实原始输出审计

`pipelines/66_audit_vasp_raw_outputs.py` 只读解析 2026-06-22 TEFS/VASP smoke run 的 POSCAR、CONTCAR、OUTCAR、vasprun.xml 和 relax.output，记录 SHA-256；`phase_energy_summary.csv` 只能交叉核对，不能作为能量权威。临时独立 spglib 2.7.0 检查确认最终结构为 m-P2_1/c #14、o-Pca2_1 #29、t-P4_2/nmc #137、c-Fm-3m #225。

真实零应变静态值为：

| phase | E (eV/f.u.) | relative to m (meV/f.u.) | static max force (eV/A) |
|---|---:|---:|---:|
| m | -30.517668955 | 0.000 | 0.014675 |
| o | -30.433368453 | 84.301 | 0.013491 |
| t | -30.351337135 | 166.332 | 0.007980 |
| c | -30.248300563 | 269.368 | 0.000000 |

这些值的状态是 `audited_with_publication_blockers`，不是 publication-grade。阻断项包括：没有 ENCUT/k-point density 收敛序列、没有重复计算或数值不确定度、没有 POTCAR 二进制哈希、没有保存 relax 阶段 OUTCAR/vasprun.xml，以及尚无真实双轴应变矩阵。m/o 的静态最大力也未达到下一阶段建议的 `<=0.01 eV/A` 门槛。

### 12.2 已生成但未执行的应变 pilot

`pipelines/67_prepare_phase_strain_jobs.py` 已生成 9 个无 POTCAR、无执行脚本、无提交脚本的 dry-run 目录。三相统一为 Hf4O8 12 原子；t 相使用整数矩阵 `[[1,1,0],[-1,1,0],[0,0,1]]`。共同实际面内基准为 `L_ref = 5.075232324868899 A`，三点为 `L_ref x {0.99, 1.00, 1.01}`。当前 9 个 POSCAR 的 hash 已冻结，独立 spglib 预检保持各自目标空间群。

当前 `ISIF=2` 只固定整个 cell 并弛豫离子，因此它只是几何和输入链路 pilot。发表级外延能量必须改为：固定两个面内晶格矢量，同时弛豫离子、面外长度及允许的面外剪切；在 VASP 6.3.0 上需经验证的外部 constrained-cell optimizer 或显式面外搜索，不能用 `ISIF=3` 释放面内约束。

### 12.3 运行与结果接纳规则

- 本机没有 VASP、MPI、scheduler、TEFS CLI、SSH identity 或已验证的远程会话；历史 TEFS 作业只能证明旧链路曾运行，不能证明当前授权、余额、镜像或价格。
- 任何 TEFS 上传或付费提交前，必须由用户本人登录并确认项目、VASP/POTCAR 授权、实时单价、预算上限、walltime、spot 中断策略、自动终止和结果回收。
- 首次重新执行只放行一个 preflight job；确认输出、自动终止和费用后，才决定是否放行其余 8 个。
- 每个结果必须从原始 OUTCAR/vasprun.xml 解析，并保存 input/output hash、VASP/POTCAR 元数据、力/应力、电子/离子收敛与最终空间群。
- 若目标相转变，标记 `phase_transformed`，不得继续作为该相的能量点。
- 不同共同面内长度必须分别归一化；不能跨机械边界用全局最低能量计算相对值。
- 人工填写 CSV 永远只能是 `user_reported_unverified`；测试 fixture 永远只能是 `synthetic_fixture`，两者都不能成为科研证据。
- 全阶段不写生产数据库；通过人工和数值门禁前只生成 worktree 文件级产物。
