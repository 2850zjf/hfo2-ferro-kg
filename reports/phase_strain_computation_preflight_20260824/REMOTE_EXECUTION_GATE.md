# HfO2 phase-strain remote execution gate

- status: `first_job_failed_before_vasp_corrected_retry_authorized`
- new TEFS jobs submitted: `1` (`151024`); VASP executions started: `0`
- paid resources created: `1` short-lived on-demand instance
- files uploaded to TEFS in this review: `2 stale archives`; neither contains
  the current direct-command package, and neither is eligible for resubmission
- production database writes: `0`
- reason: job `151024` established the real billing semantics but its nested
  shell quoting failed before VASP started.  The user authorized one corrected
  on-demand retry below `100 RMB`; the stricter `4 RMB` package ceiling remains.

## Live read-only verification on 2026-08-24

- Authenticated project: `上海大学TEFS项目3`.
- Active queue: empty.
- `ap-beijing` `S6.4XLARGE32`: 16 vCPU, 32 GB, no GPU.
- Displayed live price: on-demand `2.51 RMB/hour`, spot `0.146 RMB/hour`.
- Live `tefs hpc --help` states that `spot_paid=false` selects `SPOTPAID`, but
  real job `151024` with that exact value reported
  `instance_charge_type=POSTPAID_BY_HOUR` and `instance_price=2.92 RMB/hour`.
  The help mapping is therefore not trusted for this release decision.
- CPU VASP image observed on 2026-08-24: `img-k5up9lv7`, then advertised for
  S5/S6/M5/M6/C5/C6 with binaries under `/usr/local/vasp.6.3.0/bin`. It was
  absent from the refreshed 2026-08-30 list and is no longer eligible for
  submission.
- Cloud-side licensed inputs were inspected by title and hash only; no POTCAR was
  downloaded:
  - Hf_pv: `PAW_PBE Hf_pv 06Sep2000`, SHA-256
    `db42cd3e4a48e81eaff18895282e2f5a37f2c8c6a4b9ac45e357affc13c83b84`.
  - O: `PAW_PBE O 08Apr2002`, SHA-256
    `8a74b9a1f5fdb3d0c3e0183c7873177abdbef07d407b310b7edcd9ed0a3eea64`.
- Historical completed jobs in the same account show small HfO2 smoke runs with
  charges of roughly `0.01-0.05 RMB`; these are observations, not forecasts.

The TEFS FAQ states that TEFS itself does not charge a platform fee, while
Tencent Cloud compute and COS storage do.  It also states that VASP use requires
licence evidence.  The 2025 manual price screenshot is not treated as current;
the values above came from the live Cloud Shell.

## First paid release check on 2026-08-25

- Submitted exactly one job: `151024`, name
  `hfo2_m_biaxial_0p0000_workflow_smoke-151024`.
- `tefs squeue` reported `CHARGE=按量计费`, contradicting the CLI-help mapping.
- `tefs get 151024` reported `success(6)` only at the scheduler level,
  `POSTPAID_BY_HOUR`, `2.92 RMB/hour`, start `01:16:08`, end `01:18:14`.
- The compute interval was about 126 seconds, corresponding to an estimated
  compute charge of about `0.10 RMB`; this is an estimate, not a final invoice.
- No `relax/`, `static/`, or `result_bundle_no_potcar.tar.gz` was produced.
- The job command was serialized as nested `bash -lc '...'`, but the worker
  trace showed `CMD='bash -lc command'` followed by `-v: command not found`.
  VASP therefore never started and no scientific result exists.
- An attempted termination found that the short job had already ended.  The
  queue was empty afterward.

## Refreshed no-cost release check on 2026-08-30

- Cloud time: `2026-08-30T14:24:06+00:00`; `tefs squeue` contained only its
  table header, so no active or duplicate jobs were present.
- `S6.4XLARGE32` displayed `2.92 RMB/hour` on-demand and `0.146 RMB/hour` spot.
- The live VASP image for S5/S6/M5/M6/C5/C6 is now `img-45t2j6r7`, with VASP
  binaries still advertised under `/usr/local/vasp.6.3.0/bin`.
