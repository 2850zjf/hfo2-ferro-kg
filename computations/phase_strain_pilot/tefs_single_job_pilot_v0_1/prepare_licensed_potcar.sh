#!/usr/bin/env bash
set -euo pipefail

umask 077

if [[ ! -f INPUT_SHA256SUMS.txt ]] || ! sha256sum -c INPUT_SHA256SUMS.txt; then
  echo "Frozen input checksum verification failed; refusing POTCAR assembly." >&2
  exit 9
fi

case "$(pwd -P)" in
  /root/home/*) ;;
  *)
    echo "Refusing to assemble POTCAR outside /root/home/." >&2
    exit 2
    ;;
esac

HF_SOURCE="/root/POTCAR/PBE/Hf_pv/POTCAR"
O_SOURCE="/root/POTCAR/PBE/O/POTCAR"
HF_EXPECTED="db42cd3e4a48e81eaff18895282e2f5a37f2c8c6a4b9ac45e357affc13c83b84"
O_EXPECTED="8a74b9a1f5fdb3d0c3e0183c7873177abdbef07d407b310b7edcd9ed0a3eea64"

for source_file in "$HF_SOURCE" "$O_SOURCE"; do
  if [[ ! -f "$source_file" ]]; then
    echo "Licensed PAW source missing: $source_file" >&2
    exit 3
  fi
done

if ! awk 'NR == 6 {ok_species = ($1 == "Hf" && $2 == "O" && NF == 2)}
          NR == 7 {ok_counts = ($1 == 4 && $2 == 8 && NF == 2)}
          END {exit !(ok_species && ok_counts)}' POSCAR; then
  echo "POSCAR is not ordered as Hf O / 4 8; refusing POTCAR assembly." >&2
  exit 8
fi

hf_actual="$(sha256sum "$HF_SOURCE" | awk '{print $1}')"
o_actual="$(sha256sum "$O_SOURCE" | awk '{print $1}')"

if [[ "$hf_actual" != "$HF_EXPECTED" ]]; then
  echo "Hf_pv POTCAR hash mismatch; refusing to continue." >&2
  exit 4
fi
if [[ "$o_actual" != "$O_EXPECTED" ]]; then
  echo "O POTCAR hash mismatch; refusing to continue." >&2
  exit 5
fi

cat "$HF_SOURCE" "$O_SOURCE" > POTCAR
chmod 600 POTCAR

mkdir -p provenance
sha256sum POTCAR > provenance/POTCAR_SHA256.txt
grep 'TITEL' POTCAR > provenance/POTCAR_TITEL.txt

if ! grep -q 'PAW_PBE Hf_pv 06Sep2000' provenance/POTCAR_TITEL.txt; then
  echo "Unexpected Hf PAW title; refusing to continue." >&2
  exit 6
fi
if ! grep -q 'PAW_PBE O 08Apr2002' provenance/POTCAR_TITEL.txt; then
  echo "Unexpected O PAW title; refusing to continue." >&2
  exit 7
fi

echo "Licensed Hf_pv+O POTCAR assembled on TEFS; binary remains cloud-side."
