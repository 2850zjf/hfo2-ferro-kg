# 外部 VASP 压缩包：安全解压与计算工作流审阅

审阅日期：2026-08-29  
结论状态：`workflow_reference_only_not_publication_evidence`

## 1. 结论先行

这个压缩包可以用来学习 TEFS 单 GPU 运行目录、VASP 两阶段
`optimization -> static SCF` 组织方式，以及如何联合检查 OUTCAR、OSZICAR、
CONTCAR 和 vasprun.xml。它不能直接用于当前 HfO2/HZO 相竞争结论。

最重要的限制是：

- 压缩包没有 m-HfO2、HZO、应变扫描、缺陷或极化计算；
- o/t-HfO2 只做了固定各自 c 轴、放松面内晶格的受限优化，没有共同应变条件，
  也没有统一高精度静态能；
- 两个界面静态作业都明确报告
  `WARNING: chargedensity file is incomplete`；
- 两个名为 `*-AlN` 的模型实际完全没有 Al，而是 TiN/HfO2 slab；
- 没有 README、结构 DOI/repository 来源、计算公式、自动工作流或收敛矩阵。

因此，包内数值只能作为工作流诊断和参数比较点，不能声称验证 o/t/m 相边界、
HZO、铁电极化、相分数、退火温度或退火时间。

## 2. 来源、安全边界和解压核验

- 源文件：
  `/Users/<mac-user>/Codex/Ferroelectric knowledgegraph/nal-hfo_1753200171_1613132854-dl.tgz`
- 大小：194,191,360 bytes
- SHA-256：
  `03dd8b1814707d3421d0c8a2d1b4906387b6f0a3e76cbb4cdd6565ed34afd6b8`
- 扩展名虽为 `.tgz`，实际是未压缩的 GNU/POSIX tar。
- 原包共 181 个条目：166 个普通文件、15 个目录；没有绝对路径、`..` 路径、
  链接、设备或 `.env` 成员。
- 原包含 7 个非空、受许可约束的 `POTCAR`。本次没有读取、散列、显示或复制
  其内容。

安全副本位于：

```text
/Users/<mac-user>/Codex/hfo2-phase-competition-worktree/
external_data/nal-hfo_no_potcar_20260829/nal-hfo/
```

解压时显式排除了全部 `POTCAR`。逻辑上剩余 159 个文件；由于 macOS 默认文件
系统大小写不敏感，每个作业中的空 `REPORT` 与非空 `report` 冲突，7 对文件合并
为 7 个非空 `report`，所以物理文件数是 152。对每个冲突组保留了有实际 VASP
输出的非空文件。逐文件比较源 tar 成员和安全副本：152/152 个文件的大小与
SHA-256 一致，内容不一致数为 0，安全副本中的 `POTCAR` 数为 0。
安全副本根目录另有 1 个本地 `README_SAFE_EXTRACTION.md`，它不是源包成员，
因此当前目录总文件数为 153。

包内没有 shell/Python 工作流脚本。7 个 `sub_gpu.json` 在 tar 中带可执行权限，
但它们只是 JSON 描述符，本次没有执行其中的命令。没有运行或提交任何 VASP
任务，没有产生 TEFS 费用，也没有读取 `.env` 或写生产数据库。

## 3. 目录与真实组成

```text
nal-hfo/
├── 1Hf-AlN/
│   ├── opt/   # Ti10 N10 Hf8 O16 slab，44 原子
│   └── scf/
├── 2O-AlN/
│   ├── opt/   # Ti10 N10 Hf10 O20 slab，50 原子
│   └── scf/
├── o-HfO2TiN/o-HfO2/opt/  # Hf4 O8，12 原子
└── t-HfO2TiN/
    ├── t-HfO2/opt/         # Hf4 O8，12 原子
    └── tin/opt/             # Ti4 N4，8 原子
```

`1Hf-AlN` 和 `2O-AlN` 的 POSCAR 都没有 Al。所有 INCAR，包括纯 HfO2 和
TiN，均使用 `SYSTEM=ALNTIN`，POSCAR 标题也统一为 `Layer`。这些是复制模板
遗留信息，不能作为科学标签。包内也完全没有 Zr。

五个优化目录的 `SYMMETRY` 文件分别把输入结构标记为空间群 #1、#25、#29、
#137 和 #225。OUTCAR 对 o/t 输入分别报告 C2v/D4h 点群，与 Pca21/P42/nmc
标签相容；但没有对最终 CONTCAR 做独立 spglib 审计，因此不能把输入标签当成
最终相身份的独立验证。

## 4. 实际计算流程和设置

七个作业均报告：

