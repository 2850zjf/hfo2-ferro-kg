# HfO2-FerroKG 论文级工作流与计算闭环计划

## 论文主线

本项目的论文主线收敛为一个问题：

> 如何把 HfO2/HZO/doped HfO2 文献转化为样品级、证据可追溯、可建模、可计算反馈的材料设计知识图谱？

第一版不扩展到 BaTiO3、PZT、BiFeO3 等其他体系。论文结果以 HfO2/HZO/doped HfO2 的 Pr/2Pr 为主线，其他性能作为补充分析。

核心贡献建议写成四个层级：

1. **Ontology-first extraction**：用领域本体约束材料、工艺、相结构、器件和性能抽取。
2. **Sample-property linking**：把性能值绑定到具体样品、工艺、结构和证据页码。
3. **Tiered Pr/2Pr benchmark**：构建 `strong_only`、`strong_partial`、`all_traceable` 三层 benchmark，并使用验证集报告模型性能。
4. **Evidence-to-computation feedback**：把文献驱动的候选设计转化为可审查的 DFT/相场/MD/ML 势任务，再把计算 descriptors 回写到 KG 和 benchmark。

## 总体工作流

```text
HfO2/HZO PDF corpus
-> scope lock and strong relevance screening
-> page/table/caption/chunk parsing
-> ontology-first extraction
-> sample-property linking
-> AI audit and manual gold-set review
-> evidence KG and design KG
-> tiered Pr/2Pr benchmark
-> train/validation predictive model comparison
-> evidence-constrained design recommendations
-> computational feedback task planning
-> cloud calculation after manual approval
-> computed descriptors write back to KG/benchmark
```

## 计算闭环的位置

计算闭环不是替代实验，也不是替代文献证据。它位于设计建议之后，作为物理可行性反馈层。

输入：

- KG/RAG 给出的相似文献证据。
- Pr/2Pr benchmark 模型给出的预测值和不确定性。
- 候选材料、厚度、退火、电极、相结构、器件类型。

输出：

- 相稳定性 descriptors。
- 氧空位形成能、迁移能或风险评分。
- 电极/界面影响 descriptors。
- 厚度和边界条件下的相场或开关趋势。
- 退火路径、缺陷扩散或相转变风险。

回写：

- KG 中新增 `ComputationalTask`、`ComputedDescriptor`、`RiskAssessment`、`DesignConstraint`。
- benchmark 中新增可训练特征，如 `deltaE_o_m_meV_fu`、`oxygen_vacancy_formation_energy_eV`、`interface_energy_proxy`、`phase_field_pr_trend`。

## 计算任务类型

| 任务 | 推荐引擎 | 解决的问题 | 回写字段 |
| --- | --- | --- | --- |
| Phase stability | VASP/DFT | 正交相是否比单斜/四方相更有利 | `deltaE_o_m_meV_fu`, `deltaE_o_t_meV_fu`, `computed_polarization_uC_cm2` |
| Oxygen vacancy | VASP/DFT, NEB optional | 氧空位是否促进相稳定或带来 leakage/fatigue 风险 | `oxygen_vacancy_formation_energy_eV`, `oxygen_vacancy_migration_barrier_eV`, `defect_risk_score` |
| Interface screening | DFT slab or surrogate descriptors | 电极是否影响氧空位、界面能和相稳定 | `interface_energy_proxy`, `oxygen_scavenging_risk_score` |
| Phase-field switching | Phase-field or compact model | 厚度、边界条件和相比例对 Pr/2Pr 的趋势影响 | `phase_field_pr_trend`, `domain_fraction_proxy`, `computed_Ec_trend` |
| Annealing dynamics | ML potential MD or kinetic surrogate | 退火路径、缺陷迁移和相转变风险 | `oxygen_diffusion_proxy`, `phase_conversion_risk_score` |

