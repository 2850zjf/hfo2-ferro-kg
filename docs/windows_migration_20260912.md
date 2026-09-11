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

### 5.3 源码中的硬编码默认值（已于 §6.4 修复）

以下两处曾写死 macOS 绝对路径：

- `backend/services/vasp_raw_audit.py` — `DEFAULT_SOURCE_DIR`
- `backend/services/phase_strain_job_builder.py` — `DEFAULT_SOURCE_ROOT`

两者原本都指向
`/Users/jinfengzhang/Codex/Ferroelectric knowledgegraph/KG agent/hfo2-ferro-kg/data/computation/tefs_hfo2_phase_smoke_20260622/runs/hfo2_phase_smoke`。
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

## 7. 尚未完成的迁移项

按影响排序：

1. **`requirements.txt` 本身仍是错的**（见 §6.3）。`requirements-wsl.lock.txt`
   已覆盖 WSL 侧的复现需求，但 `requirements.txt` 仍漏掉代码实际 import 的
   `numpy` / `pillow` / `requests`，仍声明从未被 import 的 `networkx`，
   且与主仓那份互相矛盾。建议按 lock 文件的直接依赖部分重建，
   或至少在 README 里指明 WSL 环境以 lock 文件为准。
2. **未 push**：分支 `codex/phase-competition-research` 仍无 upstream。
   本地提交已是备份，但推送到公开仓前需先决定两件事：
   `reports/**` 中若干 JSON 的路径字段含 macOS 用户名 `jinfengzhang`；
   `locked_validation_selection_frame.csv` ×2 含 480 条论文标题+DOI。
3. **主仓未提交**：主仓在 `codex/benchmark-design-workflow` 分支上另有
   约 164 条未提交改动，且其 `.gitignore` 缺少 worktree 那 6 条
   `reports/**` 版权保护规则（主仓 `reports/` 下目前无任何 `phase_*` 文件，
   故当前无实际缺口，属防御性缺失）。主仓也没有 `requirements-wsl.lock.txt`。
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