- VASP 6.4.2（20Jul23，build 2024-03-13）；
- OpenACC 初始化成功，检测到 1 张 GPU；
- PBE；OUTCAR 中的 PAW 标题为 `Ti_sv 26Sep2005`、`N 08Apr2002`、
  `Hf_pv 06Sep2000`、`O 08Apr2002`，按各体系取用；
- `ENCUT=600 eV`、`PREC=Normal`、`ISMEAR=0`、`SIGMA=0.05`、
  `POTIM=0.2`、`ISIF=3`；
- 有效默认值包括 `ISYM=2`、`LREAL=F` 和 `LASPH=F`，但它们未显式冻结在
  INCAR 中。

具体设置：

| 类型 | k 网格 | EDIFF | EDIFFG | NSW / IBRION | 电荷输出 |
|---|---:|---:|---:|---|---|
| 两个 slab 优化 | 4x4x1 MP | 1e-4 | -0.05 eV/A | 200 / 2 | `LCHARG=F` |
| 两个 slab 静态 | 4x4x1 MP | 1e-5 | 静态中无作用 | 0 / -1 | `ICHARG=1, LCHARG=T` |
| o/t-HfO2 优化 | 5x5x5 MP | 1e-6 | -0.01 eV/A | 100 / 2 | `LCHARG=F` |
| TiN 优化 | 6x6x6 MP | 1e-6 | -0.01 eV/A | 100 / 2 | `LCHARG=F` |

KPOINTS 首行的 `K-Spacing Value ... 0.040` 只是标题；实际使用的是上表显式网格，
不是 `KSPACING` 自动网格。

七个 `sub_gpu.json` 内容相同：1 个 `GN10Xp.2XLARGE40` 节点、
`ap-shanghai`、1 个 MPI rank、GPU VASP 6.4.2，命令为
`module load ... && mpirun ... vasp_std &> report`。`module`、镜像、region、
`--allow-run-as-root` 和 bash 的 `&>` 都是平台特定写法，不能直接迁移。
文件中的 `spot_paid=true` 只作为历史描述符记录；在当前 TEFS CLI 帮助与实际
账单语义存在矛盾的情况下，不能据此推断当时计费模式。

两个 slab 的 `opt/CONTCAR` 与相应 `scf/POSCAR` 逐字节一致：

- 1Hf：
  `71a63e352421b2489a6f93b4c0b9ffb33b25efda5e159132242158736b40a51f`
- 2O：
  `8fc82d18bca25663e9d14ea4fb4b68390a2e315616f5468e456ff1f49bceda29`

这证明几何交接正确，但目录中没有复制脚本或调度依赖，因而它是手工衔接，
不是可重放的自动工作流。

## 5. 完整性、收敛与数值

7/7 个 OUTCAR 有正常 timing footer，7/7 个 vasprun.xml 可完整解析，XML
`calculation` 数与 OSZICAR 一致。优化作业都出现 VASP 的离子精度达成标志，
未触及 NSW；电子迭代未触及 NELM。这只表示达到了所选阈值，不代表 ENCUT、
k 网格或方法已经收敛。

| 作业 | 离子步 | 最终 E0 (eV/cell) | 最终最大力 (eV/A) | 耗时 (s) | 主要备注 |
|---|---:|---:|---:|---:|---|
| `1Hf-AlN/opt` | 194/200 | -430.11571288 | 0.0497369 | 17,792.958 | 刚好低于 0.05 阈值 |
| `1Hf-AlN/scf` | 静态 1 步，35 电子步 | -430.05850657 | 0.0485133（诊断） | 250.225 | 电荷密度不完整警告 |
| `2O-AlN/opt` | 124/200 | -450.83818671 | 0.0460620 | 15,835.428 | 大幅结构重构 |
| `2O-AlN/scf` | 静态 1 步，37 电子步 | -451.02756892 | 0.0477890（诊断） | 393.949 | 电荷密度不完整警告 |
| `o-HfO2/opt` | 20/100 | -121.75436020 | 0.0068528 | 310.994 | 只有受限优化能 |
| `t-HfO2/opt` | 3/100 | -121.42846253 | 0.0006030 | 49.760 | 只有受限优化能 |
| `TiN/opt` | 10/100 | -78.69837765 | 0.0000000 | 61.688 | 高对称零力；仍有约 1.42 kB 应力 |

两个 slab 静态目录的 `report` 均明确写出
`WARNING: chargedensity file is incomplete`。它们使用 `ISTART=0, ICHARG=1`，
而对应优化目录中的 CHG、CHGCAR、WAVECAR 都是 0 字节。优化到静态的 E0
变化分别为：

