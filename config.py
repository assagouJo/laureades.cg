import os
from dotenv import load_dotenv

load_dotenv()

ENV = os.environ.get('ENV', 'development')

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'you-will-never-guess')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///ecole_compta.db')
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
    }

class ProductionConfig(Config):
    DEBUG = False
    
    @property
    def SQLALCHEMY_DATABASE_URI(self):
        """Corrige l'URL PostgreSQL pour Render"""
        db_url = os.environ.get('DATABASE_URL')
        if not db_url:
            # Fallback SQLite si pas de DATABASE_URL
            return 'sqlite:///ecole_compta.db'
        
        # 🔥 CORRECTION CRUCIALE : Render renvoie postgres:// → postgresql://
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
        
        return db_url
    
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 20,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
    }
    
    # Sécurité pour la production
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True

# Sélection de la configuration
CurrentConfig = DevelopmentConfig if ENV == 'development' else ProductionConfig