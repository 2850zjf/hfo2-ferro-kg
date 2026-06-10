# HfO2-FerroKG 的故事线与第一个计算入口

## 为什么之前显得牵强

如果故事写成：

```text
知识图谱 -> 机器学习预测 Pr/2Pr -> VASP 计算验证
```

这条线确实不自然。原因是 VASP 不能直接验证文献中测得的 Pr/2Pr。实验 Pr/2Pr 受薄膜厚度、电极、界面、退火、wake-up、缺陷、畴结构、测试条件共同影响，而一个 bulk DFT 能量不能直接对应器件性能。

所以计算闭环不能被包装成“预测准确率验证”。它应该是物理约束反馈层。

## 更自然的论文故事

HfO2/HZO 铁电性能的核心矛盾是：

> 文献中 Pr/2Pr 的高低并不只由材料名决定，而是由相稳定、氧空位、电极/界面、退火路径和器件状态共同决定。

HfO2-FerroKG 的作用是把这些分散信息组织起来：

```text
文献 PDF
-> 样品级事实
-> 材料/工艺/相/器件/性能/证据 KG
-> Pr/2Pr benchmark
-> 预测模型发现高价值候选和不确定区域
-> 计算任务检查候选背后的物理可行性
-> computed descriptors 回写 KG / benchmark
```

这里的计算不是为了替代实验，而是回答更基础的问题：

- 这个候选是否有合理的铁电相稳定性？
- 氧空位是否可能稳定铁电相，还是带来 leakage/fatigue 风险？
- 电极/界面是否可能改变氧化还原和相稳定？
- 薄膜厚度和边界条件是否可能改变畴结构和开关趋势？
- 退火路径是否可能造成缺陷迁移或相转变？

## 第一个计算为什么选 HfO2 多相 smoke test

第一步不要直接做复杂 HZO 掺杂、界面或器件模型。最稳的入口是：

```text
Materials Project HfO2 原始结构
-> monoclinic / tetragonal / cubic / orthorhombic 多相比较
-> VASP relax + static
-> relative energy per formula unit
-> phase-stability descriptor
```

原因：

1. **它足够简单**：不需要先处理掺杂构型、界面终止面和缺陷 charge state。
2. **它足够基础**：HfO2 铁电问题的底层就是亚稳相和稳定相之间的能量竞争。
3. **它足够可复现**：结构来自 Materials Project，代码记录 material_id、space group 和 energy above hull。
4. **它是质量门槛**：如果连 HfO2 多相能量比较都不稳定，就不应该继续做更复杂计算。

## Materials Project 结构原则

本项目不手造第一批结构。第一批结构只来自 Materials Project API。

允许：

- 从 MP 下载 HfO2 多相结构。
- 记录 MP material_id、formula、space group、crystal system、energy above hull。
- 用同一套 VASP 参数做 relax/static。

不允许：

- 手动随便替换 Hf/Zr 生成 HZO，然后把它说成官方结构。
- 在没有说明构型枚举、SQS 或文献来源的情况下使用掺杂结构。
- 把 bulk DFT 结果直接写成实验 Pr/2Pr 预测。

## 计算结果如何回到 KG

第一批 HfO2 多相计算可回写字段：

- `phase_label`
- `mp_material_id`
- `spacegroup_symbol`
- `energy_eV_per_fu`
- `relative_energy_meV_per_fu`
- `calculation_level`
- `functional`
- `encut`
- `kpoints`
- `convergence_status`

第二批才进入更接近材料设计的 descriptors：

- `deltaE_o_m_meV_fu`
- `deltaE_o_t_meV_fu`
- `oxygen_vacancy_formation_energy_eV`
- `oxygen_vacancy_migration_barrier_eV`
- `interface_energy_proxy`
- `phase_field_pr_trend`
- `phase_conversion_risk_score`

## 论文里怎么讲

建议写成：

> We use the literature-derived KG and tiered Pr/2Pr benchmark to identify evidence-supported design candidates and uncertain regions. Instead of treating DFT as a direct predictor of measured polarization, we introduce a computational feedback layer that evaluates physically interpretable constraints such as HfO2 polymorph stability, oxygen-vacancy energetics, interface descriptors, and thin-film switching trends. The first computation is a Materials Project sourced HfO2 polymorph smoke test, used to validate the calculation setup before extending to doped HfO2/HZO and defect/interface models.

中文表达：

> 本工作不把计算作为实验性能的直接替代，而是作为文献驱动设计建议的物理约束反馈层。知识图谱和 benchmark 负责从文献中提取样品级证据和可建模数据；预测模型负责发现高价值候选和不确定区域；计算任务负责检查候选背后的相稳定、缺陷、界面和薄膜开关等机制约束，并将 computed descriptors 回写到 KG 与 benchmark。

这样故事就不是“KG 强行接 VASP”，而是：

```text
文献证据发现问题 -> benchmark 建模定位候选 -> 计算验证物理可行性 -> descriptors 回写提升下一轮设计
```