- The former descriptor image `img-k5up9lv7` was absent. The descriptor and
  deterministic bundle were therefore rebuilt and hash-checked before any
  upload or paid action.

The corrected command is passed directly, without nested quotes:

```text
command -v timeout >/dev/null && exec timeout --signal=TERM --kill-after=60s 30m bash ./run_vasp_two_step.sh
```

There will be at most one manual corrected resubmission.  The scientific runner
has no retry loop and this workflow will not issue a further submission; TEFS
control-plane retry behavior is not asserted by the JSON.

## Current local retry artifact on 2026-08-30

- The current deterministic upload archive is 6,267 bytes with SHA-256
  `5298d29a898f2b70c520d73850410f6935cacf5a4687f090412f00a030659219`.
  Two consecutive builds produced the same hash.
- The current `tefs_hpc.preview.json` SHA-256 is
  `e473eafb7032ad0083d2083e572ddda8b19a2f9fc313817ba98ad034214357b4`,
  and the package's `INPUT_SHA256SUMS.txt` SHA-256 is
  `89bd58a691af17fcd214b85aa300386684ffab52b41af96ef46254286d2dc871`.
- The archive has 11 members, no POTCAR, no links/devices, no path traversal,
  and all eight entries in `INPUT_SHA256SUMS.txt` verify.
- The static input now explicitly freezes `ISTART=0` and `ICHARG=2` instead of
  depending on VASP restart defaults.
- The runner records `ZBRENT` as a diagnostic rather than rejecting an
  otherwise converged ionic relaxation, while incomplete charge density,
  `EDDDAV`, `ZHEGV failed`, MPI aborts, segmentation faults, `BRMIX`,
  `VERY BAD NEWS`, and internal errors remain fatal.
- Result-packaging failure is fail-closed, and the POTCAR-free result archive
  now includes the job manifest and frozen input checksum list.
- The relevant local tests passed (`26 passed` across the bundle, result
  auditor, and
  phase-strain builder suites). No new remote submission or paid resource was
  created by these checks.

## Cloud-side preparation completed on 2026-08-24

- The first uploaded archive, SHA-256
  `b4663aaa9ea9bc0677f09a678983450bafb9a9955bb30ad3eaf0327fee375a5c`,
  contained `spot_paid=true`.  Its actual billing behavior is unknown because
  the CLI-help mapping is contradicted by job `151024`; the stale archive and
  its extracted directory are quarantined and must never be submitted.
- The corrected cloud archive is 6,721 bytes with SHA-256
  `97bdc9c6070d3d6a8743d17f4459a5d3d77d3e84ae505c51065335f5cecb609c`.
  It was unpacked without overwriting the stale directory, and its JSON was
  checked on TEFS with `spot_paid is False` before further preparation.
- The local builder is deterministic (sorted entries, normalized ownership and
  modes, zero mtime and no macOS xattrs).  After removing the nested wrapper,
  two consecutive builds produced the same 5,881-byte archive with SHA-256
  `21123fe20a6681d42354c2ce9970867405ede6bbb4c80ca64f7d4091f4adc470`.
  At that time, the local `tefs_hpc.preview.json` SHA-256 was
  `a9e4cc598e784f2392e52e9562716b94a12b08a8f1d9d02902ecdf9614cbed76`;
  the package's `INPUT_SHA256SUMS.txt` SHA-256 was
  `d83dd6face6cedbc19b364d3d82f0ede117ec816d22cab483babc7134c0ea50b`.
  The cloud `hfo2_spotfix_stage` directory still contains the nested-command
  versions and is not eligible until both files are synchronized and all input
  checksums pass again.
- The licensed Hf_pv + O POTCAR was assembled only on TEFS.  Its TITEL lines are
  `PAW_PBE Hf_pv 06Sep2000` and `PAW_PBE O 08Apr2002`; the combined cloud-side
  SHA-256 is
  `cc2e3ef4fe34f6ce782810bea297345f8bedd1ea5a55dc81b775427355a3c41e`.
  The binary was neither downloaded nor placed in the upload archive.
