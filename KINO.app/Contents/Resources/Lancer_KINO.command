#!/bin/bash
# ===================================================
# KINO Desktop — Lanceur macOS en 1 clic
# ===================================================

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

echo "=============================================="
echo "          LANCEMENT DE KINO (macOS)           "
echo "=============================================="

# 1. Vérification de Python 3
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 n'est pas installé sur votre Mac."
    echo ""
    echo "Pour l'installer facilement :"
    echo "  1) Soit avec Homebrew (recommandé) : brew install python"
    echo "  2) Soit le site officiel : https://www.python.org/downloads/macos/"
    echo ""
    read -p "Appuyez sur [Entrée] pour quitter..."
    exit 1
fi

# 2. Préparation automatique de l'environnement virtuel macOS
if [ ! -d ".venv_mac" ]; then
    echo "🔧 Première configuration sur votre Mac..."
    echo "📦 Installation des bibliothèques nécessaires (pywebview, WebKit)..."
    python3 -m venv .venv_mac
    source .venv_mac/bin/activate
    pip install --upgrade pip --quiet
    pip install -r requirements.txt --quiet
    echo "✅ Installation réussie !"
    echo ""
else
    source .venv_mac/bin/activate
fi

# 3. Démarrage de l'application
echo "🎬 Démarrage de KINO Desktop..."
python3 desktop.py
