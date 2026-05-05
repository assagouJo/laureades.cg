#!/bin/bash
set -e

echo "🚀 Démarrage de Gestion École..."
echo "📊 Environnement: ${ENV:-production}"

# Créer les dossiers nécessaires
mkdir -p static/images

# Si PostgreSQL, exécuter les migrations
if [ -n "$DATABASE_URL" ] && [[ "$DATABASE_URL" == postgres* ]]; then
    echo "🐘 PostgreSQL détecté - Exécution des migrations..."
    flask db upgrade || echo "⚠️ Migration échouée, création des tables..."
    python -c "from app import app, db; app.app_context().push(); db.create_all()"
else
    echo "📁 SQLite détecté - Création des tables..."
    python -c "from app import app, db; app.app_context().push(); db.create_all()"
fi

echo "✅ Démarrage du serveur Gunicorn..."
exec gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --workers=4 --timeout=120