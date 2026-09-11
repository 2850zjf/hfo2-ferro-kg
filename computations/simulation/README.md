# JAX and FerroX Simulation Runtime

This optional runtime connects the evidence-to-computation workflow to two
different layers:

```text
reviewed literature / DFT descriptors
-> JAX Landau-parameter sensitivity and differentiable calibration
-> FerroX TDGL + Poisson domain simulation
-> AMReX plotfiles and derived descriptors
-> KG / benchmark write-back after quality review
```

## Safety and scientific boundary

- JAX runs locally on the CPU by default on Apple Silicon.
- The local FerroX build is serial `NOACC`, `MPI=OFF`, and intended for tiny
  smoke tests only.
- Large FerroX simulations belong on the manually confirmed Tencent Cloud
  environment. No cloud job is submitted by these scripts.
- The bundled MFIM input contains example coefficients derived from the
  official FerroX example. Replace them with cited, unit-consistent HZO
  parameters before scientific use.
- A successful runtime smoke test is not a validation of HZO physics.

## Reproducible setup

```bash
bash scripts/setup_simulation_runtime.sh
```

The installer writes generated source, build products, plotfiles, and runtime
status below `data/computation/`, which is ignored by Git. The exact upstream
FerroX and AMReX commits are recorded in `ferrox.lock.json`.

## Runtime check

```bash
python pipelines/62_check_simulation_runtime.py --jax-smoke --ferrox-smoke
```

## JAX starter

```bash
python computations/simulation/jax_landau_smoke.py \
  --output data/computation/simulation_runtime/jax_landau_smoke.json
```

The default coefficients are dimensionless and exist only to verify JIT,
vectorization, and automatic differentiation.

## FerroX output

FerroX writes AMReX plotfiles such as `plt00000000`. The runtime checker uses
an `8 x 8 x 8` mesh and one time step. It does not replace mesh/time-step
convergence, coefficient provenance, electrical-boundary sensitivity, or
comparison with experimental observables.
