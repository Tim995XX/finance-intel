#!/bin/bash
set -e
cd "$(dirname "$0")"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Finance Intelligence — Setup           ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Python check
python3 --version || { echo "ERROR: Python 3 benötigt"; exit 1; }

# Virtual environment
if [ ! -d "venv" ]; then
    echo "→ Erstelle Virtual Environment …"
    python3 -m venv venv
fi

echo "→ Aktiviere venv …"
source venv/bin/activate

echo "→ Installiere Abhängigkeiten …"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo "→ Lade NLTK-Daten …"
python3 - <<'PY'
import nltk
for pkg in ["punkt", "punkt_tab", "stopwords", "averaged_perceptron_tagger"]:
    nltk.download(pkg, quiet=True)
print("  NLTK OK")
PY

echo "→ Lade TextBlob-Korpora …"
python3 -m textblob.download_corpora 2>/dev/null || true

echo "→ Initialisiere Datenbank …"
python3 -c "from src.database.db import init_db; init_db()"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Setup abgeschlossen!                   ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "Nächste Schritte:"
echo ""
echo "  source venv/bin/activate"
echo ""
echo "  # 1. News + Kurse einmalig laden:"
echo "  python main.py"
echo ""
echo "  # 2. Dashboard starten:"
echo "  streamlit run dashboard/app.py"
echo ""
echo "  # Danach automatisch täglich (cron):"
echo "  # 0 7 * * 1-5  cd $(pwd) && venv/bin/python main.py >> data/cron.log 2>&1"
echo ""
