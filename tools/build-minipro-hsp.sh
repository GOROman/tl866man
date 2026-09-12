#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUNTIME="$ROOT/.runtime"
SOURCE="$RUNTIME/minipro-hsp-src"
PREFIX="$RUNTIME/minipro-hsp"
UPSTREAM_URL=${TL866MAN_MINIPRO_URL:-https://gitlab.com/DavidGriffith/minipro.git}
UPSTREAM_REF=${TL866MAN_MINIPRO_REF:-3808aecb6a1dac9906a9691b93820ee1bd2b7a18}
JOBS=${TL866MAN_BUILD_JOBS:-2}

mkdir -p "$RUNTIME"
if [ ! -d "$SOURCE/.git" ]; then
  git clone --depth 1 "$UPSTREAM_URL" "$SOURCE"
fi

git -C "$SOURCE" fetch --depth 1 origin "$UPSTREAM_REF"
git -C "$SOURCE" checkout --detach "$UPSTREAM_REF"
if ! grep -q 'HSP-08-0' "$SOURCE/src/prom.c" ||
   ! grep -q 'linear adapter rotated 180' "$SOURCE/src/prom.c" ||
   ! grep -q 'TL866MAN_READ_DELAY_US' "$SOURCE/src/prom.c" ||
   ! grep -q 'stream_code' "$SOURCE/src/main.c"; then
  git -C "$SOURCE" reset --hard "$UPSTREAM_REF"
  git -C "$SOURCE" apply "$ROOT/patches/minipro-hsp2310.patch"
fi

make -C "$SOURCE" clean
make -C "$SOURCE" -j"$JOBS" PREFIX="$PREFIX"

mkdir -p "$PREFIX/bin" "$PREFIX/share/minipro"
cp "$SOURCE/minipro" "$PREFIX/bin/minipro"
cp "$SOURCE/logicic.xml" "$PREFIX/share/minipro/logicic.xml"
python3 "$ROOT/tools/add-hsp-infoic.py" "$SOURCE/infoic.xml" "$PREFIX/share/minipro/infoic.xml"

echo "Built custom minipro: $PREFIX/bin/minipro"
echo "HSP profile: HSP-08-0 PRG · LH2310 / DIP-28"
echo "SC-88 profile: SC-88Pro PRG LH538U0P-ROT180 DIP40"
