#!/bin/sh
# Cross-compile ExtendC.exe on Linux (needs gcc-mingw-w64-x86-64).
set -e
cd "$(dirname "$0")"
x86_64-w64-mingw32-windres extend-c.rc -O coff -o extend-c.res
x86_64-w64-mingw32-gcc -municode -mwindows -Os -s -Wall -o ExtendC.exe extend-c.c extend-c.res -lshell32 -luser32
rm -f extend-c.res
echo "built ExtendC.exe"
