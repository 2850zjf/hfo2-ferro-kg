# HfO2-FerroKG

氧化铪基铁电材料知识图谱与论文原型工作台。

本项目面向一个核心研究问题：

> 如何从 HfO2/HZO 文献中构建样品级、证据可追溯、可建模的材料知识图谱？

论文原型主线固定为 HfO2/HZO/doped HfO2，不扩展到 BaTiO3、PZT、BiFeO3 等其他铁电体系。zaaaszz ontology-first extraction、sample-property linking、tiered benchmark、evidence-grounded RAG、可验证的材料性能预测模型，以及面向云端计算的 evidence-to-computation feedback。第一性能目标聚焦 `remanent_polarization_Pr` 与 `double_remanent_polarization_2Pr`。

## 当前状态

当前基线以 2026-07-11 的数据库快照和
`data/exports/corpus_coverage_v23/corpus_coverage_report.md` 为准：

- 1172 个 PDF 记录：1120 个已解析、24 个重复隔离、28 个不相关隔离
- 14560 页解析文本、39417 个 document chunk，其中 27681 个高价值 chunk
- 2178 张结构化表、13712 张图、8267 个几何匹配图注、11774 条公式候选
- 27664 个图/表/公式多模态资产；LLM 多模态结果按 `strong_only`、`strong_partial`、`all_traceable` 分层
- publication v2.3 队列覆盖 1118 篇论文、25661 个证据块；二手对比表重分层后，第一阶段为 808 个高密度原始证据块
- 3876 条 reviewed facts、21785 条 benchmark 抽取结果、20633 条样品级关联事实
- 样品级关联中 strong 6133、partial 9187、weak 5288、ambiguous 25；4180 条 AI 二次审核记录
- 当前 design dataset 为 18122 行；已有 strong_only 2104 行、strong_partial 9079 行、all_traceable 17913 行
- 本地 TF-IDF 索引覆盖 27681 个高价值 chunk；`text-embedding-v4` 语义索引已通过 1024 维烟测，待全量构建
- PaddleOCR-VL 全量 Markdown 当前为 **0 篇完成**；历史记录只有 dry-run，未计作解析结果

旧版 README 中的 213/584 篇 PDF 和小规模图谱数字只代表历史阶段，不再作为论文原型基线。正在运行的 v2.3 候选数会持续变化，论文统计应以冻结后的 validation report 为准。

## 方法工作流

```text
PDF
-> page text / table / caption / equation / image parsing
-> ontology-first text and multimodal extraction
-> sample-property linking
-> AI audit and manual review
-> evidence KG / design KG
-> tiered benchmark
-> predictive model validation
-> evidence-constrained design recommendations
-> computational feedback task planning
-> hybrid semantic + evidence-grounded RAG
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
python3 pipelines/25_recommend_active_learning.py --target double_remanent_polarization_2Pr
python3 pipelines/34_plan_computational_feedback.py --max-candidates 20 --max-tasks 80
```

构建 v2.3 全文和多模态证据队列：

```bash
python3 pipelines/52_build_publication_extraction_queue.py
python3 pipelines/55_extract_equation_assets.py
python3 pipelines/58_link_visual_assets.py
python3 pipelines/56_build_multimodal_queue.py
python3 pipelines/60_audit_corpus_coverage.py
```

严格烟测和可恢复正文抽取：

```bash
HFO2_V23_LIMIT_CHUNKS=3 HFO2_V23_WORKERS=1 \
  bash scripts/run_publication_v23_phase.sh smoke

bash scripts/run_publication_v23_phase.sh a_primary_dense
```

`run_publication_v23_phase.sh` 默认 2 并发、每条立即提交、6000 token；复杂 JSON 截断时最多自适应到 8000 token。只有余额、鉴权或明确限流才会全局暂停；普通超时只记录当前 chunk 的错误并继续处理其他文献。

全量断点调度可使用：

```bash
bash scripts/run_publication_v23_all.sh
```

总控会按 A/B/C/D 证据阶段顺序运行，已完成的阶段自动跳过，已在运行的阶段会先等待，异常失败最多重试 3 次。鉴权、余额或明确限流时会安全暂停，不会循环消耗 API 费用。

连续 4 条发生超时、连接中断或 503 时，抽取器会触发短时熔断，默认退避 300 秒后再从断点恢复；单个偶发超时仍只记录当前 chunk，不会停掉整个任务。

若运行期间出现短时网络中断，可同时启动尾部重试清扫器；它会等待 A/B/C/D 主流程全部结束后才访问 API，不会与主抽取并发写库：

```bash
bash scripts/run_publication_v23_retry_sweep.sh
```

`pipelines/33_filter_and_compare_models.py` 会先生成强相关 Pr/2Pr 建模切片，再用 RandomForest、ExtraTrees、GradientBoosting、Ridge、ElasticNet 和 SVR-RBF 在固定训练/验证集上对比。GNN/图神经网络属于下一阶段图结构模型验证，需要先把设计图谱转为张量数据并接入 PyTorch Geometric 或 DGL。

`pipelines/34_plan_computational_feedback.py` 只生成计算反馈任务清单，不启动本机或云端计算。它会把设计建议转化为 VASP/DFT、氧空位、界面筛查、相场和 ML potential/MD 等任务，并声明需要输入、预期输出、KG 回写字段和 benchmark 回写字段。

## LLM 抽取

项目使用 OpenAI-compatible client。千问 3.7 max / DashScope 本地配置示例：

```text
HFO2_FERROKG_LLM_PROVIDER=dashscope
HFO2_FERROKG_LLM_MODEL=qwen3.7-max
HFO2_FERROKG_VISION_MODEL=qwen3-vl-plus
DASHSCOPE_API_KEY=你的本地 API key
DASHSCOPE_API_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
HFO2_FERROKG_USE_LLM=true
```

真实 API key 只能保存在环境变量或 `.env` 中，不要写入代码、日志、报告或 Git。普通本地流程可使用规则兜底；论文级 `publication_v23` 采用 strict LLM 模式，不会把失败调用降级后伪装成模型真值。

PaddleOCR-VL 需要单独在 `.env` 设置 `PADDLEOCR_TOKEN`。聊天中粘贴过的 token 不会被代码读取或自动写回配置。

## 知识向量库

离线关键词索引：

```bash
python3 pipelines/08_build_vector_index.py
```

语义索引使用 `text-embedding-v4`、1024 维向量和独立 SQLite，支持断点跳过未变化内容：

```bash
python3 pipelines/61_build_semantic_vector_index.py
```

全量索引计划覆盖正文、表格、几何图注、公式候选和多模态证据。仅当五类来源完整构建且无错误时，RAG 才自动启用 hybrid 排名；部分烟测索引不会影响正式问答。

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
