# HfO2-FerroKG 材料设计工作流

## 核心定位

HfO2-FerroKG 不是一个单纯的 PDF 批处理工具。它要解决的问题是：

> 如何把分散在大量 HfO2 铁电材料文献中的材料组成、样品工艺、相结构、器件结构、性能参数和证据句，转化为可追溯、可比较、可训练、可用于材料设计的数据基础设施。

换句话说，项目最终服务的是材料设计和工艺优化，而不只是文献管理。

## 我们要解决什么问题

### 1. 文献数据碎片化

HfO2 铁电材料的性能不由单一材料名称决定，而是由材料体系、掺杂、Zr/Hf 比例、薄膜厚度、沉积方法、退火温度、退火时间、气氛、电极 stack、衬底、相结构、器件类型和 wake-up/endurance 状态共同决定。

只记录“HZO，2Pr = 40 μC/cm²”是不够的，因为它无法回答这个值来自什么样品，也无法支撑工艺优化。

### 2. 样品条件和性能值难以对应

同一篇论文经常包含多个样品、多个厚度、多个退火条件和多个器件结构。若不能把性能值和对应样品绑定，就会出现错误关联。

项目必须尽量把每个性能值绑定到：

- 材料体系
- 薄膜厚度
- 沉积方法
- 退火温度、时间、气氛
- top/bottom electrode 或完整 device stack
- 器件类型
- 相结构
- wake-up/endurance 状态
- 证据句和页码

### 3. HfO2 文献中存在高频抽取陷阱

需要重点防止：

- 把 2Pr 当成 Pr。
- 把 kV/cm、MV/cm、V/m 混淆。
- 把综述中的二手引用当成当前论文原始实验值。
- 把图中估读值当成正文报告值。
- 把不同样品的退火条件、电极和性能值错误拼接。

因此，每个事实都要保留 raw value、raw unit、normalized value、evidence_text、page_number 和 review_status。

### 4. 缺少可训练 benchmark

知识图谱可以解释关系，但模型训练需要表格化样品记录。材料设计模型需要的是：

```text
输入变量：材料 + 工艺 + 结构 + 器件条件
输出目标：Pr / 2Pr / Ec / endurance / retention / leakage / memory window
证据：论文、DOI、页码、证据句、质量标记
```

这就是 benchmark 的意义。

## 为什么这个工作有意义

### 对科研综述

它可以快速回答“过去几年 HZO、La:HfO2、Si:HfO2 等体系分别报道了哪些性能范围”，并且每个结论都可以追溯到 DOI、页码和证据句。

### 对机制理解

它可以把材料体系、相结构、电极、退火和性能联系起来，帮助发现哪些因素更可能稳定正交相、改善 wake-up、提升 endurance 或降低 leakage。

### 对材料设计

它可以把文献事实转化为 benchmark，进一步训练预测模型或排序模型，用于提出下一批值得验证的实验组合。

### 对数据可信度

它把“AI 抽取”变成可审计流程。机器先抽取和预审核，人再检查高风险事实，避免直接相信模型输出。

## Knowledge Graph 和 Benchmark 的分工

| 模块 | 核心价值 | 数据形态 | 适合回答 |
| --- | --- | --- | --- |
| Knowledge Graph | 解释、溯源、关联 | 节点 + 关系 + 证据 | 这个结论来自哪里？哪些因素相关？ |
| Benchmark | 评测、预测、优化 | 样品输入 + 性能输出 | 给定工艺能预测什么性能？模型是否有效？ |

两者不是替代关系。知识图谱保证可信和可解释，benchmark 支撑建模和优化。

## 从问题出发的系统工作流

```text
本地 PDF
-> 文本、表格、图注、图片对象解析
-> 高价值 chunk 识别
-> LLM 全量抽取
-> AI 预审核
-> 人工抽查
-> 知识图谱
-> Benchmark 数据集
-> Baseline 模型
-> 候选工艺推荐
-> 实验反馈回填
```

## 当前内容优化重点

### 1. 从“事实抽取”升级为“样品级事实关联”

后续重点不是继续增加孤立性能值，而是把性能值和对应样品条件绑定。

优先字段：

- material_family
- dopant_elements
- zr_fraction
- film_thickness_nm
- deposition_method
- annealing_temperature_c
- annealing_time_s
- annealing_atmosphere
- electrode_stack
- device_type
- phase_name
- wake_up_or_endurance_state

### 2. 从“RAG 问答”升级为“证据推理”

回答不能只给自然语言总结。每个关键结论必须带：

- paper title
- DOI
- page_number
- evidence_text
- fact_id 或 chunk_id

### 3. 从“可视化图谱”升级为“设计图谱”

图谱页面应该突出三类节点：

- 可控变量：材料、厚度、退火、电极、沉积方法。
- 中间结构：相结构、取向、缺陷、应力、界面。
- 目标性能：Pr、2Pr、Ec、endurance、retention、leakage。

### 4. 从“普通模型训练”升级为“受约束材料设计”

模型推荐候选工艺时，不能只输出一个数值预测。它还要输出：

- 相似文献证据
- 预测性能
- 不确定性
- 关键影响变量
- 推荐理由
- 风险提示

## 最小可发表/可汇报表达

本项目面向 HfO2 基铁电材料的数据驱动设计，针对当前文献数据分散、样品条件与性能参数难以关联、Pr/2Pr 和单位易混淆、缺乏可训练 benchmark 等问题，构建了一个本地 PDF 驱动的知识图谱与 benchmark 工作台。系统从 PDF 中解析正文、表格和图注，抽取材料、工艺、相结构、器件和性能事实，并保留 DOI、页码和证据句。经 AI 预审核和人工抽查后，事实被组织为知识图谱用于可追溯问答，同时转化为样品级 benchmark 用于模型训练和工艺优化。该工作为 HfO2 铁电材料的文献归纳、机制分析和下一步实验设计提供了可验证的数据基础。

## 对应本地命令

```bash
python pipelines/21_build_design_dataset.py
python pipelines/22_train_design_models.py
```

也可以在 Streamlit 的“材料设计工作流”页面中点击按钮运行。
