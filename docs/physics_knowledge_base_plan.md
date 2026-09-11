# HfO2-FerroKG 物理知识库与交叉验证层计划

## 目标

把 HfO2/HZO 铁电文献抽取从“文本信息抽取”升级为“物理约束下的证据抽取与设计验证”。

知识库只保存可审计的物理规则、变量、边界条件、出处链接和使用场景，不复制受版权保护教材正文。

## 物理知识库来源层

第一层：教材级通用物理

- 铁电相变与 Landau-Devonshire 自由能：极化、相变、畴、能垒、滞回。
- 电介质与界面电学：退极化场、电极屏蔽、dead layer、界面电荷、漏电。
- 缺陷化学：氧空位、氧化还原、化学势、补偿电荷、疲劳和 imprint。
- 计算材料学：DFT 相稳定、Berry-phase 极化、收敛性、赝势、k 点、能量/力/应力门槛。

第二层：HfO2/HZO 领域综述和代表性论文

- HfO2/HZO 中 orthorhombic/polar phase 的稳定机制。
- 掺杂、Zr 比例、晶粒、厚度、退火和应力对相结构的影响。
- TiN、W、Pt、RuO2、ITO、LSMO 等电极/界面对氧空位和可靠性的影响。
- Pr/2Pr、Ec、endurance、retention、leakage、wake-up/fatigue 的证据标准。

第三层：项目内计算反馈

- Materials Project 来源结构优先用于初始结构。
- TEFS/VASP 输出只作为物理可行性 descriptor，不作为实验真值。
- 计算结果必须带质量门槛：能量、力、应力、收敛、结构来源、计算参数。

## 进入知识库的形式

1. 本体字段

新增或强化：

- `phase_name`, `space_group`, `phase_fraction`, `strain_state`
- `oxygen_vacancy_context`, `oxygen_reservoir`, `oxygen_partial_pressure`
- `top_electrode`, `bottom_electrode`, `interface_layer`, `interface_termination`
- `measurement_state`, `cycle_number`, `wake_up_or_endurance_state`
- `structure_source`, `calculation_quality`, `computed_descriptor`

2. 物理边界条件

在 `ontology/physical_constraints.yaml` 中维护：

- 性能硬边界：Pr、2Pr、Ec、endurance、retention、leakage。
- 工艺窗口：厚度、退火温度、退火时间、Zr fraction。
- 相结构先验：orthorhombic/rhombohedral/mixed/tetragonal/monoclinic/amorphous。
- 氧空位非单调规则：适量稳定极性相，过量增加漏电、疲劳和 imprint。
- 计算质量门槛：结构来源、POTCAR/functional、k 点、能量截断、力和应力收敛。

3. 抽取提示词

物理规则被压缩注入 LLM prompt：

- Pr 与 2Pr 不合并。
- 没有样品/相/电极/厚度/退火上下文时要降置信度。
- 高 Pr/2Pr 但只有 monoclinic/amorphous 证据时要标记风险。
- 界面和氧空位是机制变量，不是装饰字段。

4. 可靠性评分

每条设计数据行增加：

- `physical_consistency_score`
- `physical_recommendation_allowed`
- `physical_hard_violations`
- `physical_soft_warnings`
- `physical_risk_flags`
- `physical_descriptor_json`

## 交叉验证层

事实级交叉验证：

- 证据一致性模型：数值、单位、页码、Pr/2Pr 是否与证据句一致。
- 本体关系模型：材料、样品、性能、相、器件关系是否完整。
- 领域范围模型：数值和材料体系是否落在合理物理范围。
- Ensemble 投票：把事实分为 valid、needs_check、reject。
- 可选 LLM 复核：用 Qwen 3.7 max 作为额外审稿人，不替代规则审计。

模型级交叉验证：

- 论文主结果仍报告独立验证集 MAE/RMSE/R2。
- K-fold 交叉验证只用于稳定性诊断。
- 输出每个模型的 MAE mean/std、RMSE mean/std、R2 mean/std 和容差命中率。

计算级交叉验证：

- 文献证据提出机制假设。
- 物理约束筛掉明显不可行候选。
- DFT/缺陷/界面/相场任务验证 descriptor。
- 计算结果回写 KG，不直接覆盖实验标签。

## 初始执行命令

```bash
.venv/bin/python pipelines/18_multi_model_validate_dataset.py --limit 200
.venv/bin/python pipelines/37_apply_physical_constraints.py
.venv/bin/python pipelines/50_cross_validate_design_models.py --min-rows 30 --folds 5
```

可选 LLM 事实复核：

```bash
.venv/bin/python pipelines/18_multi_model_validate_dataset.py --limit 50 --include-llm --models qwen3.7-max
```

## 论文叙事中的位置

推荐表述：

> We introduce a physics-constrained reliability layer that cross-checks extracted facts through evidence consistency, ontology completeness, domain-range plausibility, and K-fold model stability. The physical knowledge base does not replace experimental evidence; it defines boundary conditions for extraction, benchmark inclusion, recommendation filtering, and computation feedback.

