#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
CMAKE="${PROJECT_ROOT}/.venv/bin/cmake"
LOCK_FILE="${PROJECT_ROOT}/computations/simulation/ferrox.lock.json"
SOURCE_DIR="${PROJECT_ROOT}/data/computation/tools/FerroX"
BUILD_DIR="${SOURCE_DIR}/build-macos-cpu"

if [[ ! -x "${PYTHON}" ]]; then
  echo "Project virtual environment not found: ${PYTHON}" >&2
  exit 2
fi

"${PYTHON}" -m pip install -r "${PROJECT_ROOT}/computations/simulation/requirements-simulation.txt"

read_lock() {
  "${PYTHON}" -c "import json; print(json.load(open('${LOCK_FILE}'))['$1'])"
}

FERROX_REPOSITORY="$(read_lock repository)"
FERROX_BRANCH="$(read_lock branch)"
FERROX_COMMIT="$(read_lock commit)"

mkdir -p "$(dirname "${SOURCE_DIR}")"
if [[ ! -d "${SOURCE_DIR}/.git" ]]; then
  git clone --branch "${FERROX_BRANCH}" "${FERROX_REPOSITORY}" "${SOURCE_DIR}"
fi

CURRENT_COMMIT="$(git -C "${SOURCE_DIR}" rev-parse HEAD)"
if [[ "${CURRENT_COMMIT}" != "${FERROX_COMMIT}" ]]; then
  git -C "${SOURCE_DIR}" fetch origin "${FERROX_COMMIT}"
  if [[ -n "$(git -C "${SOURCE_DIR}" status --porcelain)" ]]; then
    echo "FerroX source contains local changes; refusing to replace them." >&2
    exit 3
  fi
  git -C "${SOURCE_DIR}" checkout --detach "${FERROX_COMMIT}"
fi

export CC="${CC:-/usr/bin/clang}"
export CXX="${CXX:-/usr/bin/clang++}"

"${CMAKE}" \
  -S "${SOURCE_DIR}" \
  -B "${BUILD_DIR}" \
  -DFerroX_COMPUTE=NOACC \
  -DFerroX_MPI=OFF \
  -DFerroX_MPI_THREAD_MULTIPLE=OFF \
  -DFerroX_PRECISION=DOUBLE \
  -DFerroX_EB=OFF \
  -DFerroX_TIME_DEPENDENT=OFF \
  -DFerroX_SUNDIALS=OFF \
  -DFerroX_CCACHE=OFF \
  -DCMAKE_BUILD_TYPE=Release

"${CMAKE}" --build "${BUILD_DIR}" --parallel 2
"${PYTHON}" "${PROJECT_ROOT}/pipelines/62_check_simulation_runtime.py" --jax-smoke --ferrox-smoke
