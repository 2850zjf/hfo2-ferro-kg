# HfO2-FerroKG 方法升级与进展报告

生成日期：2026-05-21

## 1. 为什么这个工作值得做

HfO2 基铁电材料已经从单纯材料现象研究进入“材料-工艺-器件-计算架构”耦合阶段。近年的综述把 HfO2 铁电材料的价值归结为 CMOS 兼容、纳米尺度可保持极化、可用于 FeRAM/FeFET/FTJ、神经形态和存内计算，但也强调疲劳、imprint、可靠性、亚 5 nm 缩放等问题仍然存在。公开综述还指出，掺杂、应力工程和氧空位控制是稳定亚稳 orthorhombic phase 的关键路径。

这直接决定了我们的知识图谱不能只抽“2Pr=40 μC/cm²”这种孤立值。它必须把数值和样品上下文绑在一起：材料组成、掺杂、薄膜厚度、沉积方法、电极、退火条件、相结构、器件类型、测量条件和证据页码。

## 2. 研究现状给我们的启发

### 2.1 材料知识图谱已经证明可行，但领域本体必须更细

MatKG 在 Scientific Data 2024 中展示了从材料科学文献自动抽取实体和关系的大规模 KG 路线，覆盖材料、性质、应用、表征、合成方法、描述符和相标签等实体，并形成超过 70,000 个实体和 5.4 million triples。这说明“文献驱动材料 KG”是可行方向，但 HfO2 铁电场景要求更强的样品级语义：同一材料不同退火、电极和厚度会给出完全不同的性能。

### 2.2 LLM-KG 的主流方向是本体工程、抽取、融合三层协同

2025 年 LLM-empowered KG construction survey 将新范式总结为：LLM 正在重塑 ontology engineering、knowledge extraction、knowledge fusion 三层流程，并区分 schema-based 与 schema-free 范式。对我们而言，不能完全 schema-free；HfO2 关键指标和单位风险太高，因此采用 ontology-first + schema-constrained extraction。

### 2.3 GraphRAG 适合回答“全库趋势”问题

GraphRAG 的核心是先从文档抽实体和关系，构建图索引，再对相关实体社区生成摘要，用于回答全局 sensemaking 问题。HfO2-FerroKG 后续应把当前的 fact graph 扩展为“材料体系/工艺/性能社区”，用来回答“近三年 HZO 的退火温度与 2Pr 有什么趋势”这类全库问题，而不是只靠向量检索。

### 2.4 表格是材料 KG 的高价值来源，必须做缓存和人工反馈

2025 年 Digital Discovery 的材料表格 KG 抽取工作强调：材料表格结构高度异质、缩写多、上下文常被省略；半自动抽取、缓存已验证表头/表结构、用户反馈是保证质量的关键。MatSKRAFT 进一步说明，大规模材料知识很多被困在半结构化表格中，表格图表示和约束驱动方法可以显著提高效率。

### 2.5 结构化输出和 embedding 是工程稳定性的基础

OpenAI 官方 Structured Outputs 文档强调，结构化输出可让模型输出遵守开发者提供的 JSON Schema；这与我们的 Pydantic schema、property enum、evidence_text 必填是同一方向。Embedding 文档也明确 embeddings 适合检索、聚类、推荐、异常检测和分类；我们目前先用 TF-IDF，后续可加 embedding 做语义检索与相似文献聚类。

## 3. 已融入项目的方法升级

1. 本体先行：新增 `ontology/hfo2_ontology.yaml` 与 `ontology/kg_method_blueprint.yaml`，每轮抽取先构建 `ontology_bundle.json`。
2. 版本留痕：`extraction_candidates` 带 `ontology_version` 和 `extractor_version`。
3. 增量接入：新增 `pipelines/14_ingest_new_open_access.py`，把开放文献发现、下载、清单、解析、表格、chunk、抽取、图谱、索引串起来。
4. 开放 PDF 下载：只下载开放获取状态明确的 PDF，并检查 PDF 头、文件大小、页数、hash 和质量分。
5. 机器预审核：系统目前不直接做人工审核结论，只做 `preapproved_machine` 或 `needs_human_review`，后续由用户确认。
6. 证据约束：每个性能事实必须保留 evidence_text、paper_id、pdf_id、page_number 或 chunk_id。
7. 质量报告：`validation_report.md` 检查证据、追踪、异常值和是否达到发布门槛。

## 4. 当前本地进展

- PDF 清单：217 个 PDF 记录，其中原始 213 篇加 4 篇开放获取下载候选。
- 已解析文本页：3091 页。
- 表格：386 个表格。
- Document chunks：8838 个。
- 抽取候选：6620 条。
- 机器预审核事实：505 条。
- 本体版本：`hfo2-ferrokg-v1`。
- 当前 LLM 状态：代码已接入，但本机未配置 `OPENAI_API_KEY` 时自动回退规则抽取。

## 5. 下一阶段最值得做的优化

1. DOI 级去重与版本融合：同一论文不同 PDF 只保留一个 Paper 主节点。
2. 表格缓存：学习已验证表头到节点类型/属性映射，减少重复 LLM 调用。
3. GraphRAG 社区摘要：围绕 HZO、La:HfO2、TiN electrode、Pca21、wake-up/fatigue 等自动生成社区摘要。
4. 主文献/综述区分：综述引用值进入 secondary fact，不与原始实验值混用。
5. 主动学习：把用户后续修改作为 few-shot 示例和 eval gold set。
6. 论文级评估：抽样 20-30 篇，标注材料、工艺、相结构、性能，报告 Precision/Recall/F1。

## 6. 参考来源

- MatKG: An autonomously generated knowledge graph in Material Science, Scientific Data, 2024. https://www.nature.com/articles/s41597-024-03039-z
- From Local to Global: A Graph RAG Approach to Query-Focused Summarization, arXiv, 2024. https://arxiv.org/abs/2404.16130
- LLM-empowered knowledge graph construction: A survey, arXiv, 2025. https://arxiv.org/abs/2510.20345
- Structured information extraction from scientific text with large language models, Nature Communications, 2024. https://www.nature.com/articles/s41467-024-45563-x
- Large language models for knowledge graph extraction from tables in materials science, Digital Discovery, 2025. https://pubs.rsc.org/en/content/articlehtml/2025/dd/d4dd00362d
- Ontology-conformal recognition of materials entities using language models, Scientific Reports, 2025. https://www.nature.com/articles/s41598-025-03619-y
- MatSKRAFT: A framework for large-scale materials knowledge extraction from scientific tables, arXiv, 2025. https://arxiv.org/abs/2509.10448
- OpenAI Structured Outputs documentation. https://developers.openai.com/api/docs/guides/structured-outputs
- OpenAI Embeddings documentation. https://developers.openai.com/api/docs/guides/embeddings
