# HfO2-FerroKG

氧化铪基铁电材料知识图谱与论文原型工作台。

本项目面向一个核心研究问题：

> 如何从 HfO2/HZO 文献中构建样品级、证据可追溯、可建模的材料知识图谱？

论文原型主线固定为 HfO2/HZO/doped HfO2，不扩展到 BaTiO3、PZT、BiFeO3 等其他铁电体系。核心贡献包括 ontology-first extraction、sample-property linking、tiered benchmark、evidence-grounded RAG 和可验证的材料性能预测模型。第一性能目标聚焦 `remanent_polarization_Pr` 与 `double_remanent_polarization_2Pr`。

## 当前状态

当前基线以 `docs/HfO2-FerroKG_完整工作说明.md` 和本地数据库快照为准：

- 584 个 PDF 记录、582 个已解析 PDF、8595 页解析文本
- 1231 个表格、22526 个 document chunk、16630 个结构化抽取候选
- 4153 条 reviewed facts，其中 3510 条为 `preapproved_machine`、643 条为 `needs_human_review`
- 21785 条 benchmark 抽取结果
- 20633 条样品级关联事实，其中 strong 6133、partial 9187、weak 5288、ambiguous 25
- 584 张 LLM 文献卡片、17177 个 chunk 语义标签、4180 条 AI 二次审核结果
- 12297 行 design dataset，分层 benchmark 包含 strong_only 992 行、strong_partial 4731 行、all_traceable 12168 行
- Pr、2Pr、Ec、endurance、retention、memory window、leakage 等基础模型已训练；强相关 Pr/2Pr 数据可用 SVR、树模型和线性模型做训练/验证集评判

旧版 README 中的 213 篇 PDF 和小规模图谱数字只代表历史阶段，不再作为论文原型基线。

## 方法工作流

```text
PDF
-> page text / table / caption parsing
-> ontology-first schema extraction
-> sample-property linking
-> AI audit and manual review
-> evidence KG / design KG
-> tiered benchmark
-> predictive model validation
-> active-learning candidates
-> evidence-grounded RAG
```

论文结论不直接使用 `preapproved_machine` 作为人工真值；正式结果优先使用 strong sample-level rows、AI `usable_for_model` 和人工标注后的数据。

## 数据放置规则

原始 PDF 放在：

```text
data/raw_pdfs/
```

PDF、解析文本、chunk、向量库、SQLite、模型、日志、导出结果和 `.env` 默认不会被 Git 跟踪。不要把学校订阅资源 PDF 上传到公开代码仓库。

## 本地运行

安装依赖：

```bash
pip install -r requirements.txt
```

初始化数据库：

```bash
python3 -m backend.db.init_db
```

启动工作台：

```bash
streamlit run app/Home.py
```

运行测试：

```bash
pytest
```

生成本地质量校验报告：

```bash
python3 pipelines/10_validate_results.py
```

构建论文原型 benchmark 和模型：

```bash
python3 pipelines/21_build_design_dataset.py
python3 pipelines/29_build_benchmark_tiers.py
python3 pipelines/30_train_tiered_design_models.py --targets remanent_polarization_Pr,double_remanent_polarization_2Pr
python3 pipelines/33_filter_and_compare_models.py --min-rows 30
```

`pipelines/33_filter_and_compare_models.py` 会先生成强相关 Pr/2Pr 建模切片，再用 RandomForest、ExtraTrees、GradientBoosting、Ridge、ElasticNet 和 SVR-RBF 在固定训练/验证集上对比。GNN/图神经网络属于下一阶段图结构模型验证，需要先把设计图谱转为张量数据并接入 PyTorch Geometric 或 DGL。

## LLM 抽取

项目使用 OpenAI-compatible client。千问 3.7 max / DashScope 本地配置示例：

```text
HFO2_FERROKG_LLM_PROVIDER=dashscope
HFO2_FERROKG_LLM_MODEL=qwen3.7-max
DASHSCOPE_API_KEY=你的本地 API key
DASHSCOPE_API_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
HFO2_FERROKG_USE_LLM=true
```

真实 API key 只能保存在环境变量或 `.env` 中，不要写入代码、日志、报告或 Git。没有 API key 或额度暂停时，管线会自动退回规则抽取，保证本地闭环仍然能跑通。

## 论文原型评测

新增 gold set 评测入口：

```bash
python3 pipelines/32_evaluate_paper_prototype.py --sample-size 30
```

首次运行会生成 `data/evaluation/hfo2_paper_gold_set_template.csv`。人工填写 `gold_*` 字段后再次运行，会输出：

- `data/evaluation/paper_prototype_eval_summary.json`
- `data/evaluation/paper_prototype_eval_details.csv`
- `data/evaluation/paper_prototype_eval_report.md`

评测指标包括 extraction precision/recall/F1、sample-property linking accuracy、Pr/2Pr confusion rate 和 unit normalization error rate。

## 审核原则

- `preapproved_machine` 只能代表机器预审核通过，不能直接用于论文结论。
- `needs_human_review`、异常值和综述/二手数据必须人工复核。
- 只有人工确认后的 `approved` 或人工标注后确认的数据才适合导出为正式结论。
- Pr 和 2Pr 必须分开看；系统不会自动把 2Pr 当作 Pr。

## Streamlit Community Cloud 部署

GitHub 仓库只应包含代码和配置，不要上传 PDF、SQLite 数据库、解析文本、向量索引、模型文件或 API key。

部署参数：

```text
Repository: hfo2-ferro-kg
Branch: main
Main file path: app/Home.py
```

云端部署适合展示工作台界面和代码能力；付费 PDF 文献和全量抽取建议继续在本机或实验室服务器运行。
