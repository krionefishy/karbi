#!/bin/sh
# Сборка расширения «скриншоты» для стенда: адрес сервиса по умолчанию — локальный, и в
# optional_host_permissions добавлены http://localhost/* и http://127.0.0.1/*. Боевой
# extension/screens/manifest.json не меняется: правится только копия в каталоге сборки.
#
#   deploy/stand/build-extension.sh [адрес-сервиса] [каталог]
#   → <каталог>/unpacked/ (грузить в chrome://extensions) и <каталог>/marketplace-auto-screenshots.zip
set -eu

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
backend_url=${1:-http://localhost:8090}
out=${2:-$here/extension}
mkdir -p "$out"
out=$(cd "$out" && pwd)

cd "$root/extension"
[ -d node_modules ] || npm ci --no-audit --no-fund
npx tsc --noEmit -p tsconfig.screens.json
VITE_BACKEND_URL="$backend_url" npx vite build --config vite.screens.config.ts --outDir "$out/unpacked" --emptyOutDir

node - "$out/unpacked/manifest.json" <<'EOF'
const fs = require("node:fs");
const path = process.argv[2];
const manifest = JSON.parse(fs.readFileSync(path, "utf8"));
const hosts = new Set(manifest.optional_host_permissions ?? []);
for (const host of ["http://localhost/*", "http://127.0.0.1/*"]) hosts.add(host);
manifest.optional_host_permissions = [...hosts];
manifest.name = `${manifest.name} (стенд)`;
fs.writeFileSync(path, JSON.stringify(manifest, null, 2) + "\n");
EOF

rm -f "$out/marketplace-auto-screenshots.zip"
(cd "$out/unpacked" && zip -qr "$out/marketplace-auto-screenshots.zip" .)
echo "Расширение стенда: $out/unpacked (адрес по умолчанию $backend_url)"
