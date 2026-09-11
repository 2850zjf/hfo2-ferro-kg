# macOS → Windows/WSL 迁移记录

日期：2026-09-12
执行环境目标：WSL Ubuntu-24.04（路径以 `/mnt/d/...` 形式存储）

## 1. 迁移前状态

本机存在**两份**完整的项目副本：

| | `D:\Code-X\Ferroelectric knowledgegraph\KG agent\hfo2-ferro-kg` | `D:\KG agent\hfo2-ferro-kg` |
|---|---|---|
| 来源 | 2026-09 从 macOS 拷贝 | 2026-05~08 原生 Windows |
| DB 大小 | 1,033,093,120 | 348,246,016 |
| DB mtime / 最后 pipeline_run | 2026-07-22 | 2026-05-27 |
| git HEAD | `654f359` | `ddb3456` |
| 表数 | 29 | 19 |
| pdf_files / reviewed_facts | 1172 / 71107 | 584 / 4153 |
| multimodal_asset_queue / pdf_equations / computation_jobs | 27664 / 11774 / 12 | 三表均不存在 |

**权威副本为 `D:\Code-X\...`**。旧副本 `D:\KG agent\` 的 584 篇 PDF 对应 README 中
「旧版 README 中的 213/584 篇 PDF …… 只代表历史阶段」，是 2026-05 的历史快照，
本次迁移**未改动它**，保留作为历史参照。

worktree `D:\Code-X\hfo2-phase-competition-worktree` 下的
`data/hfo2_ferrokg.sqlite3` 只有 319,488 字节、24 张表、0 行 macOS 路径，
是一个空壳库；相竞争工作流实际通过 `--db-path` 读取主仓的 1 GB 库
（见 `reports/phase_competition_workflow_20260824/phase_competition_workflow_summary.json`）。

## 2. 路径映射规则

```
/Users/jinfengzhang/Codex/     ->  /mnt/d/Code-X/
D:\KG agent\hfo2-ferro-kg\     ->  /mnt/d/Code-X/Ferroelectric knowledgegraph/KG agent/hfo2-ferro-kg/
```

第二条同时把剩余的反斜杠归一化为正斜杠。

迁移前已验证：全部 52,861 行 macOS 路径共享 `/Users/jinfengzhang/Codex/`
单一前缀，**0 行例外**；112 个指向 `D:\KG agent\` 的 open_access PDF
在新副本 `data/raw_pdfs/open_access/`（132 个文件，超集）中**全部存在**。

## 3. 已改写的列（活指针）

数据库：`data/hfo2_ferrokg.sqlite3`，单事务提交，**52,310 行**。

| 表.列 | 行数 |
|---|---:|
| `multimodal_asset_queue.file_path` | 25,486 |
| `pdf_visual_assets.file_path` | 13,712 |
| `pdf_equations.image_path` | 11,774 |
| `pdf_files.file_path` | 1,172 |
| `literature_candidates.downloaded_pdf_path` | 122（10 macOS + 112 旧 Windows） |
| `computation_jobs.work_dir` | 12 |
| `computation_jobs.input_manifest_json` | 12 |
| `computation_jobs.cloud_payload_json` | 12 |
| `ontology_versions.bundle_path` | 4 |
| `ontology_versions.report_path` | 4 |

改写后校验：

- 改写列中残留 macOS / 旧 Windows 路径：**0**
- JSON 列合法性：`input_manifest_json` 12/12、`cloud_payload_json` 12/12，内嵌陈旧路径 12 → 0
- 抽样 220 个改写后指针映射回本机路径：**220 存在，0 缺失**
- `computation_jobs.work_dir` 全部 12 个目标目录均存在

## 4. 回滚点

```
data/backups/hfo2_ferrokg_before_windows_migration_20260912_011554.sqlite3
1,033,093,120 bytes
SHA-256 45afa38723ddaf144129b8962e42f018f84b2578e4dca558bd1937f4f4296955
```

备份与迁移前源库的 SHA-256 逐字节一致（已双向校验）。回滚方式：关闭所有
占用进程，确认无 `-wal` / `-shm` / `-journal` 边车文件，然后用该备份覆盖
`data/hfo2_ferrokg.sqlite3`。

注意：迁移后活库大小为 1,033,101,312 字节，比备份大 8,192 字节，这是
52,310 次 UPDATE 造成的正常页重分配，不代表备份有误。

回滚或任何后续改写后，用以下命令复核（只读）：

```bash
python scripts/migration/verify_migration.py
```

## 5. 故意未改写的部分（请勿"顺手修好"）

### 5.1 `pipeline_runs.stats_json`（663 行含 macOS 路径）

该表是**只追加的历史运行日志**（列为 `run_id, step_name, status, stats_json,
message, created_at`），里面的 `output_csv` / `report_path` / `output_path` /
`gold_set_csv` 记录的是**当次 run 当时**把输出写到了哪里。

已核实全仓仅 `backend/services/pipeline_log.py` 接触该列：`record_pipeline_run()`
写入、`recent_pipeline_runs()` 读回展示，**没有任何代码从中解析路径**。

改写它会篡改"某次运行发生在何处"的历史事实，与本项目 AGENTS.md 第 7–9 条的
证据可追溯原则、以及 VASP 审计以 SHA-256 固定原始输出的做法相冲突。因此保留原值。
其中还含 `D:\KG agent\...` 与 `C:\Users\hautz\Desktop\KG\...`（JSON 转义为
`D:\\KG agent\\...`）等更早的 Windows 历史位置，同样保留。

### 5.2 `reports/**` 下的 JSON / MD

`vasp_raw_output_audit_20260824/`、`phase_competition_workflow_20260824/`、
`phase_strain_computation_preflight_20260824/`、`external_vasp_archive_audit_20260824/`
等是**哈希绑定的审计产物**：记录了被审计文件的 SHA-256、审计时的源目录、
以及 TEFS 作业 `151024` 的计费与失败证据。改写其中的路径会让审计链与
`REMOTE_EXECUTION_GATE.md` 的声明失去对应关系。保留原值；如需 Windows/WSL
版本，应在本机**重跑** pipeline 生成新报告，而不是编辑旧报告。

### 5.3 源码中的硬编码默认值（尚未修）

以下两处仍写死 macOS 绝对路径，属**未完成的迁移项**：

- `backend/services/vasp_raw_audit.py:18` — `DEFAULT_SOURCE_ROOT`
- `backend/services/phase_strain_job_builder.py:18` — `DEFAULT_SOURCE_ROOT`
  指向 `/Users/jinfengzhang/Codex/Ferroelectric knowledgegraph/KG agent/hfo2-ferro-kg/data/computation/tefs_hfo2_phase_smoke_20260622/runs/hfo2_phase_smoke`

两者都已有 `PROJECT_ROOT = Path(__file__).resolve().parents[2]`，应改为基于
`PROJECT_ROOT` 推导或从环境变量读取。调用 `pipelines/66_audit_vasp_raw_outputs.py`
和 `pipelines/67_prepare_phase_strain_jobs.py` 时若不显式传 `--source-root`，
当前会失败。

## 6. 其它已完成项

- **AppleDouble 清理**：删除 58,587 个文件、240.0 MB（主仓 58,587，worktree 0）。
  删除策略要求同时满足：文件名以 `._` 开头或等于 `.DS_Store`；`._` 文件前 4 字节
  必须是 AppleDouble 魔数 `00 05 16 07`；且未被 git 跟踪。结果：0 个文件因魔数
  校验失败被跳过，0 个被 git 跟踪，其中 173 个位于 `.git/` 内部。
- **`.gitignore`**：主仓补入 `._*`（`.DS_Store` 原已存在）。worktree 的
  `.gitignore` 在未提交改动中已含 `._*`，无需改动。
- **git 完整性**：清理后 `git fsck` 无错误，HEAD 仍为 `654f359`，
  `git worktree list` 两个工作树均正常。主仓 `git status --porcelain`
  从 362 条降至 164 条。
- **`.env`**：无需改动。`HFO2_FERROKG_DB_PATH=data/hfo2_ferrokg.sqlite3`、
  `HFO2_FERROKG_PDF_ROOT=data/raw_pdfs` 均为相对路径，本身可移植。
  注意 worktree 下**没有** `.env`（主仓有），在 worktree 内运行需自行准备。

### 6.1 迁移工具

三个脚本纳入版本控制，位于 `scripts/migration/`：

| 脚本 | 作用 | 写入行为 |
|---|---|---|
| `migrate_paths_macos_to_wsl.py` | 执行 §3 的路径改写 | `--dry-run` 只读；`--apply` 先做 SHA-256 校验备份，再单事务改写 |
| `clean_appledouble.py` | 删除 macOS 拷贝残留 | `--dry-run` 只读；`--apply` 删除 |
| `verify_migration.py` | 复核上述全部结果 | 纯只读，检查失败返回非 0 |

三者都不硬编码绝对路径：主仓位置从 worktree 的 `.git` 指针解析，
WSL 前缀由脚本自身在目录树中的位置推导（`D:\Code-X` → `/mnt/d/Code-X`），
因此在 Windows 与 WSL 两侧都能运行，整棵树被移动后也不需要改脚本。
必要时可用 `--main-repo` / `--db` / `--wsl-prefix` / `--old-win-prefix` 覆盖。

`clean_appledouble.py` 的删除策略是四条同时满足，任一不满足即跳过并报告：
文件名以 `._` 开头或等于 `.DS_Store`；`._` 文件前 4 字节必须是 AppleDouble
魔数 `00 05 16 07`；未被 git 跟踪；文件可读。若 `git ls-files` 无法枚举某个
仓库，该仓库整体跳过而不是不安全地清理。

三个脚本在 2026-09-12 迁移后均从新位置复跑验证：`verify_migration.py`
全部检查通过并退出 0，另两个 `--dry-run` 分别报告 0 行待改、0 文件待删（幂等）。

## 7. 尚未完成的迁移项

按影响排序：

1. **Python 环境**：主仓 `.venv` 是 macOS 版（`pyvenv.cfg` 记录
   `version = 3.12.13`，含 `.venv/bin`），在 Windows/WSL 均不可用。
   需在 WSL 内按 3.12 重建并 `pip install -r requirements.txt`（12 个依赖）。
   Windows 侧默认 `python` 是 Anaconda **3.13.5**，与 macOS 的 3.12.13 存在版本漂移。
2. **`.env` 未落到 worktree**（见 §6）。
3. **§5.3 的两个硬编码默认路径**未修。
4. **行尾策略未定**：`git diff` 对全部已修改文件报
   `LF will be replaced by CRLF`。仓库在 macOS 上写成、现于 Windows 检出，
   而 `scripts/*.sh`（含 `run_publication_v23_*.sh`、`cloud/*.sh`）必须保持 LF
   才能在 bash 下执行。建议先落 `.gitattributes` 再改代码。
5. **验证未跑**：本次迁移**没有执行 `pytest`**，因此不对迁移后代码可用性作任何断言。
   建议在 WSL 环境就绪后运行 `pytest` 与
   `python pipelines/10_validate_results.py` 做首轮验证。
6. **路径含空格**：`/mnt/d/Code-X/Ferroelectric knowledgegraph/KG agent/hfo2-ferro-kg`
   含两处空格。Python `pathlib` 无碍，但所有 shell 脚本与命令行必须加引号。
7. **137 个未跟踪文件 + 10 个未推送提交**仍未进版本控制，分支无 upstream。

## 8. 本次迁移未做的事

- 未修改任何 `reports/**`、`docs/**`、`computations/**` 下的既有文件内容
- 未改写 `pipeline_runs.stats_json`
- 未改动旧副本 `D:\KG agent\`
- 未创建虚拟环境、未安装依赖
- 未运行 `pytest`、Streamlit 或任何 pipeline
- 未提交、未推送任何 git 变更（`.gitignore` 的一处修改仍在工作区）
- 未触碰 TEFS / 云端，未产生任何付费资源
