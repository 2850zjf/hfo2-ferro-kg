# TEFS Cloud Shell Connection Guide

This guide uses the web shell only. It does not require exposing SSH keys,
Tencent Cloud credentials, API keys, or POTCAR files to the local project.

It is aligned with the local TEFS manuals:

- `TEFS 操作手册/ExUserGuide.pdf`
- `TEFS 操作手册/tefs-ai-document.pdf`

## What You Have

The TEFS Cloud Shell supports:

- `rz`: upload a local file into the current cloud directory.
- `sz file`: download a cloud file to the local browser.
- `tefs hpc filename.json`: submit a compute job from a prepared directory.
- `tefs squeue`: inspect running jobs.
- `tefs get JOBID`: inspect compute node information.
- `tefs rsync JOBID`: sync job outputs back to the submission directory.
- `tefs terminate JOBID`: terminate a running job.

Manual notes:

- Persistent user work should be placed under `/root/home` or `/root/public`.
- Web-created experiment outputs are under `/root/experiments`.
- The first automated route should use `/root/home`.

This is enough for the first HfO2-FerroKG computation smoke test.

## Upload Package

In the cloud shell:

```bash
mkdir -p /root/home/hfo2_ferrokg
cd /root/home/hfo2_ferrokg
rz
```

Select the local package:

```text
outputs/cloud_transfer/hfo2_ferrokg_compute_bridge.tar.gz
```

Then unpack it:

```bash
tar -xzf hfo2_ferrokg_compute_bridge.tar.gz
cd hfo2_ferrokg_compute_bridge
bash cloud/00_check_tefs_shell.sh
```

## Add Structures

For the first smoke test, put these four downloaded HfO2 structures into:

```text
manual_structure_intake/raw/
```

Required names:

```text
hfo2_monoclinic.POSCAR
hfo2_tetragonal.POSCAR
hfo2_cubic.POSCAR
hfo2_orthorhombic.POSCAR
```

If you upload the structures separately from the browser shell:

```bash
cd /root/home/hfo2_ferrokg/hfo2_ferrokg_compute_bridge/data/computation/manual_structure_intake/raw
rz
```

Then import them into the prepared job packages:

```bash
cd /root/home/hfo2_ferrokg/hfo2_ferrokg_compute_bridge
bash cloud/01_import_manual_structures.sh
```

## POTCAR Policy

Do not upload POTCAR back to the local project or GitHub. On the cloud machine,
point the script to a licensed combined HfO2 POTCAR:

```bash
export TEFS_POTCAR_HFO2=/root/POTCAR/path/to/HfO2/POTCAR
```

If your cloud folder stores element POTCAR files separately, create the combined
HfO2 POTCAR on the cloud machine according to your institutional VASP setup, then
set `TEFS_POTCAR_HFO2` to that combined file.

## First Run Boundary

The first computation should only test HfO2 polymorph phase stability:

- monoclinic
- tetragonal
- cubic
- orthorhombic

It is a calculation setup validation step. It does not directly prove
experimental Pr/2Pr or HZO design performance.

Prepare the VASP folders:

```bash
cd /root/home/hfo2_ferrokg/hfo2_ferrokg_compute_bridge
bash cloud/02_prepare_hfo2_smoke_run.sh
```

Before submission, query available machine types and images:

```bash
tefs cvm
tefs images
```

Set the machine and image. Replace values with the entries available in your
TEFS project:

```bash
export TEFS_REGION=ap-beijing
export TEFS_INSTANCE_TYPE=GN10Xp.2XLARGE40
export TEFS_IMAGE_ID=img-g3gm0m37
export TEFS_NTASKS_PER_NODE=1
export TEFS_VASP_BIN=/usr/local/vasp.6.3.0/bin/vasp_std
```

Legacy descriptor dry-run only:

```bash
bash cloud/03_submit_hfo2_smoke.sh
```

Paid submission through this legacy multi-phase script is disabled.  Do not
use the former `--submit` route; it lacked the single-job timeout and billing
guards now required.  Prepare the reviewed single-job pilot instead:

```bash
bash computations/phase_strain_pilot/tefs_single_job_pilot_v0_1/build_upload_bundle.sh
```

Monitor and sync:

```bash
tefs squeue
tefs get JOBID
tefs rsync JOBID
```

## Return Results

After calculations complete, collect a preliminary results CSV:

```bash
bash cloud/04_collect_hfo2_results.sh
sz hfo2_phase_smoke_results.csv
```

The CSV contains:

- job_id
- phase
- final_energy_eV
- formula_units
- energy_per_fu_eV
- converged
- notes

Then import it locally with:

```bash
.venv/bin/python pipelines/45_import_computation_results.py hfo2_phase_smoke_results.csv --job-id <job_id>
```
