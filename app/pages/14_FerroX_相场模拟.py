from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

PROJECT_ROOT_LOCAL = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_LOCAL) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_LOCAL))

from app.support import render_top_nav
from backend.schemas.ferrox_experiment_schema import FerroXExperimentSpec
from backend.services.ferrox_postprocess import (
    compare_grid_convergence,
    process_run_outputs,
    read_plotfile,
    summarize_replicates,
)
from backend.services.ferrox_runner import (
    RUNS_ROOT,
    FerroXConfig,
    create_grid_convergence_specs,
    create_replicate_specs,
    create_screening_specs,
    launch_run,
    load_curve_csv,
    prepare_run,
    read_run_state,
    render_inputs,
    stop_run,
)


BENCHMARK_CANDIDATE = {
    "title": "Effects of plasma gas interface processing on ferroelectric property of TiN/Hf0.5Zr0.5O2/TiN ferroelectric device",
    "doi": "10.1515/htmp-2025-0098",
    "file": r"D:\Code-X\Ferroelectric knowledgegraph\KG agent\Paper PDF\2026_High_Temperature_Materials_Processes_Effects_plasma_gas_interface_processing_ferroelectric_property_TiN_Hf0_5Zr0.pdf",
    "status": "候选：缺少已核实的 Ec 数值和 P-V 测试频率，禁止定量标定",
}


def _make_spec(
    preset: str,
    lateral_nm: float,
    cell_nm: float,
    thickness_nm: float,
    voltage_v: float,
    voltage_step_v: float,
    settle_steps: int,
    plot_interval: int,
    fraction: float,
    spread: float,
    seed: int,
    mpi: int,
    omp: int,
) -> FerroXExperimentSpec:
    return FerroXExperimentSpec.model_validate(
        {
            "name": "HZO MFM baseline" if preset == "mfm_baseline" else "HZO phase/orientation screen",
            "preset": preset,
            "thickness_nm": thickness_nm,
            "grid": {
                "lateral_x_nm": lateral_nm,
                "lateral_y_nm": lateral_nm,
                "cell_size_nm": cell_nm,
            },
            "voltage": {
                "mode": "quasistatic_triangle",
                "voltage_min_v": -voltage_v,
                "voltage_max_v": voltage_v,
                "voltage_step_v": voltage_step_v,
                "settle_steps": settle_steps,
                "source": {
                    "kind": "simulation_assumption",
                    "reference": "default +/-3 V; benchmark frequency not yet verified",
                },
            },
            "microstructure": {
                "tetragonal_fraction": fraction,
                "orientation_spread_deg": spread,
                "random_seed": seed,
            },
            "plot_interval": plot_interval,
            "mpi_ranks": mpi,
            "omp_threads": omp,
            "quantitative_calibration": False,
        }
    )


