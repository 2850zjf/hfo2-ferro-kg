# Computational Workflows

This directory contains lightweight, reproducible calculation starters that connect HfO2-FerroKG design candidates to physics checks.

Safety boundary:

- Input structures must come from a documented source such as Materials Project, ICSD-derived data, or a manually cited publication structure.
- No `POTCAR`, API key, SSH key, cloud token, or paid database file should be committed.
- Generated structures and VASP input folders should be written under `data/computation/`, which is ignored by Git.
- The first calculation is a smoke test, not a final physical claim.

Current starter:

- `mp_hfo2_phase_smoke_test`: fetch HfO2 polymorph structures from Materials Project and prepare VASP phase-stability folders.
