# AGENTS.md

你正在开发 HfO2-FerroKG，即“氧化铪基铁电材料知识图谱工作台”。

项目目标：
基于用户本地已有的 213 篇近三年氧化铪基铁电材料 PDF，构建一个本地科研工作台，支持 PDF 文献入库、PDF 解析、HfO2 信息抽取、人工审核、知识图谱构建、RAG 问答和数据分析。

硬性规则：
1. 不要自动爬取学校订阅数据库。
2. 不要自动下载付费数据库全文。
3. 不要把 PDF 文件提交到 Git。
4. 不要把 data/raw_pdfs/、data/parsed_pages/、data/vector_index/ 提交到 Git。
5. 不要把 API key、账号、密码、cookie、token 写入代码、日志或 Git。
6. API key 只能从环境变量或 .env 读取。
7. 每个抽取出的材料性能参数必须有 evidence_text、paper_id、pdf_id、page_number 或 chunk_id。
8. 没有证据的抽取结果不能进入正式 facts 表。
9. RAG 回答必须基于证据，不允许编造。
10. 当前范围只做 HfO2 基铁电材料，不做 BaTiO3、BiFeO3、PZT 等其他铁电材料。
11. 第一版优先保证可运行，不要过度设计。
12. 每次修改后运行 pytest。

重点抽取对象：
1. HfO2
2. HZO / Hf1-xZrxO2 / Hf0.5Zr0.5O2
3. Si:HfO2
4. Al:HfO2
5. La:HfO2
6. Y:HfO2
7. Gd:HfO2
8. Sr:HfO2
9. doped hafnia
10. hafnium oxide ferroelectric thin films

重点性能：
1. Pr
2. 2Pr
3. Ec
4. Ps
5. dielectric constant
6. leakage current density
7. endurance
8. retention
9. wake-up
10. fatigue
11. memory window

推荐命令：
- pytest
- streamlit run app/Home.py
- python pipelines/01_build_manifest.py
- python pipelines/02_parse_pdfs.py
- python pipelines/04_chunk_documents.py
- python pipelines/05_run_extraction.py
- python pipelines/07_build_graph.py
- python pipelines/08_build_vector_index.py
- python pipelines/10_validate_results.py

LLM 规则：
- 第一版启用 LLM 结构化抽取，但必须保留规则兜底。
- 没有 OPENAI_API_KEY 时不能报错中断全流程。
- LLM 抽取结果必须通过 Pydantic schema 校验。
- 所有 LLM 结果必须进入候选和机器预审核状态，不直接当作人工 approved。

质量校验规则：
- `preapproved_machine` 不是人工通过，只能用于机器预审核浏览和下一步人工审核。
- 发布论文、报告或正式图谱前必须运行 `python pipelines/10_validate_results.py`。
- Pr > 100 μC/cm²、2Pr > 200 μC/cm²、Ec > 10 MV/cm 必须标记为人工复核。
- 缺少 evidence_text、page_number 或 chunk_id 的事实不能进入正式结果。
