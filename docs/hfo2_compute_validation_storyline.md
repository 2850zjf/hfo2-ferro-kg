# HfO2-FerroKG 计算闭环故事线

## 一句话主线

HfO2-FerroKG 的核心不是简单把文献做成数据库，而是把文献中的样品级证据、机器学习预测和可复现计算验证串成一个材料设计闭环。

## 汇报逻辑

1. **问题**：文献知识图谱能总结 HfO2/HZO 的工艺-结构-性能关系，但仅靠文献抽取和预测模型容易停留在相关性，缺少物理可行性约束。
2. **方法**：先把 PDF 证据抽取成样品级 KG 和 Pr/2Pr benchmark，再用训练/验证集模型筛选候选，最后把关键候选转成计算任务。
3. **最小闭环**：在进入复杂 HZO、掺杂和缺陷之前，先审计已有 HfO2 四相 smoke output，验证原始文件、结构、能量归一化和收敛证据的解析路径。该审计不写生产数据库。
4. **结构证据**：只从实际保存的 POSCAR/CONTCAR 出发；结构来源声明若没有可核验的 source ID、引用和原始下载哈希，就作为 provenance 缺口，不反向补写为 Materials Project 事实。
5. **能量证据**：当前只允许报告直接从真实 static OUTCAR 复算的零应变 smoke 值：m = 0、o = 84.3、t = 166.3、c = 269.4 meV/f.u.（相对 m）。这些数值证明已有 VASP 输出可解析，不构成论文级相边界。
6. **质量证据**：离子松弛停止标志来自 `relax.output`，静态总能来自 OUTCAR/vasprun.xml，并记录文件 SHA-256。当前没有保存 relax 阶段 OUTCAR/vasprun.xml，因此完整应力、每一步原子力和可重放 provenance 仍不充分。
7. **回写边界**：在 ENCUT/k-density 收敛、POTCAR 元数据、相保持和共同机械边界全部通过前，任何能量都不得写成 KG 的 `verified` descriptor。人工 CSV 只能标为 `user_reported_unverified`，pytest 数值只能标为 `synthetic_fixture`。
8. **下一步**：机制只选双轴应变；先冻结 P2₁/c #14、Pca2₁ #29、P4₂/nmc #137 与共同 (001) 外延几何，再运行 `3 phases × 3 common in-plane constraints`。不能把各相相对于自身晶格的同名应变直接互比，也不从 DFT 结果声称验证退火温度或时间。

## 论文角度的定位

这不是要把计算做成单独一篇 DFT 论文，而是把计算作为 KG-driven materials design 的物理反馈层。当前第一版结果只能证明“保存的 VASP smoke 输出可以被独立审计”；数值收敛与新应变矩阵完成后，才能讨论可复现的相竞争趋势。
