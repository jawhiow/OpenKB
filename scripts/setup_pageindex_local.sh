#!/usr/bin/env sh
set -eu

install_root="${1:?usage: setup_pageindex_local.sh INSTALL_ROOT}"
mkdir -p "$install_root"

cat > "$install_root/installation.json" <<'JSON'
{
  "repo_dir": "",
  "python_path": "",
  "script_path": "",
  "version": "",
  "status": "manual_setup_required"
}
JSON

printf '%s\n' "Created local PageIndex setup manifest at $install_root/installation.json"