def _run_controls(prefix: str, allow_microstructure: bool) -> FerroXExperimentSpec:
    size_col, grid_col, thickness_col = st.columns(3)
    lateral = size_col.selectbox("横向尺寸 (nm)", [16.0, 32.0], key=f"{prefix}_lateral")
    cell = grid_col.selectbox("网格间距 (nm)", [0.5, 1.0], key=f"{prefix}_cell")
    thickness = thickness_col.number_input("HZO 厚度 (nm)", 1.0, 100.0, 10.0, 0.5, key=f"{prefix}_thickness")
    v_col, dv_col, settle_col, save_col = st.columns(4)
    voltage = v_col.number_input("扫描幅值 (V)", 0.1, 20.0, 3.0, 0.1, key=f"{prefix}_voltage")
    voltage_step = dv_col.number_input("电压步长 (V)", 0.01, 2.0, 0.25, 0.05, key=f"{prefix}_dv")
    settle = settle_col.number_input("每个电压点最多步数", 10, 1_000_000, 5000, 100, key=f"{prefix}_settle")
    plot_interval = save_col.number_input("场保存间隔", 1, 1_000_000, 100, 10, key=f"{prefix}_plot")
    fraction, spread = 0.0, 0.0
    if allow_microstructure:
        micro_a, micro_b, micro_c = st.columns(3)
        fraction = micro_a.selectbox("四方相体积分数", [0.0, 0.1, 0.25, 0.4], format_func=lambda x: f"{x:.0%}", key=f"{prefix}_fraction")
        spread = micro_b.selectbox("取向离散度", [0.0, 15.0, 30.0], format_func=lambda x: f"{x:.0f}°", key=f"{prefix}_spread")
        seed = micro_c.number_input("随机种子", 1, 2_147_483_647, 1, key=f"{prefix}_seed")
    else:
        seed = st.number_input("随机种子", 1, 2_147_483_647, 1, key=f"{prefix}_seed")
    mpi_col, omp_col = st.columns(2)
    mpi = mpi_col.number_input("MPI 进程", 1, 64, 4, key=f"{prefix}_mpi")
    omp = omp_col.number_input("每进程 OMP 线程", 1, 64, 4, key=f"{prefix}_omp")
    return _make_spec(
        "phase_orientation_screen" if allow_microstructure else "mfm_baseline",
        lateral, cell, thickness, voltage, voltage_step, settle, plot_interval,
        fraction, spread, int(seed), int(mpi), int(omp),
    )


def _launch(spec: FerroXExperimentSpec, prefix: str) -> None:
    run_id = st.text_input("运行 ID", value=f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}", key=f"{prefix}_run_id")
    with st.expander("FerroX 输入预览"):
        st.code(render_inputs(spec), language="ini")
    if st.button("确认参数并启动", type="primary", key=f"{prefix}_launch"):
        try:
            run = prepare_run(run_id, spec)
            state = launch_run(run, config=FerroXConfig.from_env())
            st.session_state["ferrox_run_dir"] = run["run_dir"]
            st.success(f"已启动：PID {state['pid']}。完成的 plotfile 会持续保留。")
        except Exception as exc:
            st.error(f"启动失败：{exc}")


