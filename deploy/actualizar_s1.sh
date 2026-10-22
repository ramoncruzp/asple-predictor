#!/usr/bin/env bash
# Actualiza ASPLE Predictor en S1: respaldo de la BD, git pull, reinicio y verificación.
# Se corre EN S1:   bash ~/asple-predictor/deploy/actualizar_s1.sh
# O desde la PC:    bash deploy/actualizar.sh   (hace push y llama a este script por SSH)
set -euo pipefail

REPO=/home/ubuntu/asple-predictor
SERVICIO=asple-predictor
PY="$REPO/venv2/bin/python"
BD="$REPO/asple_predictor.db"
RESPALDOS=/home/ubuntu/respaldos
MAX_RESPALDOS=10
SALUD=http://127.0.0.1:8000/api/health

rojo() { printf '\033[31m%s\033[0m\n' "$*"; }
verde() { printf '\033[32m%s\033[0m\n' "$*"; }
paso() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

cd "$REPO"
antes=$(git rev-parse --short HEAD)

paso "1/5 Buscar cambios en GitHub"
git fetch --quiet origin
nuevos=$(git log --oneline HEAD..origin/main)
if [ -z "$nuevos" ]; then
  verde "S1 ya está al día ($antes). No hay nada que actualizar."
  exit 0
fi
echo "Commits nuevos:"
echo "$nuevos"

# Si un archivo cambiado a mano en S1 también viene cambiado de GitHub, el pull chocaría.
locales=$(git diff --name-only | sort)
entrantes=$(git diff --name-only HEAD origin/main | sort)
choque=$(comm -12 <(echo "$locales") <(echo "$entrantes") | sed '/^$/d')
if [ -n "$choque" ]; then
  rojo "Detenido: estos archivos tienen cambios locales en S1 y también vienen cambiados de GitHub:"
  echo "$choque"
  rojo "No se tocó nada. Avísale a Claude antes de seguir."
  exit 1
fi

paso "2/5 Respaldo de la base de datos"
mkdir -p "$RESPALDOS"
destino="$RESPALDOS/asple_predictor_$(date +%Y%m%d_%H%M%S)_$antes.db"
"$PY" - "$BD" "$destino" <<'PYEOF'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
PYEOF
echo "Respaldo: $destino ($(du -h "$destino" | cut -f1))"
# Conserva solo los últimos respaldos automáticos (los hechos a mano no se tocan).
ls -1t "$RESPALDOS"/asple_predictor_20*.db 2>/dev/null | tail -n +$((MAX_RESPALDOS + 1)) | xargs -r rm --

paso "3/5 Traer el código"
git merge --ff-only --quiet origin/main
despues=$(git rev-parse --short HEAD)
echo "$antes -> $despues"
if git diff --name-only "$antes" "$despues" | grep -qE '(^|/)requirements[^/]*\.txt$'; then
  rojo "Atención: cambiaron las dependencias (requirements)."
  read -r -p "¿Instalarlas ahora con pip antes de reiniciar? (s/N) " r
  if [ "${r,,}" = "s" ]; then "$REPO/venv2/bin/pip" install -r "$REPO/requirements.txt"; fi
fi

paso "4/5 Reiniciar el servicio"
inicio=$(date '+%Y-%m-%d %H:%M:%S')
sudo systemctl restart "$SERVICIO"
echo -n "Esperando a que la app responda"
ok=0
for _ in $(seq 1 45); do
  if curl -fs -m 3 "$SALUD" >/dev/null 2>&1; then ok=1; break; fi
  echo -n "."; sleep 2
done
echo

paso "5/5 Verificar"
systemctl status "$SERVICIO" --no-pager | sed -n '3p'
# Avisos conocidos que no son fallas (Prophet sin plotly: solo afecta gráficos interactivos que la app no usa).
IGNORAR='prophet.plot: Importing plotly failed'
errores=$(journalctl -u "$SERVICIO" --since "$inicio" --no-pager | grep -E "Traceback|ERROR|CRITICAL" | grep -v -F "$IGNORAR" | head -20 || true)
if [ "$ok" = 1 ] && [ -z "$errores" ]; then
  verde "Listo: S1 actualizado de $antes a $despues. Salud: $(curl -s "$SALUD")"
  exit 0
fi
[ "$ok" = 1 ] || rojo "La app NO respondió en 90 segundos."
if [ -n "$errores" ]; then rojo "Errores en el log desde el reinicio:"; echo "$errores"; fi
echo
echo "Últimas líneas del log:"
journalctl -u "$SERVICIO" -n 25 --no-pager
echo
rojo "Para volver a la versión anterior ($antes):"
echo "  cd $REPO && git reset --keep $antes && sudo systemctl restart $SERVICIO"
echo "Respaldo de la BD de antes de actualizar: $destino"
exit 1
