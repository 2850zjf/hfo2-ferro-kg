# TEFS single-job workflow smoke v0.1

This package prepares one **non-publication** VASP workflow smoke test for the
generated `monoclinic/biaxial_0p0000` geometry.  Its purpose is to verify the
TEFS transfer -> licensed POTCAR assembly -> VASP relax -> VASP static -> safe
result retrieval chain.  It does not establish a strain phase boundary.

## Frozen scientific input

- Source job: `generated_v0_1/jobs/monoclinic/biaxial_0p0000`
- Structure: Hf4O8, 12 atoms, four HfO2 formula units
- Expected unrelaxed identity: P2_1/c, space-group number 14
- POSCAR SHA-256: `fe3c64e78ad8ce419eb018e6815f0ce73f646f2ac0bde85ec0c422e6335be9b7`
- Constraint: `ISIF=2`, fixed lattice and ionic relaxation only
- Static restart policy: `ISTART=0`, `ICHARG=2`; the static calculation starts
  from a fresh atomic charge-density superposition rather than reusing an
  unspecified charge-density file.

The factor-1 cell is the common square in-plane pilot reference.  It is not the
zero-stress monoclinic equilibrium cell, and the fixed-cell method is not the
final epitaxial method.

## Live TEFS observations on 2026-08-24/25 and 2026-08-30

- Project session: authenticated; active queue was empty.
- Region: `ap-beijing`
- Instance: `S6.4XLARGE32` (16 vCPU, 32 GB)
- Displayed price before the first submission: on-demand `2.51 RMB/hour`; spot
  `0.146 RMB/hour`.  The live listing later showed `0.1506 RMB/hour` spot.
- The CLI help claimed `spot_paid=false` means `SPOTPAID`, but real job `151024`
  with that exact value reported `instance_charge_type=POSTPAID_BY_HOUR` and
  `instance_price=2.92 RMB/hour`.  Actual service behavior therefore overrides
  the contradictory help text for this release decision.
- The reviewed retry keeps `spot_paid=false` because job `151024` is the only
  observed mapping.  It is conservatively budgeted as on-demand, and the actual
  charge type must be checked immediately after launch.  The user authorized
  direct execution below `100 RMB`; this package applies the stricter existing
  one-job safety ceiling of `4 RMB`.
- On 2026-08-30 at `2026-08-30T14:24:06+00:00`, the queue was empty and
  `S6.4XLARGE32` displayed `2.92 RMB/hour` on-demand and `0.146 RMB/hour`
  spot. The old image ID `img-k5up9lv7` was absent from the live image list.
- Current image: `img-45t2j6r7`, CPU VASP image for S5/S6/M5/M6/C5/C6
- VASP path advertised by TEFS: `/usr/local/vasp.6.3.0/bin`
- Licensed cloud-side PAW titles:
  - `PAW_PBE Hf_pv 06Sep2000`
  - `PAW_PBE O 08Apr2002`

The price is an observation, not a promise.  Immediately before the corrected
retry, require the live on-demand price to be at most `3.50 RMB/hour`.  With the
30-minute timeout plus a 60-second termination grace period, the compute-only
ceiling at that gate is about `1.81 RMB`.  At the observed `2.92 RMB/hour`, it
is about `1.51 RMB`.  Disk and storage charges are separate and are not claimed
to be zero.  The total authorization ceiling remains `4.00 RMB`, covering
uncertainty and abnormal release handling; it is a safety ceiling, not a cost
forecast.

## Safety properties

- One node, one job, 16 MPI ranks.
- `spot_paid=false`, conservatively budgeted as on-demand from real job `151024`;
  the contradictory CLI-help mapping is not trusted and the next job's actual
  charge type must be verified after launch.
- 30-minute hard timeout; the scientific runner contains no retry loop.
- 50 GB disk and `public_ip=false`.
- Exactly one manual corrected resubmission is authorized.  This workflow will
  not issue another submission if it fails; TEFS control-plane retry behavior
  is not asserted by the JSON.
- The command is passed directly to TEFS without nested shell quoting.  Job
  `151024` proved that a nested `bash -lc '...'` wrapper is parsed incorrectly.
- POTCAR is never placed in this repository or the upload archive.
- The licensed POTCAR is assembled only under `/root/home/...` in TEFS.
- The result archive explicitly lists safe files and never includes `POTCAR`.
- The result remains `publication_ready=false` until all convergence and
  constrained-cell blockers in the parent manifest are resolved.

## Local dry-run only

```bash
bash computations/phase_strain_pilot/tefs_single_job_pilot_v0_1/build_upload_bundle.sh
```

This creates a tar archive and SHA-256 sidecar under `output/`.  Building it
does not contact TEFS.

## Remote release gate

Do not upload or submit until the user confirms all of the following at action
time:

1. the current project is authorized for the institution's VASP licence;
2. the Tencent Cloud account has sufficient balance and an acceptable alarm;
3. one job budgeted as on-demand is approved with displayed on-demand price
   <= 3.50 RMB/hour;
4. the 30-minute timeout, runner-without-retry policy, 50 GB disk, and no public
   IP are accepted;
5. the total one-job authorization ceiling is 4.00 RMB, and the user accepts
   that timeout/failure produces diagnostics only, not a scientific result.

After approval, upload the generated tar archive to `/root/home`, unpack it,
run `bash prepare_licensed_potcar.sh`, recheck `tefs squeue` and the live price,
then submit the corrected retry exactly once with:

```bash
tefs hpc tefs_hpc.preview.json
```

Monitor with `tefs squeue`.  Retrieve only
`result_bundle_no_potcar.tar.gz` with a file-filtered `tefs rsync` command.
Never download or synchronize the job's `POTCAR` file.

The runner starts the static stage only after relaxation returns zero, reports
the ionic accuracy criterion, has a normal timing footer, contains no selected
fatal VASP markers, preserves Hf4O8 composition, and preserves the fixed cell.
The static stage must return zero, reach `EDIFF`, and have a normal footer.
