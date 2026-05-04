from app import app, db
from models import User
from datetime import datetime

def init_app():
    """Initialisation minimale au démarrage (création admin si inexistant)"""
    with app.app_context():
        # Créer les tables si elles n'existent pas
        db.create_all()
        print("✅ Base de données connectée")
        
        # Vérifier si un admin existe, sinon le créer
        if not User.query.filter_by(username='admin').first():
            print("⚠️  Aucun administrateur trouvé, création en cours...")
            admin = User(
                username='admin',
                nom='Administrateur',
                prenom='Admin',
                role='admin',
                actif=True,
                created_at=datetime.utcnow()
            )
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("✅ Compte admin créé: admin / admin123")
        
        # Afficher quelques statistiques
        total_users = User.query.count()
        print(f"👥 Utilisateurs enregistrés: {total_users}")

if __name__ == '__main__':
    print("=" * 50)
    print("🚀 DÉMARRAGE DE L'APPLICATION")
    print("=" * 50)
    
    init_app()
    
    print("\n🌐 Application disponible sur:")
    print("   http://localhost:5000")
    print("\n🔐 Accès administrateur:")
    print("   Utilisateur: admin")
    print("   Mot de passe: admin123")
    print("=" * 50)
    
    app.run(debug=True, host='0.0.0.0', port=5000)