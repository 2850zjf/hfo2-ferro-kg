# Generated HfO2 biaxial-strain pilot

Status: `prepared_dry_run_not_executed`

This directory is a dry-run input contract. It has not run or submitted VASP.
No POTCAR, API key, database content, or `.env` content is included.

## Frozen pilot

- phases: m P2_1/c #14, o Pca2_1 #29, t P4_2/nmc #137
- comparable size: Hf4O8, 12 atoms, four formula units
- factors: 0.99, 1.00, 1.01
- common square in-plane reference: actual relaxed tetragonal sqrt(2) x sqrt(2) length
- relaxation template: fixed cell, ions only (`ISIF = 2`)

## Independent pre-execution audit

The builder itself does not call spglib. A separate one-time spglib 2.7.0 check
at symprec=1e-3 found m #14, o #29, and t #137 for all nine frozen
unrelaxed POSCAR hashes; reported minimum distances span about 1.998-2.078 A.
This audit is hash-bound and does not verify any future relaxed output.

Review `strain_pilot_manifest.json` and every publication blocker before any licensed-machine execution.