def _result_view() -> None:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    run_dirs = sorted(
        [path for path in RUNS_ROOT.iterdir() if path.is_dir() and (path / "run.json").is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not run_dirs:
        st.info("还没有由工作台创建的 FerroX 运行。")
        return
    selected = st.selectbox("运行记录", run_dirs, format_func=lambda path: path.name)
    state = read_run_state(selected)
    a, b, c, d = st.columns(4)
    a.metric("状态", state.get("status", "unknown"))
    b.metric("plotfile", len(list(selected.glob("plt*"))))
    c.metric("FerroX", str(state.get("ferrox_commit", "-"))[:8])
    d.metric("用途", "仅模拟")
    st.caption(f"参数哈希：{state.get('parameter_sha256', '-')} · KG 写回：关闭")
    action_a, action_b = st.columns(2)
    if state.get("status") == "prepared":
        if action_a.button("启动这个已准备任务", type="primary", use_container_width=True):
            try:
                run = {
                    "run_dir": str(selected),
                    "input_path": str(selected / "inputs.hzo"),
                    "state_path": str(selected / "run.json"),
                }
                launched = launch_run(run, config=FerroXConfig.from_env())
                st.success(f"已启动：PID {launched['pid']}")
            except Exception as exc:
                st.error(f"启动失败：{exc}")
    elif action_a.button("停止任务", use_container_width=True):
        st.success("停止信号已发送，已完成的场文件已保留。") if stop_run(selected) else st.warning("任务未运行或已结束。")
    if action_b.button("生成曲线、指标和 VTI", use_container_width=True):
        try:
            result = process_run_outputs(selected)
            st.success(f"后处理完成：{result['plotfile_count']} 个 plotfile。")
        except Exception as exc:
            st.error(f"后处理失败：{exc}")
    log_path = selected / "stdout.log"
    if log_path.is_file():
        with st.expander("运行日志"):
            st.text_area("日志末尾", log_path.read_text(encoding="utf-8", errors="replace")[-16000:], height=300, label_visibility="collapsed")
    curve_path = selected / "curve.csv"
    if curve_path.is_file():
        frame = pd.DataFrame(load_curve_csv(curve_path))
        if {"electric_field_mv_cm", "polarization_uc_cm2"} <= set(frame):
            st.subheader("P-E 回线")
            st.line_chart(frame, x="electric_field_mv_cm", y="polarization_uc_cm2")
    metrics_path = selected / "metrics.json"
    if metrics_path.is_file():
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        st.subheader("提取指标")
        st.json(metrics)
    plotfiles = sorted(path for path in selected.glob("plt*") if path.is_dir())
    if plotfiles:
        with st.expander("畴切片预览"):
            chosen = st.select_slider("场文件", options=plotfiles, format_func=lambda path: path.name)
            if st.button("加载中间 Z 切片"):
                try:
                    pz = np.asarray(read_plotfile(chosen)["fields"]["Pz"])
                    plane = pz[pz.shape[0] // 2]
                    scale = max(float(np.max(np.abs(plane))), 1e-15)
                    red = np.clip((plane / scale + 1) / 2, 0, 1)
                    blue = 1 - red
                    image = np.dstack((red, np.zeros_like(red) + 0.15, blue))
                    st.image(image, caption=f"{chosen.name} · 中间 Z 层 · 红/蓝表示相反 Pz", clamp=True)
                except Exception as exc:
                    st.error(f"切片读取失败：{exc}")
    vti_files = sorted((selected / "vti").glob("*.vti")) if (selected / "vti").is_dir() else []
    if vti_files:
        picked = st.selectbox("ParaView 文件", vti_files, format_func=lambda path: path.name)
        st.download_button("下载 VTI", picked.read_bytes(), file_name=picked.name)

    comparison_rows = []
    comparison_metrics = {}
    for candidate in run_dirs:
        metrics_file = candidate / "metrics.json"
        experiment_file = candidate / "experiment.json"
        if not metrics_file.is_file() or not experiment_file.is_file():
            continue
        metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
        comparison_metrics[candidate.name] = metrics
        experiment = json.loads(experiment_file.read_text(encoding="utf-8"))
        micro = experiment.get("microstructure", {})
        comparison_rows.append(
            {
                "运行": candidate.name,
                "四方相": micro.get("tetragonal_fraction"),
                "取向离散度": micro.get("orientation_spread_deg"),
                "种子": micro.get("random_seed"),
                "Pr (uC/cm2)": metrics.get("pr_uc_cm2"),
                "2Pr (uC/cm2)": metrics.get("two_pr_uc_cm2"),
                "Ec (MV/cm)": metrics.get("ec_mv_cm"),
                "回线面积": metrics.get("loop_area_uc_mv_cm2"),
                "畴壁密度": metrics.get("domain_wall_density"),
                "状态": metrics.get("status"),
            }
        )
    if comparison_rows:
        st.subheader("运行对比")
        st.dataframe(pd.DataFrame(comparison_rows), use_container_width=True, hide_index=True)
        selected_replicates = st.multiselect("三种子汇总", list(comparison_metrics), max_selections=3)
        if selected_replicates:
            st.json(summarize_replicates([comparison_metrics[name] for name in selected_replicates]))
        grid_a, grid_b = st.columns(2)
        coarse_name = grid_a.selectbox("1.0 nm 运行", [""] + list(comparison_metrics), key="coarse_grid_run")
        fine_name = grid_b.selectbox("0.5 nm 运行", [""] + list(comparison_metrics), key="fine_grid_run")
        if coarse_name and fine_name:
            convergence = compare_grid_convergence(comparison_metrics[coarse_name], comparison_metrics[fine_name])
            if convergence.get("converged"):
                st.success(f"网格收敛通过：{convergence['differences_percent']}")
            else:
                st.error(f"网格收敛未通过，不得形成正式结论：{convergence}")


st.set_page_config(page_title="FerroX HZO 相场研究", layout="wide", initial_sidebar_state="collapsed")
render_top_nav()
st.title("FerroX · HZO 相场研究")
st.caption("Hf0.5Zr0.5O2 · TiN/HZO/TiN · simulation_only · 不写入实验 facts")

with st.expander("基准论文与参数来源", expanded=True):
    st.write(BENCHMARK_CANDIDATE["title"])
    st.code(f"DOI: {BENCHMARK_CANDIDATE['doi']}\n文件: {BENCHMARK_CANDIDATE['file']}")
    st.warning(BENCHMARK_CANDIDATE["status"])
    st.caption("当前 Landau/梯度系数来自 FerroX 官方 MFIM 示例，尚未针对该论文拟合。默认 ±3 V 是模拟假设。")

baseline_tab, screen_tab, results_tab = st.tabs(["基准 MFM", "相比例 / 取向研究", "运行与结果"])
with baseline_tab:
    st.subheader("TiN/HZO/TiN 基线")
    st.caption("TiN 用顶底 Dirichlet 电势边界表示；计算域内只有 HZO。建议先用 16 nm 试跑，再切换 32 nm。")
    baseline_spec = _run_controls("baseline", allow_microstructure=False)
    _launch(baseline_spec, "mfm")

with screen_tab:
    st.subheader("受控合成微结构")
    st.caption("四方相区域冻结为非铁电；晶粒为确定性 5×4 分区，不代表真实 TEM 微结构。")
    screen_spec = _run_controls("screen", allow_microstructure=True)
    matrix = create_screening_specs(screen_spec)
    st.dataframe(
        pd.DataFrame(
            [{"条件": item.name, "四方相": item.microstructure.tetragonal_fraction, "取向离散度": item.microstructure.orientation_spread_deg, "种子": item.microstructure.random_seed} for item in matrix]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.info("矩阵按钮只准备 12 个输入，不自动启动计算。")
    matrix_id = st.text_input("矩阵批次 ID", value=f"screen_{datetime.now():%Y%m%d_%H%M%S}")
    if st.button("生成 12 个任务输入"):
        try:
            prepared = [prepare_run(f"{matrix_id}_{index + 1:02d}", item) for index, item in enumerate(matrix)]
            st.success(f"已准备 {len(prepared)} 个任务；可在运行记录中逐个确认启动。")
        except Exception as exc:
            st.error(f"矩阵准备失败：{exc}")
    st.divider()
    st.subheader("单个条件试跑")
    _launch(screen_spec, "micro")
    st.divider()
    st.subheader("代表条件复算与网格收敛")
    st.caption("筛选后在上方选定一个代表条件，再生成三种子或 1.0/0.5 nm 配对输入；仍不会自动启动。")
    batch_a, batch_b = st.columns(2)
    if batch_a.button("为当前条件生成 3 个种子", use_container_width=True):
        try:
            items = create_replicate_specs(screen_spec)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            [prepare_run(f"rep_{stamp}_seed{item.microstructure.random_seed}", item) for item in items]
            st.success("已准备 seed 1、2、3 三个任务。")
        except Exception as exc:
            st.error(f"生成失败：{exc}")
    if batch_b.button("生成 1.0 / 0.5 nm 收敛对", use_container_width=True):
        try:
            items = create_grid_convergence_specs(screen_spec)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            [prepare_run(f"grid_{stamp}_{str(item.grid.cell_size_nm).replace('.', 'p')}nm", item) for item in items]
            st.success("已准备两个网格任务；只有 Pr 与 Ec 差异均不超过 5% 才可形成正式结论。")
        except Exception as exc:
            st.error(f"生成失败：{exc}")

with results_tab:
    _result_view()
