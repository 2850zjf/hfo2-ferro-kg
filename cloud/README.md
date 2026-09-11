# HfO2-FerroKG TEFS Cloud Bridge

These scripts are meant to be run inside TEFS Cloud Shell after uploading
`hfo2_ferrokg_compute_bridge.tar.gz`.

Safety defaults:

- No command submits paid compute unless you pass `--submit`.
- No secret, API key, SSH key, cloud token, or POTCAR is stored here.
- POTCAR is referenced only through a cloud-side path that you provide.

Expected TEFS flow:

1. Upload package with `rz`.
2. Unpack in `/root/home` or a subdirectory.
3. Upload four HfO2 POSCAR files into `data/computation/manual_structure_intake/raw`.
4. Run `cloud/01_import_manual_structures.sh`.
5. Run `cloud/02_prepare_hfo2_smoke_run.sh` after setting `TEFS_POTCAR_HFO2`.
6. Dry-run submit with `cloud/03_submit_hfo2_smoke.sh`.
7. Real submit with `cloud/03_submit_hfo2_smoke.sh --submit`.
8. Use `tefs squeue`, `tefs get JOBID`, and `tefs rsync JOBID` to monitor and sync.

The first smoke test runs only HfO2 polymorph phase stability. HZO supercells
come later after the TEFS/VASP setup is reproducible.
