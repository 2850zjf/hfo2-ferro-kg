# HfO2 m/o/t 双轴应变 VASP dry-run pilot

本目录只定义和生成一个预注册的三点计算合同，不运行或提交 VASP，也不包含 POTCAR、密钥、`.env` 或数据库内容。

## 冻结范围

- 材料：HfO2。
- 相：m-P2_1/c #14、极性 o-Pca2_1 #29、t-P4_2/nmc #137。
- 可比 cell：Hf4O8，12 原子，4 个 HfO2 formula units。
- t 相使用整数矩阵 `[[1,1,0],[-1,1,0],[0,0,1]]` 构造 `sqrt(2) x sqrt(2) x 1` 超胞。
- 共同方形面内基准 `L_ref`：真实、已弛豫 t-CONTCAR 的 `sqrt(2) x sqrt(2)` 面内矢量长度。
- 初始三点：factor 0.99、1.00、1.01，即工程应变 -1%、0、+1%。
- pilot 约束：`ISIF = 2`，固定全部晶格矢量，只弛豫离子。

这三个点不是完整相边界，也不允许外推具体退火温度、时间、实验相分数、Pr 或 2Pr。

## 真实结构来源

生成器只读取原工作区以下快照中的 `monoclinic/CONTCAR`、`orthorhombic/CONTCAR` 和 `tetragonal/CONTCAR`：

```text
/Users/jinfengzhang/Codex/Ferroelectric knowledgegraph/KG agent/hfo2-ferro-kg/
data/computation/tefs_hfo2_phase_smoke_20260622/runs/hfo2_phase_smoke/
```

三份源文件 SHA-256 已冻结在代码中；缺失、内容变化、化学计量不符或相对称合同失败时，只输出 blocked contract，不生成 POSCAR。

## 独立的生成后预检

Builder 内部只执行 phase-specific operation/motif contract，不调用或冒充 spglib。

2026-08-24 对当前 `generated_v0_1` 的 9 个未运行 POSCAR 做过一次独立外部预检：临时使用 spglib 2.7.0、`symprec=1e-3`，不修改原 `.venv`。结果为：

- 3 个 m job：P2_1/c #14；
- 3 个 o job：Pca2_1 #29；
- 3 个 t job：P4_2/nmc #137；
- 最短原子距离约为 1.998--2.078 A。

该预检仅对 manifest 中逐项记录的 POSCAR SHA-256 有效。它不是 VASP 结果，也不覆盖未来弛豫后的结构；每个 relaxed output 仍必须重新计算空间群和最短原子距离。

## 生成

```bash
cd "/Users/jinfengzhang/Codex/hfo2-phase-competition-worktree"

"/Users/jinfengzhang/Codex/Ferroelectric knowledgegraph/KG agent/hfo2-ferro-kg/.venv/bin/python" \
  pipelines/67_prepare_phase_strain_jobs.py \
  --output-dir computations/phase_strain_pilot/generated_v0_1
```

输出目录必须是新的 worktree 子目录。命令拒绝覆盖已有目录。

每个 job 只含：

- `POSCAR`
- `INCAR.relax`
- `INCAR.static`
- `KPOINTS`
- `POTCAR_NOT_INCLUDED.txt`
- `job_manifest.json`

没有运行/提交脚本。根 manifest 保存源路径与 hash、空间群合同、整数变换矩阵、三点应变、共同 `L_ref`、约束策略和 publication blockers。

## Publication blockers

生成成功仅代表几何和文件合同可复现，仍至少需要：

1. 冻结源结构的 DOI/repository/license provenance；
2. 用独立 spglib/结构工具复核源结构和每个弛豫后结构的空间群；
3. 论证所选 `(001)` coherent orientation 与实际问题相关；
4. 建立并验证允许外平面/剪切弛豫且保持面内约束的协议；
5. 冻结同一 POTCAR/functional，并完成 ENCUT、k-point、force/stress 收敛；
6. 检查相身份、结构坍塌、电子/离子收敛及每 formula unit 能量归一化；
7. 在扩展应变网格前先审阅三点 pilot，不能用三点拟合稳健 crossing；
8. 保持解释边界为指定 0 K DFT 设置下的相对稳定性趋势。
