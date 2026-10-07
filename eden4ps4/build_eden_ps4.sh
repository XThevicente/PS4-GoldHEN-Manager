#!/usr/bin/env bash
set -euo pipefail

: "${ORBIS_SDK_BUNDLE:?ORBIS_SDK_BUNDLE must point at unpacked orbis-sdk-v1}"
EDEN_DIR="${EDEN_DIR:-$PWD/eden-src}"

# Load the matched OpenOrbis + orbis-compat + Mesa paths.
# shellcheck disable=SC1091
source "$ORBIS_SDK_BUNDLE/env.sh"

MESA_VK="$ORBIS_MESA_BUILD/src/amd/vulkan/libvulkan_radeon.a"
COMPAT_LIB="$ORBIS_COMPAT_DIR/build/liborbis-compat.a"
[[ -f "$MESA_VK" ]] || { echo "missing RADV archive: $MESA_VK" >&2; exit 2; }
[[ -f "$COMPAT_LIB" ]] || { echo "missing orbis-compat archive: $COMPAT_LIB" >&2; exit 2; }

if [[ ! -d "$EDEN_DIR/.git" ]]; then
  git clone --depth 1 --branch eden-orbis-ps4 https://github.com/eden-emulator/mirror.git "$EDEN_DIR"
fi

cd "$EDEN_DIR"

# Fix the PS4 FFmpeg cross flags for the current SDK-bundle layout.
# The upstream branch still points FFmpeg at <sdk>/usr/include, but this OpenOrbis bundle
# exposes headers directly at <sdk>/include. Passing the directory itself as a compiler
# argument also makes clang treat it as an input file, so use an explicit -I.
python3 - <<'PY'
from pathlib import Path
p = Path("externals/ffmpeg/CMakeLists.txt")
s = p.read_text()
old_c = "--extra-cflags=${CMAKE_SYSROOT}/usr/include"
old_cxx = "--extra-cxxflags=${CMAKE_SYSROOT}/usr/include"
new_c = "--extra-cflags=-I${CMAKE_SYSROOT}/include"
new_cxx = "--extra-cxxflags=-I${CMAKE_SYSROOT}/include"
if old_c not in s or old_cxx not in s:
    raise SystemExit("Eden FFmpeg OpenOrbis flag layout changed; refusing an unverified patch")
patched = s.replace(old_c, new_c).replace(old_cxx, new_cxx)
old_libs = """list(APPEND FFmpeg_CROSS_COMPILE_LIBS
        -lkernel"""
new_libs = """list(APPEND FFmpeg_CROSS_COMPILE_LIBS
        ${CMAKE_SYSROOT}/lib/crt1.o
        -lc
        -lkernel"""
if old_libs not in patched:
    raise SystemExit("Eden FFmpeg OpenOrbis library block changed; refusing an unverified patch")
p.write_text(patched.replace(old_libs, new_libs))
PY

# OpenOrbis v0.5.4 libc++ does not expose std::contiguous_iterator.
# Eden only uses this concept to select the contiguous data()/size() fast path,
# so use a PS4-local equivalent based on a pointer-returning data().
python3 - <<'PY'
from pathlib import Path
p = Path("src/common/concepts.h")
s = p.read_text()
old = """#include <iterator>\n#include <type_traits>"""
new = """#include <iterator>\n#include <type_traits>\n#include <utility>"""
if old not in s:
    raise SystemExit("Eden concepts includes changed; refusing an unverified patch")
s = s.replace(old, new)
old_concept = "template <typename T>\nconcept IsContiguousContainer = std::contiguous_iterator<typename T::iterator>;"
new_concept = """template <typename T>\n#ifdef __OPENORBIS__\nconcept IsContiguousContainer = requires(T& value) {\n    value.data();\n    value.size();\n} && std::is_pointer_v<decltype(std::declval<T&>().data())>;\n#else\nconcept IsContiguousContainer = std::contiguous_iterator<typename T::iterator>;\n#endif"""
if old_concept not in s:
    raise SystemExit("Eden contiguous-container concept changed; refusing an unverified patch")
p.write_text(s.replace(old_concept, new_concept))
PY

# OpenOrbis libc++ also lacks the C++20 <ranges> header. Eden only uses
# ranges::find here, which is equivalent to std::find over the span iterators.
python3 - <<'PY'
from pathlib import Path
p = Path("src/common/fs/fs_util.cpp")
s = p.read_text()
if "#include <ranges>" not in s or "std::ranges::find" not in s:
    raise SystemExit("Eden fs_util ranges usage changed; refusing an unverified patch")
s = s.replace("#include <ranges>\n", "")
s = s.replace("std::ranges::find(buffer, u8{0})", "std::find(buffer.begin(), buffer.end(), u8{0})")
p.write_text(s)
PY

# Eden's PS4 branch links the driver by the conventional name "vulkan_radeon".
# Put the matched SDK bundle's archive in the OpenOrbis library search path.
ln -sf "$MESA_VK" "$OO_PS4_TOOLCHAIN/lib/libvulkan_radeon.a"

# The RADV archive was built against the paired orbis-compat overlay.  Eden's older
# PS4 toolchain predates that overlay, so make the archive available to the final link.
ln -sf "$COMPAT_LIB" "$OO_PS4_TOOLCHAIN/lib/liborbis-compat.a"

# Eden's helper creates this only when it does not already exist.  We provide a compatible
# version with the current matched SDK paths and the corrected TLS linker script.
cat > ps4-toolchain.cmake <<EOF
set(CMAKE_SYSROOT "$OO_PS4_TOOLCHAIN")
set(CMAKE_STAGING_PREFIX "$OO_PS4_TOOLCHAIN")
set(CMAKE_SYSTEM_NAME "OpenOrbis")
set(CMAKE_SYSTEM_PROCESSOR x86_64)

