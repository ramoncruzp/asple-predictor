#!/usr/bin/env bash
# Desde la PC (Git Bash), en la carpeta del repo:   bash deploy/actualizar.sh
# Revisa que todo esté en commit, hace push y actualiza S1 por SSH.
set -euo pipefail
cd "$(dirname "$0")/.."

rojo() { printf '\033[31m%s\033[0m\n' "$*"; }

rama=$(git rev-parse --abbrev-ref HEAD)
if [ "$rama" != "main" ]; then rojo "Estás en la rama '$rama'. S1 usa main. Detenido."; exit 1; fi

pendientes=$(git status --porcelain)
if [ -n "$pendientes" ]; then
  rojo "Hay cambios sin commit. Haz commit primero (o avísale a Claude):"
  echo "$pendientes"
  exit 1
fi

git fetch --quiet origin
if [ -n "$(git log --oneline HEAD..origin/main)" ]; then
  rojo "GitHub tiene commits que tu PC no tiene. Haz 'git pull' primero. Detenido."; exit 1
fi
if [ -n "$(git log --oneline origin/main..HEAD)" ]; then
  echo "Subiendo a GitHub:"; git log --oneline origin/main..HEAD
  git push
else
  echo "GitHub ya tiene todo lo de tu PC."
fi

echo; echo "Conectando a S1..."
# Usa siempre la versión del script que está en GitHub (sirve también la primera vez).
ssh -t s1 'cd ~/asple-predictor && git fetch -q origin && git show origin/main:deploy/actualizar_s1.sh > /tmp/actualizar_s1.sh && bash /tmp/actualizar_s1.sh'
