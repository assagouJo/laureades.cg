#!/bin/bash
set -e

echo "🚀 Démarrage de Gestion École..."
echo "📊 Environnement: ${ENV:-production}"
echo "🐍 Python version: $(python --version)"

# Créer les dossiers nécessaires
mkdir -p static/images

# Initialisation/Mise à jour de la base de données
echo "📊 Initialisation de la base de données..."
python init_tables.py
echo "✅ Base de données initialisée avec succès"

echo "✅ Démarrage du serveur Gunicorn..."
exec gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --workers=4 --timeout=120