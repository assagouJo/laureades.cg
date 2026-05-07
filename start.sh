#!/bin/bash
set -e

echo "🚀 Démarrage de Gestion École..."
echo "📊 Environnement: ${ENV:-production}"
echo "🐍 Python version: $(python --version)"

# Créer les dossiers nécessaires
mkdir -p static/images

# 🔒 CRÉATION DES TABLES UNIQUEMENT (sans insertions)
echo "📊 Vérification de la base de données..."
python -c "
from app import app, db
with app.app_context():
    # Crée UNIQUEMENT les tables manquantes
    # Ne touche JAMAIS aux données existantes
    db.create_all()
    print('✅ Tables vérifiées - Données PRÉSERVÉES')
"

echo "✅ Démarrage du serveur Gunicorn..."
exec gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --workers=4 --timeout=120