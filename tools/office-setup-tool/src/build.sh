#!/bin/sh
# Builds OfficeSetupTool.exe on Linux (needs mono-mcs + gcc-mingw-w64 windres).
# $1 = folder with the Office Scrubber Fast package (OfficeScrubber.cmd + bin\)
set -e
cd "$(dirname "$0")"
SCR="${1:-../Scrubber}"
x86_64-w64-mingw32-windres -O res app.rc -o app.res
RES=""
for f in "$SCR"/OfficeScrubber.cmd "$SCR"/RunScrubber.ps1 "$SCR"/bin/*; do
  rel="Scrubber/${f#$SCR/}"
  RES="$RES -resource:$f,$rel"
done
mcs -target:winexe -platform:anycpu -codepage:utf8 -optimize+ -win32res:app.res \
    -r:System.Windows.Forms.dll -r:System.Drawing.dll -r:System.Xml.dll \
    $RES -out:../OfficeSetupTool.exe OfficeSetupTool.cs
rm -f app.res
echo "built ../OfficeSetupTool.exe"
