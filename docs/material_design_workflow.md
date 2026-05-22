# HfO2-FerroKG 材料设计工作流

这个工作流的目标不是只做“文献管理”，而是把本地 HfO2 铁电文献逐步变成一个可溯源、可评测、可训练、可反推工艺的材料设计 benchmark。

## 1. 数据入口

输入来自本地 `data/raw_pdfs/`。系统先完成 PDF 登记、解析、表格抽取、图注和图片对象登记，然后生成带页码的 chunk。

当前原则：

- PDF 不提交到 Git。
- 解析结果保留页码和 chunk_id。
- 表格、图注和正文都可以进入后续抽取。
- OCR 或解析质量差的 PDF 先标记，不强行编造结果。

## 2. 两条抽取线

第一条是本体约束抽取。它围绕材料体系、薄膜厚度、退火、电极、相结构、器件类型、Pr/2Pr/Ec 等核心字段抽取，适合构建知识图谱。

第二条是开放 benchmark 抽取。它不再局限于旧本体，会额外抽取机制解释、理论启发、设计规则、优化目标、图表趋势和适合机器学习的一行式 benchmark record。

两条线都必须保留证据句、页码、paper_id、pdf_id 和 chunk_id。

## 3. AI 预审核 + 人工抽查

LLM 可以帮忙做高强度预审核，但正式结论仍建议保留人工抽查入口。

优先检查：

- Pr 和 2Pr 是否混淆。
- 单位是否正确，特别是 μC/cm²、MV/cm、kV/cm。
- 性能值是否来自当前论文实验，而不是综述引用。
- 厚度、退火、电极和性能值是否属于同一个样品。
- 图中估读和正文直接报告是否区分。

## 4. Knowledge Graph

知识图谱保存“事实之间的关系”。

典型路径：

```text
Paper
-> HafniaMaterial
-> ThinFilmSample
-> FabricationProcess
-> PhaseStructure
-> FerroelectricProperty
-> Evidence
```

它主要服务于溯源、解释、RAG 和发现关联路径。比如回答“TiN 电极相关的 HZO 2Pr 报道有哪些”，图谱可以把材料、器件、电极、性能和证据连起来。

## 5. Benchmark

Benchmark 保存“可训练的一行样品记录”。

一行记录通常包含：

- 输入变量：材料体系、掺杂、Zr 比例、厚度、沉积方法、退火温度、退火时间、气氛、电极、衬底、器件类型、相结构。
- 输出目标：Pr、2Pr、Ec、endurance、retention、memory window、leakage current density 等。
- 证据：论文标题、DOI、页码、证据句、质量标记。

它主要服务于模型训练、模型评测、候选排序和工艺优化。

## 6. Baseline 模型

第一版先训练 baseline 回归模型，目标是判断当前数据是否已经足够支撑趋势预测。

优先训练：

1. double_remanent_polarization_2Pr
2. remanent_polarization_Pr
3. coercive_field_Ec

训练时按目标性能分别建模，输出 MAE、RMSE、R² 和相对均值基线的提升。如果某个目标行数不足，则跳过，不强行训练。

## 7. 工艺优化

后续可以在 baseline 之上增加：

- 相似文献约束：推荐结果必须能追溯到相近证据。
- 不确定性估计：优先选择预测高性能且不确定性较高的候选。
- 多目标优化：同时考虑 2Pr 高、Ec 合理、leakage 低、endurance 高。
- 主动学习：实验新结果回填后自动更新 benchmark 和模型。

## 8. 推荐执行顺序

```text
全量解析
-> 全量 LLM 抽取
-> AI 预审核
-> 人工抽查高风险事实
-> 构建知识图谱
-> 构建设计数据集
-> 训练 baseline 模型
-> 生成候选工艺
-> 实验反馈回填
```

对应本地命令：

```bash
python pipelines/21_build_design_dataset.py
python pipelines/22_train_design_models.py
```

也可以在 Streamlit 的“材料设计工作流”页面中点击按钮运行。
