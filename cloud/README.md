# HfO2-FerroKG TEFS Cloud Bridge

These scripts are meant to be run inside TEFS Cloud Shell after uploading
`hfo2_ferrokg_compute_bridge.tar.gz`.

Safety defaults:

- The legacy multi-phase route never submits paid compute; it exits fail-closed
  even if `--submit` is passed.
- No secret, API key, SSH key, cloud token, or POTCAR is stored here.
- POTCAR is referenced only through a cloud-side path that you provide.

Expected TEFS flow:

1. Upload package with `rz`.
2. Unpack in `/root/home` or a subdirectory.
3. Upload four HfO2 POSCAR files into `data/computation/manual_structure_intake/raw`.
4. Run `cloud/01_import_manual_structures.sh`.
5. Run `cloud/02_prepare_hfo2_smoke_run.sh` after setting `TEFS_POTCAR_HFO2`.
6. Use `cloud/03_submit_hfo2_smoke.sh` only to inspect legacy dry-run
   descriptors; the script now refuses `--submit`.
7. Use the reviewed single-job package under
   `computations/phase_strain_pilot/tefs_single_job_pilot_v0_1` for any
   separately approved paid smoke test.
8. Use `tefs squeue`, `tefs get JOBID`, and `tefs rsync JOBID` to monitor and sync.

The first smoke test runs only HfO2 polymorph phase stability. HZO supercells
come later after the TEFS/VASP setup is reproducible.
