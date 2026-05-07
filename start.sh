#!/bin/bash
set -e

echo "🚀 Démarrage de Gestion École..."
echo "📊 Environnement: ${ENV:-production}"

# Créer les dossiers nécessaires
mkdir -p static/images

# Appliquer les migrations Flask-Migrate 4.1.0
echo "📊 Application des migrations..."
flask db upgrade
echo "✅ Migrations appliquées"

echo "✅ Démarrage du serveur Gunicorn..."
exec gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --workers=4 --timeout=120