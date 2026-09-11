# HfO2/HZO 计算主线文献审计与创新定位

更新日期：2026-08-13

## 结论先行

现有文献库的规模已经足够大，但旧筛选口径不适合作为新的计算论文主线。当前相关性表共覆盖 1,144 篇 PDF，其中 1,116 篇被旧规则判为 `core`；按题名与文件名中的 DFT、NEB、机器学习势、分子动力学、相场、声子、域壁和能垒等信号做保守复核后，计算相关候选约为 78 篇。这个数字仅用于库覆盖诊断，不替代后续全文级人工/LLM 复核。

因此，下一阶段不再扩大通用工艺抽取，也不再以 Pr/2Pr 回归作为唯一论文问题。推荐将主线改为：

> 构建证据可追溯、能垒感知、可执行的 HfO2/HZO 多尺度计算知识图谱，研究热力学稳定性与动力学可达性何时一致、何时失配，并用 DFT/NEB、机器学习势分子动力学和相场计算对关键反例进行闭环验证。

## 为什么这个点值得做

1. 文献通常分别报告相对能、切换路径、域壁迁移、应变、缺陷或电场响应，缺少可以跨论文比较的统一 `ComputationalCase` 表达。
2. 低相对能或高极化并不自动意味着低切换势垒；静态相稳定性模型无法回答有限温度、电场和域壁参与下的可达性问题。
3. 2025-2026 年的新工作正在快速改写中间相、晶格模、域壁和尺寸效应的认识，单纯复述旧综述会很快过时。
4. 项目已经具备 VASP/TEFS、FerroX、JAX 和文献多模态解析基础，适合做“文献证据 -> 可执行参数 -> 计算验证 -> 回写图谱”的闭环，而不是只做展示型知识图谱。

## 推荐本体中心

主实体由 `SampleRecord` 转为 `ComputationalCase`，工艺样品保留为补充证据。

| 实体/对象 | 最低必需字段 |
|---|---|
| `StructureState` | composition、space_group、orientation、cell/supercell、structure_source、structure_id |
| `SimulationProtocol` | code/version、functional、pseudopotential、cutoff、k-mesh、convergence、ensemble |
| `Perturbation` | strain tensor、electric field、temperature、pressure、defect type/site/concentration、interface |
| `TransitionPath` | initial/final/intermediate phase、path label、NEB images、barrier、normalization basis |
| `Observable` | total/relative/free energy、polarization、coercive field、domain-wall energy/velocity、phonon mode |
| `EvidenceArtifact` | source DOI、page/figure/table/equation、verbatim evidence、parser/model version |
| `ValidationRun` | input hash、cloud job id、status、output files、convergence checks、deviation from literature |

## 论文级 Benchmark

- 数据单位：一个带来源证据的 `ComputationalCase`，而不是一个 PDF 或一个孤立数值。
- 主任务：相稳定性排序、切换路径分类、势垒数值抽取/预测、物理一致性判定、证据定位。
- 划分：按 DOI 分组，训练集与验证集不共享同一论文；验证集作为主指标。另保留 2026 年新论文作前瞻性外部检查，不参与调参。
- 主要指标：macro-F1、top-k phase ranking accuracy、barrier MAE/RMSE、pathway confusion matrix、evidence exact/partial match、unit-normalization error rate。
- 强制约束：能量归一化基准必须显式记录；路径起点/终点相必须匹配；极化、应变和电场方向必须带坐标系；不同泛函结果不得混为同一真值。

## 当前库覆盖诊断

按保守关键词审计，78 篇计算候选中主题存在交叠：缺陷/界面 33、域壁/切换动力学 16、DFT/NEB 13、计算综述/通用建模 9、相场/LGD 8、声子/软模 5、机器学习势/分子动力学 1。后者的低计数也受题录质量影响；项目中实际已有一篇 2026 年电场机器学习势论文，但必须修正元数据并纳入计算本体。

旧相关性筛选的 `core=1,116/1,144` 过宽，不能直接作为计算 benchmark。后续应先建立 `computation_core`、`computation_context`、`experiment_support`、`exclude` 四级标签，再启动定向全文抽取。

## 近期执行顺序

1. 补齐 Excel 中的 P0 文献及其 Supporting Information、代码或公开数据。
2. 对现有约 78 篇计算候选做全文级计算相关性复核，并修复题名/DOI。
3. 用 20-30 篇高质量论文建立计算 gold set，优先覆盖相能、路径、势垒、应变、缺陷、域壁和电场条件。
4. 先复现四相静态能排序，再选择 2-3 条存在争议的切换路径运行 CI-NEB。
5. 将已提供的电场机器学习势工作作为有限温动力学基线，并用 FerroX 检查原子尺度势垒结论在域尺度是否仍成立。

