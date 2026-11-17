#!/bin/sh
# Ein Lauf von main.py für Cron/anacron (siehe ~/.anacron/anacrontab und
# crontab -l). flock serialisiert Läufe: holt anacron z.B. um 14:58 den
# Morgenlauf nach, wartet der 15-Uhr-Lauf, statt parallel in die SQLite-DB
# zu schreiben.
cd "$(dirname "$0")" || exit 1
exec /usr/bin/flock -w 3600 data/.cron.lock venv/bin/python main.py "$@" >> data/cron.log 2>&1