第一版先生成计算任务清单，不自动提交云端任务。所有任务必须人工确认结构、赝势、泛函、收敛参数、队列和费用后再运行。

第一个可开展的轻量计算是 Materials Project 来源的 HfO2 多相稳定性 smoke test。它只检查计算环境和 HfO2 polymorph relative-energy ranking，不直接声称预测实验 Pr/2Pr。

```bash
python computations/mp_hfo2_phase_smoke_test/fetch_mp_structures.py \
  --output-dir data/computation/mp_hfo2_phase_smoke_test \
  --max-energy-above-hull 0.35
```

该脚本只从 Materials Project API 获取结构，记录 MP material id 和 symmetry 信息，不手造 POSCAR，不提交 POTCAR。

## 腾讯云接入边界

安全原则：

- 本地代码只生成计算任务计划和输入意图。
- 不在 Git 中保存 SSH key、云密钥、API key、服务器密码或作业系统 token。
- 云端提交脚本后续只读取本机环境变量或用户手动配置的 SSH agent。
- 默认不在本机运行重计算，不影响电脑正常使用。

后续需要确认：

- 云服务器系统、CPU/GPU/内存、磁盘和队列系统。
- VASP 许可证和可执行文件路径。
- Python/conda 环境、ASE/pymatgen/atomate/fireworks/custodian 是否可用。
- 是否已有 ML potential 或需要先从文献/开源势开始筛选。
- 结果文件回传方式：`rsync`、SCP、对象存储或 Git ignored data 目录。

## 论文评测计划

### 数据质量评测

- 30 篇 gold set，覆盖 HZO、pure HfO2、doped HfO2、FeCAP、FeFET、FTJ。
- 标注字段：材料体系、厚度、沉积方法、退火温度/时间/气氛、电极 stack、相结构、Pr、2Pr、证据页码。
- 指标：extraction precision/recall/F1、sample-property linking accuracy、Pr/2Pr confusion rate、unit normalization error rate。

### Benchmark 评测

- 主数据表：`sample_property_links`。
- 主目标：`remanent_polarization_Pr`、`double_remanent_polarization_2Pr`。
- 主报：`strong_only`，敏感性分析：`strong_partial`。
- 模型：RandomForest、ExtraTrees、GradientBoosting、Ridge、ElasticNet、SVR-RBF。
- 指标：验证集 MAE、RMSE、R2、within 5 μC/cm²、within 10 μC/cm²、相对训练集均值 baseline 的 improvement。

### 计算反馈评测

第一版不声称计算能直接预测实验 Pr/2Pr。它只评测：

- 文献候选能否被自动转成明确计算任务。
- 每个计算任务是否有结构假设、输入要求、预期输出和回写字段。
- 计算 descriptors 是否能作为下一版模型特征。

## 立即执行计划

1. 固化当前数据快照和强相关 Pr/2Pr 切片。
2. 完成 30 篇 gold set 模板与人工标注入口。
3. 对 136 篇强相关论文队列做高价值 chunk 复抽取。
4. 重建 `strong_only` 和 `strong_partial` benchmark。
5. 在固定验证集上重跑六类通用模型。
6. 生成证据约束设计建议。
7. 生成计算反馈任务清单。
8. 把结果整理成论文图、表和进展 PPT。

## 对应命令

```bash
python3 pipelines/10_validate_results.py
python3 pipelines/21_build_design_dataset.py
python3 pipelines/29_build_benchmark_tiers.py
python3 pipelines/30_train_tiered_design_models.py --targets remanent_polarization_Pr,double_remanent_polarization_2Pr
python3 pipelines/33_filter_and_compare_models.py --min-rows 30
python3 pipelines/25_recommend_active_learning.py --target double_remanent_polarization_2Pr
python3 pipelines/34_plan_computational_feedback.py --max-candidates 20 --max-tasks 80
```

`34_plan_computational_feedback.py` 只生成计划，不启动本地或云端计算。