- 1Hf：+0.05720631 eV；
- 2O：-0.18938221 eV。

最终电子循环虽然达到 EDIFF，但这个重启链没有可靠建立，且两个能量变化方向
相反。若要使用界面静态结果，应从最终结构以 `ICHARG=2` 重新自洽启动，或先
验证一个完整 CHGCAR 的来源、结构/网格兼容性和 hash。

归档完整性也不均衡：7 个 WAVECAR 和 7 个平台 stdout 日志全部为 0 字节；
5/7 个 CHG、CHGCAR 为 0 字节，只有两个 slab 静态目录保存了非空电荷密度。
这是 `LWAVE=F`、优化阶段 `LCHARG=F` 与命令把标准输出重定向到小写
`report` 的组合结果；空占位文件不能被当作可恢复重启数据。

其他应保留的诊断信息：

- `ZBRENT: can't locate minimum`：1Hf 优化 19 次、2O 优化 17 次、
  o-HfO2 优化 2 次；最终虽过力阈值，但优化路径不平滑；
- 全部 `report` 在退出时记录 IEEE invalid/divide-by-zero/underflow/inexact
  flags；没有伴随非零退出或缺失 timing footer，但不能从审计记录中删除；
- OpenMPI 找不到 OpenFabrics 接口后回退到其他传输，主要是性能提示；
- 没有发现 kill、timeout、EDDDAV、BRMIX 或未达到 NELM/NSW 的失败。

INCAR 注释本身有误：`ISTART=0` 不是“读取现有 WAVECAR”；`ICHARG=1/2`
也不是注释声称的固定密度非自洽能带计算。应以参数的实际 VASP 语义为准。

## 6. OPTCELL、slab 与结构合理性

所有优化目录都有同一个非标准 `OPTCELL`：

```text
110
110
000
```

POSCAR/CONTCAR 的实测结果是 c 向量完全不变，a/b 和面内剪切可变：

| 作业 | 初始 -> 最终晶格长度 (A) | 面内/体积变化 |
|---|---|---|
| `1Hf-AlN/opt` | 6.7254x6.7254x24.7421 -> 6.6054x6.5175x24.7421 | 面积 -4.8276% |
| `2O-AlN/opt` | 6.7254x6.7254x26.1976 -> 7.5008x7.3424x26.1976 | 面积 +21.7085% |
| `o-HfO2/opt` | 5.2693x5.0401x5.0745 -> 5.2659x5.0469x5.0745 | 体积 +0.0695% |
| `t-HfO2/opt` | 5.0817x5.0817x5.2247 -> 5.0725x5.0725x5.2247 | 体积 -0.3644% |
| `TiN/opt` | 4.2535^3 -> 4.2419x4.2419x4.2535 | 体积 -0.5446% |

VASP 官方文档说明，`LATTICE_CONSTRAINTS` 配合 IBRION=1/2 的结构弛豫从
VASP 6.4.3 才提供；包内报告的是 6.4.2，且使用的文件名是 `OPTCELL`。因此
必须把它视为镜像中的未记录补丁/扩展，不能假设 stock VASP 或当前 TEFS
VASP 6.3.0 会支持同样行为。官方说明：
<https://vasp.at/wiki/LATTICE_CONSTRAINTS>

如果把 a/b 定义为外延面、c 定义为法向，这个 mask 的实际方向是“放松面内、
固定面外”，与当前相竞争协议需要的“固定共同面内应变、放松面外和离子”相反。
而且 o、t 和 TiN 都固定了各自不同的初始 c，所以它们不是完全弛豫体相参考。

两个 TiN/HfO2 模型还有约 15 A 的初始最大周期 z 间隙，是带真空的 slab。
优化后该间隙约为 15.274 A（1Hf）和 18.216 A（2O）。没有启用
`IDIPOL/LDIPOL`；如果 slab 有净偶极，应至少进行偶极修正与真空厚度敏感性
检查。VASP 官方表面偶极修正说明：
<https://vasp.at/wiki/Electrostatic_corrections>

2O 的面内面积增加 21.7%，slab 占据厚度明显缩小，最终最短 Ti-N 距离仅
1.722885 A；作为对照，包内 TiN 参考的最短 Ti-N 距离为 2.120968 A。
即使数值达到力阈值，也必须先做结构可视化、成键和模型物理合理性复核。

## 7. o/t 能量能说明什么

o-HfO2 和 t-HfO2 都是 Hf4O8，使用相同主要设置及相同 OUTCAR PAW 标题，
因此可以做一个严格限定的内部诊断：

```text
t - o = +0.32589767 eV / Hf4O8
      = +81.4744 meV / HfO2
```

