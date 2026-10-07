#!/usr/bin/env bash
set -euo pipefail
: "${OO_PS4_TOOLCHAIN:?Set OO_PS4_TOOLCHAIN first}"
SAMPLE="$OO_PS4_TOOLCHAIN/samples/net_http"
mkdir -p sce_sys/about sce_module
cp "$SAMPLE/sce_sys/about/right.sprx" sce_sys/about/right.sprx
cp "$SAMPLE/sce_sys/icon0.png" sce_sys/icon0.png
cp "$SAMPLE/sce_module/libSceFios2.prx" sce_module/libSceFios2.prx
cp "$SAMPLE/sce_module/libc.prx" sce_module/libc.prx
printf 'Prepared package assets from %s\n' "$SAMPLE"

