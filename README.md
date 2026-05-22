# HfO2-FerroKG

氧化铪基铁电材料知识图谱工作台。

本项目第一阶段目标：
基于本地 213 篇近三年 HfO2 基铁电材料 PDF，自动解析文献文本，抽取 HfO2 材料组成、工艺、相结构、铁电性能和证据句，经人工审核后构建知识图谱，并提供基于证据的 RAG 问答。

## 当前状态

本地工作台已经跑通第一版闭环，并保留人工复核门槛：

- 213 篇 PDF 已登记到本地 SQLite，PDF 路径均指向 `data/raw_pdfs/`
- 2964 页 PDF 文本已解析
- 9597 个 document chunk 已生成，其中 6793 个为高价值 chunk
- 386 个表格结果已保留
- 6681 条 HfO2 抽取候选已生成
- 434 条机器预审核事实已进入审核区
- 图谱导出包含 1217 个节点、2126 条关系
- 轻量 RAG 索引覆盖 6793 个高价值 chunk

当前校验结论：这些结果是机器预审核数据，不是最终人工 approved 数据。`validation_report.md` 显示证据和页码/chunk 追溯完整，但有 2 条 Pr 大于 100 μC/cm²，需要优先人工复核。

## 数据放置规则

原始 PDF 放在：

```text
data/raw_pdfs/
```

PDF、解析文本、chunk、向量库和 `.env` 默认不会被 Git 跟踪。不要把学校订阅资源 PDF 上传到公开代码仓库。

## 本地运行

安装依赖：

```bash
pip install -r requirements.txt
```

初始化数据库：

```bash
python -m backend.db.init_db
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
python pipelines/10_validate_results.py
```

持续补充开放文献并只处理新增 PDF：

```bash
python pipelines/14_ingest_new_open_access.py --download-limit 10
```

这个增量流程会发现公开数据库中的 HfO2/HZO 论文候选，只下载开放获取状态明确的 PDF，
再按“清单 -> 解析 -> 表格 -> chunk -> 抽取 -> 图谱 -> 索引 -> 报告”的顺序处理新增数据。
项目定位和方法论见 `docs/research_positioning.md`。

## LLM 抽取

第一版已经接入 LLM 结构化抽取。配置 `.env`：

```text
OPENAI_API_KEY=你的本地 API key
HFO2_FERROKG_LLM_MODEL=gpt-4.1-mini
HFO2_FERROKG_USE_LLM=true
```

没有 API key 时，管线会自动退回规则抽取，保证本地闭环仍然能跑通。

## Streamlit Community Cloud 部署

GitHub 仓库只应包含代码和配置，不要上传 PDF、SQLite 数据库、解析文本或向量索引。

部署参数：

```text
Repository: hfo2-ferro-kg
Branch: main
Main file path: app/Home.py
```

在 Streamlit Cloud 的 Secrets 中配置：

```toml
OPENAI_API_KEY = "your-key"
HFO2_FERROKG_LLM_MODEL = "gpt-4.1-mini"
HFO2_FERROKG_USE_LLM = "true"
```

云端部署适合展示工作台界面和代码能力；付费 PDF 文献和全量抽取建议继续在本机或实验室服务器运行。

## 后续任务顺序

1. PDF 文献清单与去重
2. PDF 文本解析
3. 正文切分和表格抽取
4. HfO2 抽取 Schema
5. HfO2 信息抽取（LLM 优先，规则兜底）
6. 事实标准化与单位转换
7. 人工审核页面
8. 知识图谱构建
9. RAG 问答
10. 数据分析看板
11. 本地质量校验与人工审核
12. 开放文献发现、下载和增量接入

## 审核原则

- `preapproved_machine` 只能代表机器预审核通过，不能直接用于论文结论。
- `needs_human_review`、异常值和综述/二手数据必须人工复核。
- 只有人工确认后的 `approved` 事实才适合导出为正式结果。
- Pr 和 2Pr 必须分开看；系统不会自动把 2Pr 当作 Pr。

## 材料设计工作流

新增“材料设计工作流”页面，用来把当前结果继续推进到可训练 benchmark：

```bash
python pipelines/21_build_design_dataset.py
python pipelines/22_train_design_models.py
```

完整方法见 `docs/material_design_workflow.md`。第一版会把审核/预审核事实和开放 benchmark 抽取结果整理为 `data/design/hfo2_design_dataset.csv`，再为 Pr、2Pr、Ec 等目标训练 baseline 模型，输出到 `models/design_models/`。