也就是：在这组 PBE、600 eV、5x5x5、`PREC=Normal`、各自固定不同 c 的
受限优化中，o 分支比 t 分支低约 81.5 meV/f.u.。

这与当前分支中 2026-06-22 历史 smoke 原始输出的
`t-o = 82.0313 meV/f.u.` 相差约 0.56 meV/f.u.。两者 OUTCAR 报告相同的
Hf/O PAW 标题，但使用不同 VASP 版本、ENCUT、晶格约束和最终静态协议；外部
包的结构来源也不清楚。因此这个接近只能记录为数值交叉核对，不能叫独立复现，
更不能替代收敛研究。

它不构成相边界，因为缺少：

- m-HfO2 共同参考；
- HZO/Zr；
- 共同面内应变序列和能量交叉；
- bulk 的统一高精度静态计算；
- ENCUT、k 点、PREC、POTCAR 组合与应变步长收敛；
- 最终结构的独立相身份、动力学稳定性/声子检查；
- Berry-phase 极化。

两个 slab 组成、厚度、最终面积和表面终止均不同，其绝对总能量不能直接相减。
即使引入 HfO2/TiN 体相参考，也还需要同一面内应变下的参考、表面/界面数量、
化学势定义及明确的面积归一化公式。

## 8. 对当前相竞争工作流的取舍

可以复用：

1. `relax/`、`static/` 分目录，静态 POSCAR 由 relax CONTCAR 生成；
2. 对结构交接和冻结输入做 SHA-256 绑定；
3. 同时要求 OUTCAR footer、电子/离子标志、OSZICAR 步数、可解析 XML、
   最大力、应力和最终结构检查；
4. 保存 VASP 版本、资源描述符和 POTCAR 标题，但不下载或再分发 POTCAR；
5. 把 600 eV 与 5x5x5 作为收敛矩阵的一个比较点，而不是结论参数。

不能复用：

1. `OPTCELL=110/110/000` 的方向及其未记录补丁；
2. `1Hf-AlN`、`2O-AlN` 和 `SYSTEM=ALNTIN` 的科学标签；
3. `ICHARG=1` 配合不完整 CHGCAR 的静态重启；
4. 不同组成 slab 的裸总能量比较；
5. `PREC=Normal`、界面 `EDIFF=1e-4/EDIFFG=-0.05` 作为发表精度；
6. 任何 HZO、o/t/m 相边界、铁电或退火工艺结论。

当前 runner 把任意 `ZBRENT` 字样都作为失败标志也应重新审阅：这个外部包证明
`ZBRENT: can't locate minimum` 可以出现在最终达到离子阈值且正常结束的优化中。
更稳健的规则应区分可恢复的线搜索提示与真正 fatal 状态，同时保留次数、检查
能量/力轨迹、最终结构和结束标志。反过来，`chargedensity file is incomplete`
应作为静态重启协议失败，而不是被最终 EDIFF 掩盖。

## 9. 下一阶段计算门槛

在释放 m/o/t 应变主矩阵前至少应满足：

1. 冻结 m-P21/c、o-Pca21、t-P42/nmc 的可追溯 Hf4O8 起始结构与独立空间群
   检查；
2. 在同一取向、共同面内晶格和同一 POTCAR/functional 下建立三分支；
3. 固定 a/b/面内剪切，放松 c、允许的面外剪切和内部坐标；先用可审计小体系
   验证约束方向。若 TEFS 6.3.0 无可信 constrained-cell 实现，应做显式 c 搜索，
   不能直接复制这个 OPTCELL；
4. 每个应变点完成 relax 后，以最终结构做统一静态自洽计算；默认 `ICHARG=2`
   或验证完整 CHGCAR 的 hash/兼容性；
5. 完成 ENCUT、k-point density、力、应力和应变步长收敛，并按 HfO2 f.u.
   归一化；
6. 复核每个 relaxed 结构的空间群、最短距离和是否坍塌到其他相；
7. 只有能量交叉对上述设置稳定，才登记为 0 K 指定边界条件下的相竞争边界；
8. 不从 VASP 相对能量直接声称验证具体退火温度、时间、实验相分数或 Pr/2Pr。

## 10. 证据位置

- 安全副本：`external_data/nal-hfo_no_potcar_20260829/nal-hfo/`
- 当前应变协议：`computations/phase_strain_pilot/README.md`
- 当前历史 raw-output 审计：`reports/vasp_raw_output_audit_20260824/vasp_raw_audit.md`
- 研究协议：`docs/phase_competition_research_protocol.md`

本报告只使用安全副本中的非 POTCAR 文件和已有只读审计结果；没有制造任何
计算结果或模型指标。
