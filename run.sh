#!/usr/bin/env bash
#
# run.sh — Avvia la dashboard, e su richiesta rigenera prima i dati.
#
# PERCHE' ESISTE. Aggiornare i dati e mostrarli sono due operazioni separate:
# build_dataset.py scarica da SEC ed yfinance (minuti), app.py legge solo i CSV
# gia' prodotti (secondi). Tenerle separate e' giusto — nessuno vuole aspettare
# un quarto d'ora per aprire una dashboard — ma obbliga a ricordare due comandi,
# il percorso del virtualenv e la variabile per la SEC. Questo script li ricorda
# al posto tuo.
#
#   ./run.sh              avvia la dashboard con i dati che ci sono
#   ./run.sh --update     rigenera prima i dati, poi avvia
#   ./run.sh --update-only        rigenera e basta, senza aprire nulla
#   ./run.sh --update --universe sp500      aggiorna un universo diverso
#   ./run.sh --port 8600  usa un'altra porta
#
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
UNIVERSE="sp500+russell1000"
PORT="8501"
UPDATE=0
ONLY_UPDATE=0

while [ $# -gt 0 ]; do
  case "$1" in
    -u|--update)      UPDATE=1 ;;
    --update-only)    UPDATE=1; ONLY_UPDATE=1 ;;
    --universe)       UNIVERSE="${2:?--universe richiede un valore}"; shift ;;
    --universe=*)     UNIVERSE="${1#*=}" ;;
    --port)           PORT="${2:?--port richiede un valore}"; shift ;;
    --port=*)         PORT="${1#*=}" ;;
    -h|--help)        sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "opzione non riconosciuta: $1 (usa --help)" >&2; exit 2 ;;
  esac
  shift
done

if [ ! -x "$PY" ]; then
  echo "Manca l'ambiente virtuale. Creane uno con:"
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# La SEC richiede un User-Agent con un contatto reale, altrimenti risponde 403.
# Lo prendiamo dall'ambiente, oppure dal file locale .sec_user_agent — che NON
# viene versionato, cosi' l'indirizzo non finisce in un repo pubblico.
if [ -z "${SEC_USER_AGENT:-}" ] && [ -f .sec_user_agent ]; then
  SEC_USER_AGENT="$(tr -d '\r\n' < .sec_user_agent)"
  export SEC_USER_AGENT
fi

if [ "$UPDATE" = "1" ]; then
  if [ -z "${SEC_USER_AGENT:-}" ]; then
    echo "Per aggiornare i dati serve un contatto per la SEC. Scrivilo una volta:"
    echo "  echo 'Lynch Research tua@email.com' > .sec_user_agent"
    exit 1
  fi
  echo "→ Aggiorno i dati (universo: $UNIVERSE). Sono diversi minuti."
  # I dati attuali restano al loro posto finche' il build non li riscrive: se
  # qualcosa va storto a meta', la dashboard continua a mostrare l'ultimo
  # dataset valido invece di trovarsi con file mezzi scritti.
  "$PY" build_dataset.py --universe "$UNIVERSE" --workers 5
  echo "→ Dati aggiornati."
fi

if [ "$ONLY_UPDATE" = "1" ]; then
  exit 0
fi

echo "→ Dashboard su http://localhost:$PORT  (Ctrl+C per fermarla)"
exec "$PY" -m streamlit run app.py --server.port "$PORT"
