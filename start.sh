#!/bin/bash
set -e

echo "🚀 Démarrage de Gestion École..."
echo "📊 Environnement: ${ENV:-production}"
echo "🐍 Python version: $(python --version)"

# Créer les dossiers nécessaires
mkdir -p static/images

# 🔒 SAUVEGARDE AVANT INITIALISATION
echo "💾 Vérification de la base de données..."
if [ -f "instance/ecole.db" ]; then
    echo "✅ Base de données existante détectée"
    # Optionnel : faire une sauvegarde
    cp instance/ecole.db instance/ecole_backup_$(date +%Y%m%d_%H%M%S).db
    echo "💾 Sauvegarde effectuée"
fi

# Initialisation/Mise à jour SÉCURISÉE de la base de données
echo "📊 Initialisation sécurisée de la base de données..."
python init_tables.py
echo "✅ Base de données initialisée (données existantes préservées)"

echo "✅ Démarrage du serveur Gunicorn..."
exec gunicorn app:app --bind 0.0.0.0:${PORT:-8000} --workers=4 --timeout=120