- The refreshed queue remained empty.  The refreshed S6.4XLARGE32 listing
  showed `2.51 RMB/hour` on-demand and `0.146 RMB/hour` spot on 2026-08-24;
  the spot column showed `0.1506 RMB/hour` immediately before job `151024` on
  2026-08-25.
- This preparation was used for job `151024`; the scientific runner itself did
  not start because TEFS split the nested shell command incorrectly.

Live CLI help and readable strings in `/usr/sbin/tefs` both map
`spot_paid=false` to `SPOTPAID`, but job `151024` is stronger empirical evidence
for the active service: it used `POSTPAID_BY_HOUR`.  The wrapper does not expose
a separate price-cap or fallback-control field, so the corrected retry is
intentionally budgeted as on-demand rather than relying on the contradictory
help text.

## What was actually verified

- A 2026-06-22 TEFS/VASP 6.3.0 four-phase run exists with raw OUTCAR and vasprun.xml.
- The raw-output audit is in `reports/vasp_raw_output_audit_20260824/`.
- A no-execution m/o/t three-point geometry pilot is in `computations/phase_strain_pilot/generated_v0_1/`.
- The nine generated POSCAR files are Hf4O8 and passed a hash-bound spglib 2.7.0 precheck at `symprec=1e-3`.

The historical TEFS descriptors used one `S6.4XLARGE32` node, 16 tasks, 200 GB disk, region `ap-beijing`, `spot_paid=true`, `public_ip=true`, and VASP 6.3.0.  The meaning of that historical flag cannot be inferred safely from the contradictory current CLI help.  These values are evidence of the historical run only; they are not approved defaults for a new run.

## Corrected retry release basis

The local machine still has no `tefs`, VASP, MPI, scheduler command, SSH host
alias, or SSH agent identity, so the authenticated Web Cloud Shell is the only
authorized execution path.  The project, queue, current instance price, image,
POTCAR identity and one observed on-demand launch are verified.  The user explicitly
authorized direct execution below `100 RMB`; this retry retains the stricter
`4 RMB` ceiling and 30-minute timeout; the runner has no retry loop and this
workflow authorizes only one manual corrected submission.  The dry-run also
uses fixed-cell `ISIF=2`; a publication-grade common epitaxial comparison still
needs a validated out-of-plane/shear relaxation protocol.

## Remaining release checks

1. Preserve the recorded VASP licence-scope and account-readiness confirmation.
2. Sync and hash-check only the corrected JSON/checksum in the already reviewed
   cloud working directory; do not upload POTCAR.
3. Immediately before submission, require the displayed on-demand rate to be at
   most `3.50 RMB/hour`, queue empty, and `spot_paid` exactly `false`; then check
   the actual charge type immediately after launch.
4. Submit one corrected resubmission only, with 30-minute timeout, no further
   submission from this workflow,
   50 GB disk and no public IP.  The compute ceiling is about `1.81 RMB` at the
   rate gate; disk/COS charges remain additional and uncertain.  The total
   one-job authorization ceiling is `4.00 RMB`.
5. Record the job ID against the frozen input hash, monitor release, and
   retrieve only the POTCAR-free result bundle.
6. Treat scheduler success as insufficient.  Require runner gates, VASP
   convergence evidence, and the safe result archive before any scientific
   interpretation.

## Release sequence

1. Use the fixed-cell factor-1 package only as a workflow smoke test; do not use
   it as the final strain result.
2. Use the existing action-time user authorization and stricter `4 RMB` cap.
3. Sync the corrected command into the reviewed TEFS directory; retain the
   already assembled cloud-only licensed POTCAR.
4. Recheck the empty queue and rate, then submit one corrected factor-1 retry.
5. Confirm automatic termination, charge, raw outputs and phase identity.
6. Replace the fixed-cell method with a validated constrained-cell method,
   generate nine reviewed descriptors and only then decide whether to release
   the strain matrix.

Until all six steps pass, no strain-dependent energy or phase crossing exists and none may be reported.