set(ORBIS_COMPAT_DIR "$ORBIS_COMPAT_DIR")
set(ORBIS_MESA_BUILD "$ORBIS_MESA_BUILD")

set(ORBIS_COMMON "-D__OPENORBIS__ -D__ORBIS__ -D__PS4__ -DPS4 -DORBIS -D_LIBCPP_HAS_MUSL_LIBC=1 -D_GNU_SOURCE=1 -D_BSD_SOURCE=1 --target=x86_64-pc-freebsd12-elf -mtune=btver2 -march=btver2 -fPIC -funwind-tables -femulated-tls -isystem $OO_PS4_TOOLCHAIN/include/c++/v1 -isystem $ORBIS_COMPAT_DIR/include -isystem $OO_PS4_TOOLCHAIN/include -include orbis_prefix.h")
set(CMAKE_C_FLAGS "\${ORBIS_COMMON}")
set(CMAKE_CXX_FLAGS "\${ORBIS_COMMON}")

set(ORBIS_LINK "-m elf_x86_64 -pie --script=$ORBIS_SDK_BUNDLE/toolchain/orbis-tls.ld --eh-frame-hdr --no-rosegment -L$OO_PS4_TOOLCHAIN/lib -L$ORBIS_MESA_BUILD/src/amd/vulkan")
# FFmpeg uses the compiler driver for its configure link probes, so these flags must be clang-friendly.
set(ORBIS_DRIVER_LINK "--target=x86_64-pc-freebsd12-elf -nostdlib -fuse-ld=lld -Wl,-m,elf_x86_64 -Wl,-pie -Wl,--script=$ORBIS_SDK_BUNDLE/toolchain/orbis-tls.ld -Wl,--eh-frame-hdr -Wl,--no-rosegment -L$OO_PS4_TOOLCHAIN/lib -L$ORBIS_MESA_BUILD/src/amd/vulkan")
set(CMAKE_EXE_LINKER_FLAGS "\${ORBIS_DRIVER_LINK}")
set(CMAKE_C_LINK_FLAGS "\${ORBIS_DRIVER_LINK}")
set(CMAKE_CXX_LINK_FLAGS "\${ORBIS_DRIVER_LINK}")

set(CMAKE_C_COMPILER clang)
set(CMAKE_CXX_COMPILER clang++)
set(CMAKE_AR llvm-ar)
set(CMAKE_RANLIB llvm-ranlib)
set(CMAKE_LINKER clang)

set(CMAKE_C_LINK_EXECUTABLE "ld.lld -m elf_x86_64 -pie --script=$ORBIS_SDK_BUNDLE/toolchain/orbis-tls.ld --eh-frame-hdr --no-rosegment -L$OO_PS4_TOOLCHAIN/lib -L$ORBIS_MESA_BUILD/src/amd/vulkan <OBJECTS> -o <TARGET> $OO_PS4_TOOLCHAIN/lib/crt1.o <LINK_LIBRARIES> --whole-archive $COMPAT_LIB --no-whole-archive -lc++ -lc++abi -lunwind -lc -lkernel -lSceUserService -lSceSysmodule -lSceNet -lSceLibcInternal")
set(CMAKE_CXX_LINK_EXECUTABLE "ld.lld -m elf_x86_64 -pie --script=$ORBIS_SDK_BUNDLE/toolchain/orbis-tls.ld --eh-frame-hdr --no-rosegment -L$OO_PS4_TOOLCHAIN/lib -L$ORBIS_MESA_BUILD/src/amd/vulkan <OBJECTS> -o <TARGET> $OO_PS4_TOOLCHAIN/lib/crt1.o <LINK_LIBRARIES> --whole-archive $COMPAT_LIB --no-whole-archive -lc++ -lc++abi -lunwind -lc -lkernel -lSceUserService -lSceSysmodule -lSceNet -lSceLibcInternal")

set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)
set(CMAKE_SIZEOF_VOID_P 8)
EOF

rm -rf build

# Do not pass Eden's ARCH_FLAGS override: the toolchain above owns target flags and include order.
cmake -S . -B build -G "Unix Makefiles"   -DCMAKE_TOOLCHAIN_FILE="$EDEN_DIR/ps4-toolchain.cmake"   -DENABLE_QT_TRANSLATION=OFF   -DENABLE_CUBEB=OFF   -DCMAKE_BUILD_TYPE=Release   -DENABLE_LIBUSB=OFF   -DENABLE_UPDATE_CHECKER=OFF   -DENABLE_QT=OFF   -DENABLE_OPENGL=ON   -DENABLE_WEB_SERVICE=OFF   -DUSE_DISCORD_PRESENCE=OFF   -DCPMUTIL_FORCE_BUNDLED=ON   -DYUZU_USE_EXTERNAL_FFMPEG=ON   -DYUZU_USE_CPM=ON   -DDYNARMIC_ENABLE_NO_EXECUTE_SUPPORT=OFF   -DDYNARMIC_TESTS=OFF   -DYUZU_TESTS=OFF

if ! cmake --build build --target yuzu-cmd_pkg --parallel "${NPROC:-2}"; then
  echo "== Build failed: diagnostic logs =="
  if [[ -f build/_deps/ffmpeg-build/ffbuild/config.log ]]; then
    echo "---- FFmpeg ffbuild/config.log (tail) ----"
    tail -n 500 build/_deps/ffmpeg-build/ffbuild/config.log || true
  fi
  exit 1
fi

echo "== Eden PS4 outputs =="
find build -maxdepth 3 -type f \( -name 'eden-cli*' -o -name 'eboot.bin' -o -name '*.oelf' -o -name '*.pkg' \) -print -exec ls -lh {} \;
