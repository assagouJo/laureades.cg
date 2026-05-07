#!/bin/bash
set -e

echo "🚀 Démarrage de Gestion École..."
echo "📊 Environnement: ${ENV:-production}"

mkdir -p static/images

# 🔒 CRÉER LES TABLES SI ELLES N'EXISTENT PAS
echo "📊 Initialisation de la base de données..."
python -c "
from app import app, db
with app.app_context():
    db.create_all()
    print('✅ Tables créées/vérifiées')
"

echo "✅ Démarrage du serveur Gunicorn..."
exec gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --workers=4 --timeout=120