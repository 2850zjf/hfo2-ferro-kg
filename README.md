# HfO2-FerroKG

氧化铪基铁电材料知识图谱工作台。

本项目第一阶段目标：
基于本地 213 篇近三年 HfO2 基铁电材料 PDF，自动解析文献文本，抽取 HfO2 材料组成、工艺、相结构、铁电性能和证据句，经人工审核后构建知识图谱，并提供基于证据的 RAG 问答。

## 当前状态

这是第一阶段的项目骨架，已经包含：

- 标准目录结构
- Streamlit 本地工作台首页
- SQLite 初始化模块
- 基础项目规则和环境变量样例
- 最小 pytest 测试

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
