# Computational Workflows

This directory contains lightweight, reproducible calculation starters that connect HfO2-FerroKG design candidates to physics checks.

Safety boundary:

- Input structures must come from a documented source such as Materials Project, ICSD-derived data, or a manually cited publication structure.
- No `POTCAR`, API key, SSH key, cloud token, or paid database file should be committed.
- Generated structures and VASP input folders should be written under `data/computation/`, which is ignored by Git.
- The first calculation is a smoke test, not a final physical claim.

Current starter:

- `mp_hfo2_phase_smoke_test`: fetch HfO2 polymorph structures from Materials Project and prepare VASP phase-stability folders.
- `simulation`: use JAX for differentiable Landau checks and FerroX/AMReX for phase-field job preparation and tiny runtime smoke tests.

Install and verify the optional simulation runtime:

```bash
bash scripts/setup_simulation_runtime.sh
```

The local FerroX build is serial and CPU-only. Large MPI/GPU simulations are
prepared for a manually confirmed Tencent Cloud environment; the setup script
does not submit cloud jobs.
