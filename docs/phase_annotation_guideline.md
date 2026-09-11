# HfO2/HZO 相结构与一致性标注指南 v0.1

本指南用于 `phase_observation_annotation_queue.csv`。队列中的自动标签、同 chunk 恢复句和条件建议都不是金标准；标注员必须查看原 PDF 页面或对应图表后确认。

## 1. 标注单位

一个标注单位必须绑定到一个明确样品、一个相观测范围和一组证据定位。相同 chunk 中多个性能链接若指向同一材料与相，只保留一条相标注任务。不同厚度、退火、电极、衬底、循环状态或空间位置必须拆成不同样品观测。

论文分组键优先使用规范化 DOI；无 DOI 时使用 paper_id。同一论文的正文、补充材料、重复 PDF 和所有样品必须留在同一个训练/验证 partition。

## 2. 必填人工字段

- `review_phase_label`：`m`、`o`、`t`、`m+o`、`o+t`、`m+t`、`m+o+t`、`other`、`unresolved`。
- `review_phase_scope`：`dominant_bulk_average`、`minority`、`mixed_unquantified`、`local_grain`、`interface_local`、`orientation_specific`、`computed_structure`、`unresolved`。
- `review_sample_conditions`：确认、修正或补全厚度、沉积、衬底、电极、退火、应变与测量/循环状态；每项需指向原文。
- `review_source_type`：`primary_experiment`、`primary_computation`、`secondary_citation`、`review_summary`、`unresolved`。
- `adjudication_status`：`annotated_single`、`needs_second_annotator`、`adjudicated`、`exclude_insufficient_evidence`。
- `annotator` 与 `review_notes`。

## 3. 相标签规则

1. 仅按作者的结构表征结论与可定位图表标注，不能由 Pr/2Pr、回线形状或“ferroelectric phase”反推 o 相。
2. `Pca21`、明确 polar orthorhombic 或作者明确写出的 o-phase 可标 `o`；仅写 polar/ferroelectric 而无晶相信息时标 `unresolved`。
3. 同一证据同时报告 o 和 m，即使 m 为少量，也不能把样品无条件标成纯 o；用多标签并在 scope/notes 记录 dominant/minority。
4. XRD 的体平均结论与 TEM/STEM 的局部晶粒结论分开记录。二者不同优先视作 observation-scope difference，不立即判科学矛盾。
5. 理论候选结构、DFT 能量表和相模型不是实验样品相结果，标 `computed_structure` 与 `primary_computation`。
6. 综述对其他论文的转述不能作为 primary experiment；应追溯被引原论文，否则排除出锁定真值集。

## 4. 条件建议的接受规则

`condition_suggestions_json` 只来自同一 chunk 的保守规则。接受前必须确认数值属于当前 HfO2/HZO 样品：

- 厚度不能误取电极、界面层、衬底或晶粒尺寸；
- 退火温度/时间不能误取沉积温度、测量温度或器件脉冲宽度；
- 沉积方法不能误取顶电极/底电极的沉积方法；
- tensile/compressive stress/strain 必须确认作用于目标薄膜，而非衬底或电极；
- “thermal budget” 不能自动等价为完整退火条件。

所有接受的补全值都要保留 evidence text、page/chunk 和标注员。

## 5. 跨文献一致性裁决

只有两侧均为 `primary_experiment`、相证据已人工确认、样品条件覆盖充分，才进入一致性裁决：

- `scientific_contradiction_candidate`：记录条件可比且相集合不相交，尚无已知表征范围或状态差异；仍需二次核查。
- `condition_explained_difference`：存在明确工艺、应变、界面、表征尺度或循环/测量状态差异。
- `extraction_review_needed`：相绑定、样品绑定、证据定位或 source type 有问题。

不得把“缺失条件”当作“条件相同”。关键字段未知时必须降级为证据不足。

## 6. 双人标注与冻结

训练集和候选池可迭代修订。锁定验证集在模型训练前由人工分层选择并冻结 DOI/论文清单；冻结后不根据模型误差更换论文。

至少对锁定集和训练集随机 20% 做双人独立标注。相标签和一致性类别 Cohen's κ 目标均为 ≥0.80；若未达到，先修订指南并重新标注，不能通过调模型掩盖标注分歧。
