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
/Users/<mac-user>/Codex/     ->  /mnt/d/Code-X/
D:\KG agent\hfo2-ferro-kg\     ->  /mnt/d/Code-X/Ferroelectric knowledgegraph/KG agent/hfo2-ferro-kg/
```

第二条同时把剩余的反斜杠归一化为正斜杠。

迁移前已验证：全部 52,861 行 macOS 路径共享 `/Users/<mac-user>/Codex/`
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

### 5.3 源码中的硬编码默认值（已于 §6.4 修复）

以下两处曾写死 macOS 绝对路径：

- `backend/services/vasp_raw_audit.py` — `DEFAULT_SOURCE_DIR`
- `backend/services/phase_strain_job_builder.py` — `DEFAULT_SOURCE_ROOT`

两者原本都指向
`/Users/<mac-user>/Codex/Ferroelectric knowledgegraph/KG agent/hfo2-ferro-kg/data/computation/tefs_hfo2_phase_smoke_20260622/runs/hfo2_phase_smoke`。
后果是实测的：`tests/test_phase_strain_job_builder.py` 有 4 个测试因找不到冻结
CONTCAR 而跳过，Track B 的哈希与空间群校验层空转。

现已改为从 `backend.core.config.MAIN_REPO` 派生，详见 §6.4。

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

### 6.2 git worktree 跨操作系统指针（重要，勿随意改动）

git worktree 用两个纯文本文件互相指向。迁移后它们都存的是 Windows 绝对路径，
导致**在 WSL 内 git 完全不可用**：

```
fatal: not a git repository: /mnt/d/Code-X/hfo2-phase-competition-worktree/D:/Code-X/...
```

Linux 下 git 不把 `D:/...` 当绝对路径，于是拼到 cwd 后面成了垃圾路径。
反过来把两个文件都改成 `/mnt/d/...` 则会弄坏 Windows 侧。

实测结论（两种组合都试过）：**不存在让两侧都认为 worktree 有效的写法**，
因为 back-pointer 必须是某一个 OS 的绝对路径。当前采用的配置是三者结合：

| 文件 | 当前值 | 理由 |
|---|---|---|
| `hfo2-phase-competition-worktree/.git` | `gitdir: ../Ferroelectric knowledgegraph/KG agent/hfo2-ferro-kg/.git/worktrees/hfo2-phase-competition-worktree` | **相对路径**，两侧解析结果相同。git 确实按"文件所在目录"解析它——同目录的 `commondir` 本来就是 `../..` |
| `<main>/.git/worktrees/hfo2-phase-competition-worktree/gitdir` | `D:/Code-X/hfo2-phase-competition-worktree/.git` | 保持 **Windows 绝对路径**。试过改相对，git 不解析它，`worktree list` 原样显示并标记 `prunable` |
| 同目录下的 `locked` | 存在，含 reason | **关键防线**，见下 |

**为什么必须锁**：back-pointer 是 Windows 路径，所以在 WSL 内该 worktree
永远显示为 `prunable`。实测 WSL 内 `git worktree prune --dry-run --verbose` 输出：

```
Removing worktrees/hfo2-phase-competition-worktree: gitdir file points to non-existent location
```

也就是说，**在 WSL 里随手跑一次 `git worktree prune` 就会删掉 worktree 管理目录**，
把它变成孤儿。`git worktree lock` 之后实测该命令无任何输出，锁挡住了。

副作用：`git worktree remove` 现在需要 `--force`。这是期望行为。

当前状态（两侧均已验证）：

| 操作 | Windows | WSL |
|---|---|---|
| `git status` / `log` / `add` / `commit` / `diff` | ✅ | ✅ |
| `git worktree list` | ✅ 路径正常 | ⚠️ 该条目显示为 `D:/Code-X/...` + `locked`（仅显示问题） |
| `git worktree prune --dry-run` | ✅ 无可清理 | ✅ 无可清理（锁生效） |

注意：项目代码**没有任何地方调用 git**（已全仓 grep 确认）。venv 里的
`gitpython` 是 streamlit 的传递依赖，streamlit 只用它取仓库信息做遥测，
包在 try/except 内，不影响运行。因此这个限制只影响人工 git 操作。

若将来移动了目录树，用 `git worktree repair` 修复；修完记得**重新 lock**。

### 6.3 WSL Python 环境与首轮验证

环境：`/home/hautz/.venvs/hfo2-ferrokg`，Python **3.12.3**，pip 26.2.1。
（WSL 的 ext4 由 `C:\Users\hautz\AppData\Local\wsl\{...}\ext4.vhdx` 承载，
即 venv 实际占用 C 盘空间，约 600 MB；C 盘当时可用 177.4 GB。）

依赖按 **mac venv 的实测版本精确钉死**，而不是照 `requirements.txt` 装。
原因是两份 `requirements.txt` 都与真实环境不符：

- worktree 那份声明 12 个全为 `>=` 无上界的包，且**漏掉了代码实际 import 的
  `numpy`（7 个文件）、`pillow`、`requests`**；`networkx` 则声明了但从未被 import
- 主仓那份声明了 `plotly`、`dashscope`，而 mac venv 里两者都不存在
- mac venv 实际有 **84 个包**，含 `ase`/`jax`/`jaxlib`/`cmake`/`gitpython`
  等 FerroX 相关依赖（属独立子项目，见 `scripts/install_ferrox_wsl.ps1`，本次未装）

完整解析结果已冻结为**跟踪文件** `requirements-wsl.lock.txt`（仓库根，79 个
精确 pin + 依据注释）。重建环境：

```bash
python3.12 -m venv ~/.venvs/hfo2-ferrokg
~/.venvs/hfo2-ferrokg/bin/python -m pip install -r requirements-wsl.lock.txt
```

已验证 `pip install --dry-run -r requirements-wsl.lock.txt` 退出 0，即 pin
之间可解、文件可被 pip 解析。

16 个直接依赖全部精确命中 mac venv。**传递依赖有漂移**（安装时只钉了直接依赖）：
`scipy 1.18.1`（mac 1.17.1）、`pyarrow 25.0.1`（24.0.0）、
`cryptography 50.0.1`（48.0.0）、`gitpython 3.1.62`（3.1.50）等。
lock 文件冻结的是**实测通过的版本**，所以重建可复现，但与 mac 环境并非逐字节相同。

**一处有意偏离 mac 环境**：额外安装了 `spglib==2.7.0`。mac venv 里没有它，
但 `REMOTE_EXECUTION_GATE.md:148` 声称九个 POSCAR 通过了「hash-bound spglib 2.7.0
precheck」，协议 §9 也要求独立空间群验证。`backend/services/vasp_raw_audit.py`
对 import 有 `try/except ModuleNotFoundError` 守卫，故此举纯属补齐缺口。
安装后 `tests/test_vasp_raw_audit.py` 确实触发了 spglib 代码路径。

pytest 结果：

```
初次运行（硬编码路径修复前）:  193 passed, 4 skipped, 118 warnings in 22.94s
修复 §5.3 之后:                197 passed, 0 skipped               in 24.90s
两次均 exit 0
```

跑前跑后的完整性核对：

- worktree 空壳库 `data/hfo2_ferrokg.sqlite3` SHA-256 前后一致
  （`a2ef0a221483809e6d4c9491ec563f5fc5b686cc20052c78c4b63ab56b654120`）
- 1 GB 生产库 SHA-256 前后一致（`0e1a0e04fa8b025b326d5e19...`）
- `conftest.py` 的 `prevent_production_output_pollution` 守卫通过，
  且三个受保护目录在 worktree 内**确实存在**，所以是真检查而非空过
- **1 GB 生产库全程未被打开**：`backend/core/config.py` 用显式路径
  `load_dotenv(PROJECT_ROOT/".env")`（不做向上搜索），worktree 无 `.env`，
  故 `db_path` 落到 worktree 自己的空壳库

**故意不建 `.env`**：`config.py` 的 `use_llm` 默认为 `True`，而
`get_llm_api_key()` 在无 `.env` 时返回 `None`。这样任何 LLM 调用会立即失败，
而不是拿着真实 `DASHSCOPE_API_KEY` 发起**付费**调用（协议硬规则：默认不发起付费 LLM 调用）。
需要真实调用时再单独建 `.env`。

初次的 4 个 skip 全部来自 `tests/test_phase_strain_job_builder.py`，
原因是 §5.3 的硬编码路径；修复后见 §6.4。

### 6.4 硬编码 macOS 路径的修复（§5.3 已完成）

`backend/core/config.py` 新增 `_resolve_main_repo()` 与模块常量 `MAIN_REPO`，
按以下优先级解析"拥有生产 `data/` 树的那个仓库"：

1. 环境变量 `HFO2_FERROKG_MAIN_REPO`
2. `PROJECT_ROOT/.git` 是**目录** → 本身即主仓
3. `PROJECT_ROOT/.git` 是**文件** → 解析其 `gitdir:` 指针
   （相对与绝对都支持，故 Windows 与 WSL 两侧通用），
   再取 `<main>/.git/worktrees/<name>` 的上三级

两处消费方改为从 `MAIN_REPO` 派生：

- `backend/services/phase_strain_job_builder.py` → `DEFAULT_SOURCE_ROOT`
- `backend/services/vasp_raw_audit.py` → `DEFAULT_SOURCE_DIR`

两侧实测解析结果：

| | `MAIN_REPO` |
|---|---|
| Windows | `D:\Code-X\Ferroelectric knowledgegraph\KG agent\hfo2-ferro-kg` |
| WSL | `/mnt/d/Code-X/Ferroelectric knowledgegraph/KG agent/hfo2-ferro-kg` |

**一个刻意的安全边界**：`Settings.db_path` 仍基于 `PROJECT_ROOT`，**没有**改成
`MAIN_REPO`。那正是 pytest 全程不碰 1 GB 生产库的原因；改了就会让测试打到
生产库上。此约束已写入 `_resolve_main_repo` 的 docstring。

修复后 `tests/test_phase_strain_job_builder.py` 中原本跳过的 4 个测试真正执行，
并通过了它们对冻结 CONTCAR 的校验。三个 CONTCAR 的实测 SHA-256 与
`PHASE_SPECS[*]["source_sha256"]` 逐相一致：

| 相 | SHA-256 | 期望空间群 |
|---|---|---|
| monoclinic | `2efef57fd989be257b684e2c3f95b0b94cb0fc380ca31a0b8307c22f6162eef9` | 14 (P2_1/c) |
| orthorhombic | `4987c18c5a4edec450c47cb80b1fd7651e2f8ee08ee569406ebfb6dae2f46453` | 29 (Pca2_1) |
| tetragonal | `00f700b91decd07961cbb09b7e1cf96c3f2c2412887b26e454eb135958250ab8` | 137 (P4_2/nmc) |

也就是说，Track B 冻结输入的那层保护**从空转变为有效**。
（`cubic` 目录下也有 CONTCAR，但 `PHASE_SPECS` 无对应条目——应变 pilot 只覆盖 m/o/t。）

### 6.5 主仓保全提交与两条 FerroX 线的分叉

主仓（`D:\Code-X\Ferroelectric knowledgegraph\KG agent\hfo2-ferro-kg`，
分支 `codex/benchmark-design-workflow`，有 upstream）此前有 56 个已修改 +
588 个真实未跟踪文件（12.74 MB）。已提交为 `cd1d9bb`（154 文件，+21910/−455），
**纯保全，未与本分支做任何合并**。

#### 先补的 `.gitignore` 缺口

主仓 `.gitignore` 已逐条忽略约 24 个 `data/` 子目录，但漏了 4 个，
导致 `git add -A` 会吞进 **464 个文件 / 7.75 MB 的论文内容**：

| 路径 | 规模 | 内容 |
|---|---|---|
| `data/literature_intake/**/05_fulltext/by_paper/*/pages/*.md` | 456 文件 / 7.59 MB | 论文**全文**提取 |
| `data/literature/hzo_{,missing_}high_quality_download_list.{csv,json}` | 4 文件 / 1.24 MB | `abstract_snippet` 列，**301 条出版社摘要原文**，最长 700 字符 |
| `data/relevance_screening/` | 5 文件 / 1.58 MB | 筛选报告 |
| `data/unrelated_pdfs/` | 2 文件 / 1.39 MB | 隔离报告 |

补规则前先确认这 4 个路径下**已跟踪文件数为 0**，故规则纯属增量、不会 untrack 任何东西。
补后未跟踪集从 588 降到 **100 个代码/文档文件 / 0.64 MB**，`data/` 下剩 0。
其余 12 个 `data/literature/**` 文件经扫描确认只含书目元数据（标题/DOI/URL/SHA-256/文件名），
无正文，但按"`data/` 不进版本控制"的既有约定一并忽略。

#### 提交前检查

- 154 个待提交文件全部扫描凭据（含 `.pptx`/`.docx` 容器内部）：**0 命中**
- 含 CRLF 的文件：**0 个**（主仓虽是原生 Windows 开发，`.sh` 实测已是 LF）
- 暂存总量 1.31 MB，最大单文件 119.7 KB

#### 故意未提交：2 个 pptx 的删除

主仓工作区缺 2 个**已跟踪**文件：

```
reports/HfO2-FerroKG_强相关筛查与模型验证进展.pptx
reports/HfO2-FerroKG_论文级工作流与计算闭环汇报.pptx
```

它们在 HEAD 里有、在本 worktree 磁盘上也有（3 个 pptx 齐全），内容不会丢。
但**不知道它们为何在主仓消失**，而记录一个没人要求过的删除比让它继续显式可见更糟，
所以从暂存区撤出，保留为未暂存的 ` D`，留给了解历史的人处理。

#### 未解决的分叉

两条 FerroX 实现并存，**谁都不是谁的超集**：

| 主仓分支 | 本 worktree 分支 |
|---|---|
| `backend/services/simulation_runtime.py` | `backend/services/ferrox_runner.py` |
| `computations/simulation/`（含 `ferrox.lock.json`、`jax_landau_smoke.py`、`requirements-simulation.txt`、`templates/inputs_hzo_mfim`） | `backend/services/ferrox_postprocess.py` |
| `scripts/setup_simulation_runtime.sh` | `backend/services/ferrox_benchmark.py` |
| `pipelines/62_check_simulation_runtime.py` | `backend/schemas/ferrox_experiment_schema.py` |
| `tests/test_simulation_runtime.py` | `app/pages/14_FerroX_相场模拟.py` |
| `computation_planner.py` 里 engine 改名 `FerroX_AMReX_with_JAX_calibration` + JAX/AMReX 质量门 | `scripts/install_ferrox_wsl.ps1` |

**流水线编号冲突**：`62` 在主仓是 `check_simulation_runtime.py`，
在本分支是 `analyze_phase_contradictions.py`（本分支另有 63–67）。

另有 12 个文件两边内容实质不同（+356/−45），最大的是
`backend/services/llm_extractor.py`（162 行）、`app/pages/12_材料设计工作流.py`（89 行）、
`tests/test_llm_extractor.py`（78 行）。

调和这些需要决定哪套架构胜出、`62` 怎么重编号，属设计决策而非机械合并，**未做**。

### 6.6 主仓 pytest：三个危险、四个写入者、一个守卫

主仓**不能直接跑 `pytest`**。三个已实测确认的危险：

1. 它的 `backend/core/config.py` 是修复前版本，`db_path` 解析为
   `PROJECT_ROOT/data/hfo2_ferrokg.sqlite3`，而在主仓那**就是 1 GB 生产库**；
   `backend/db/session.py:connect()` 以读写方式打开并调用 `init_database()`。
2. 它原本**没有 `tests/conftest.py`**，所以 worktree 那个污染治理守卫不存在。
3. 它的 `.env` 设了 `HFO2_FERROKG_USE_LLM=true` 且带真实 `DASHSCOPE_API_KEY`，
   而 `config.py` 会 `load_dotenv(PROJECT_ROOT/".env")` —— 任何触及 LLM 客户端的
   测试都会**真实花钱**。

因此新增 `scripts/migration/run_main_repo_pytest.sh`：重定向数据库到临时文件、
强制 `USE_LLM=false`、清空两个 API key，并**以断言方式验证三者生效**（不是假定），
再对生产库与 `data/` 做跑前跑后指纹。主仓路径从 worktree 的 `.git` 指针推导，
故 Windows 与 WSL 两侧通用。

#### 谁在写生产数据

逐个测试文件单独跑并做指纹比对，实测**恰好 4 个**：

| 测试文件 | 写入位置 |
|---|---|
| `test_llm_extractor.py` | `data/extraction_candidates/`、`data/ontology/hfo2-ferrokg-v2.3/` |
| `test_multi_model_validator.py` | `data/exports/model_validation_*`（4 个文件） |
| `test_computation_validation.py` | `data/computation/validation_jobs/` |
| `test_computation_workflow.py` | `data/computation/simulation_runtime/runtime_status.json` |

先前"`tmp_path` 引用数为 0 的就是元凶"的启发式**完全错误**：被怀疑的
`test_hfo2_extractor.py` 和 `test_visual_asset_linker.py` 什么都没写，而大量使用
`tmp_path` 的 `test_ontology_builder.py`（6 次）、`test_model_comparison.py`（10 次）、
`test_simulation_runtime.py`（17 次）也什么都没写。

根因在服务层：多个函数在**调用时**用 `PROJECT_ROOT / "data" / ...` 计算输出位置
（`multi_model_validator.py:301`、`hfo2_extractor.py:609`、
`ontology_builder.load_ontology_bundle:260`），测试没传覆盖参数就会落到生产路径。

另发现 `test_llm_extractor.py` 与 `test_multi_model_validator.py` **单独跑会失败、
在完整套件里却通过** —— 存在执行顺序依赖，是另一个隔离缺陷。

#### 守卫的实现取舍（实测数据）

`tests/conftest.py` 的 `PROTECTED_RUNTIME_DIRS` 已扩到含 `data/computation`，
两个仓库的文件**逐字节相同**（SHA-256 一致）。实现上踩了两个坑，都用实测数字纠正：

| 版本 | 主仓耗时 | 说明 |
|---|---:|---|
| SHA-256 全量哈希、3 个目录 | 23.73 s | 原始版，不含 `data/computation` |
| SHA-256 全量哈希、4 个目录 | 164.84 s | `data/computation` 154 MB / 3447 文件，哈希两遍 |
| 改用 `(size, mtime_ns)` | 93.57 s | 仍慢 |
| 加 `tools/` 排除，但用 `rglob` 过滤 | 68.42 s | **过滤发生在遍历之后，遍历成本一点没省** |
| 改用 `os.walk` 并在 `dirnames` 上剪枝 | **27.56 s** | 回到基线 |

两个结论：

- **不用内容哈希**。一是慢；二是测试把生产文件重写成**完全相同的字节**仍然是在写
  生产树，内容哈希会放过它，`(size, mtime_ns)` 不会。改用后者后守卫从只报 2 个文件
  变成报全 **10 个**。
- **排除 `data/computation/tools/`**（2921 / 3447 文件，85%）。那是 vendored 的
  FerroX/AMReX 工具链源码，不是本项目产出的研究产物，也不是任何服务的输出目标。
  这是有意识的盲区，已写在 conftest 里。

修好这 4 个测试并非每个一行：`check_simulation_runtime` 的
`status_path=DEFAULT_STATUS_PATH` 是**定义时绑定**的默认参数，事后改模块常量无效；
而 `PROJECT_ROOT` 有 48 处用在 `data/` 之外，其中 `llm_extractor.py:72` 是调用时读取
`prompts/hfo2_extraction_prompt.md`，统一重定向会直接弄崩测试。详见 conftest 模块 docstring。

#### 本次运行的实测结论

守卫加入后（主仓提交 `0edd566`）：

```
主仓    : 1 failed, 128 passed, 1 error   (error 即守卫触发，列出 10 个文件)
worktree: 197 passed, 0 skipped
生产库  : 0e1a0e04fa8b025b326d5e19cb651700e07ba98160c416fda03fa2d4fb912228  前后一致
```

修复 4 个写入者后（主仓提交 `8b2e6d6`，7 文件 +43/−15）：

```
主仓    : 1 failed, 128 passed            (26.20 s，守卫不再触发)
```

修复 jax 测试后（主仓提交 `cf4bb4b`，1 文件 +78/−1）：

```
主仓    : 130 passed, 0 failed, 0 error   (28.17 s，exit 0)
worktree: 197 passed, 0 skipped
```

两个独立机制同时确认无写入：conftest 守卫不再报错，且隔离脚本自己对
`data/exports`、`data/extraction_candidates`、`data/ontology`、`data/computation`
下 **3475 个文件**的指纹比对报 `no watched data/ file changed`。生产库哈希仍一致。

修法是给 4 个服务各加一个覆盖参数（`run_extraction` 的
`output_path`/`ontology_output_dir`、`_write_outputs` 与 `run_multi_model_validation`
的 `output_dir`、`import_computation_results` 的 `output_dir`、
`run_computation_workflow` 的 `runtime_status_path`），再让 3 个测试传 `tmp_path`；
`test_computation_validation.py` 无需改动。其中：

- `import_computation_results` 是**唯一的默认行为变更**：`report_dir` 从
  `DEFAULT_JOBS_DIR` 改为 `output_dir or results_path.parent`。改前已核实只有
  `pipelines/45_import_computation_results.py` 和 `computation_workflow.py` 两个调用方，
  且**全仓没有任何代码读取** `validation_jobs/*_normalized_results.csv` 或
  `*_import_report.md`，属终端产物，无下游消费者。
- `computation_workflow` 的 `runtime_status_path` 不能简单转发：
  `check_simulation_runtime` 的 `status_path` 是定义时绑定的默认参数，传 `None`
  会用 `None` 覆盖默认值而非回退，故只在显式给出时才转发。
  **`simulation_runtime` stage 本身保留** —— worktree 是把整个 stage 连同 import
  一起删掉的（FerroX 分叉的一部分），照搬会删掉本分支的 FerroX 集成。
- `multi_model_validator.py` 改后与 worktree 版本**逐字节相同**；另 3 个服务仍有
  差异（`hfo2_extractor` 2/4、`computation_validation` 22/8、`computation_workflow` 2/20），
  那些是既有的 FerroX 与 `synthetic_fixture` 分叉，非本次引入。

#### 剩下的那 1 个失败：已修（主仓 `cf4bb4b`）

原失败是 `test_simulation_runtime.py::test_read_only_probe_preserves_matching_smoke_results`，
断言 `refreshed["jax"]["smoke_status"] == "ok"` 实得 `"not_run"`。

**生产代码是对的，没有改动。** `_carry_forward_smoke` 开头就是：

```python
if not current.get("installed") or previous.get("smoke_status") in {None, "not_run"}:
    return
```

运行时未安装却把旧 smoke 结果当作当前有效，那才是错误行为。**缺陷在测试**：
它隐含假设 jax 已安装，却没有声明这个前提。

也不能靠往状态文件注入结果来绕：`_jax_status()` 每次都从真实环境重算 `installed`，
状态文件影响不了那个决定是否 carry forward 的分支。所以改为
`monkeypatch` 掉 `simulation_runtime._jax_status`，让替身在 `run_smoke=True` 时成功、
在 `run_smoke=False`（只读探测）时从 `"not_run"` 起步 —— 这样只读结果里的 `"ok"`
**只可能**来自 `_carry_forward_smoke`，测试才真正验证了它名字所声称的东西，
且不依赖 FerroX 栈。这也与同一个测试原本处理 ferrox 的方式一致（桩二进制 + 手工注入状态）。

同时新增 `test_read_only_probe_discards_smoke_when_identity_changes` **防止该修法空转**：
carry-forward 以 `(version, jaxlib_version, python)` 为身份键，新测试先在一个身份下
记录 smoke，再用不同身份重探，断言旧的 `"ok"` **不会**被带过来、且 `backend` 不存在。
若 monkeypatch 只是把 `"ok"` 硬写进每个结果，这个测试就会失败。

注意 `simulation_runtime.py` 与 `test_simulation_runtime.py` **只存在于主仓**，
worktree 两个都没有——这是 FerroX 分叉的一部分（见 §6.5）。

#### 顺带清理：陈旧的 macOS `.pyc`

两仓共 715 个项目 `.pyc`（主仓 342、worktree 373），其中主仓 107 个、worktree 112 个
**内嵌 macOS 绝对路径**。因为 `cp -p` 保留了 mtime 且文件大小未变，Python 判定缓存有效
并直接加载，导致 pytest 回溯显示 `/Users/<mac-user>/Codex/...` 且源码行为 `???`。
已全部删除（`__pycache__` 是纯派生缓存，已被 gitignore，会自动重建）。mac 的 `.venv`
未受影响（其 10,720 个 `.pyc` 保留）。

清理时 `find` 范围伸进了 `data/`，删除了 `data/literature_intake/.../Doped-HfO2/`
（一个克隆的第三方仓库）内的 **13 个 `.pyc`**。已验证无损害：13 个 `.py` 源文件全部在、
`git log` 正常、`git -c core.filemode=false diff --stat` **输出为空即零内容差异**。
该仓库另有 134 个文件显示为 `100644 → 100755` 模式变化，源于 `/mnt/d` 9p 挂载合成
模式位，是既有现象，与本次操作无关。

## 7. 尚未完成的迁移项

按影响排序：

1. **`requirements.txt` 本身仍是错的**（见 §6.3）。`requirements-wsl.lock.txt`
   已覆盖 WSL 侧的复现需求，但 `requirements.txt` 仍漏掉代码实际 import 的
   `numpy` / `pillow` / `requests`，仍声明从未被 import 的 `networkx`，
   且与主仓那份互相矛盾。建议按 lock 文件的直接依赖部分重建，
   或至少在 README 里指明 WSL 环境以 lock 文件为准。
2. **未 push**：分支 `codex/phase-competition-research` 仍无 upstream。
   本地提交已是备份，但推送到公开仓前需先决定两件事：
   `reports/**` 中若干 JSON 的路径字段含 macOS 用户名 `<mac-user>`；
   `locked_validation_selection_frame.csv` ×2 含 480 条论文标题+DOI。
3. **主仓与 worktree 分支存在未调和的分叉**（见 §6.5）。主仓已提交保全
   （`cd1d9bb`），但两条 FerroX 实现与 `pipelines/62` 编号冲突仍未解决，
   需要你决定架构取舍，不是机械合并能处理的。
4. **`.env` 未落到 worktree** —— 这是 §6.3 的有意选择，不是遗漏。
   需要真实 LLM 调用时再建。
5. **`python pipelines/10_validate_results.py` 未运行**（README 建议的另一项验证）。
6. **路径含空格**：`/mnt/d/Code-X/Ferroelectric knowledgegraph/KG agent/hfo2-ferro-kg`
   含两处空格。Python `pathlib` 无碍，但所有 shell 脚本与命令行必须加引号。
7. **FerroX 未安装**：`ase`/`jax`/`jaxlib`/`cmake` 不在本次 venv 内，
   `app/pages/14_FerroX_相场模拟.py` 相关功能不可用。需要时按
   `scripts/install_ferrox_wsl.ps1` 单独安装。
8. **git worktree 仍是跨 OS 的妥协配置**（见 §6.2）：在 WSL 内
   `git worktree list` 会把该条目显示为 Windows 路径。功能无影响，
   且已用 lock 挡住 `prune`，但**不要解锁**，也不要在 WSL 内跑
   `git worktree prune` / `remove`。

## 8. 本次迁移未做的事

- 未修改任何 `reports/**`、`docs/**`（除本文件）、`computations/**` 下既有文件的内容
- 未改写 `pipeline_runs.stats_json`
- 未改动旧副本 `D:\KG agent\`
- 未运行 Streamlit 或任何 pipeline
- **未 push**；未设置 upstream
- 未安装 FerroX 相关依赖（`ase`/`jax`/`jaxlib`）
- 未对全部 84 个包做完整版本冻结
- 未触碰 TEFS / 云端，未产生任何付费资源，未发起任何 LLM 调用

已做但需明确标注的：本地提交 `033b4e8` 一并纳入了此前未提交的相竞争工作
（pipelines 35–67、约 30 个 backend services、约 30 个测试文件、reports），
**这些 diff 未经逐个审查**。该事实已写入提交信息末段，避免后来者误以为经过 review。

