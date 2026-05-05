import os
from dotenv import load_dotenv

load_dotenv()

ENV = os.environ.get('ENV', 'development')

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'you-will-never-guess')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

def get_database_url():
    """Retourne l'URL de la base de données corrigée"""
    db_url = os.environ.get('DATABASE_URL', 'sqlite:///ecole_compta.db')
    
    # Correction pour PostgreSQL Render
    if db_url and db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)
    
    return db_url

class DevelopmentConfig(Config):
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = get_database_url()
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
    }

class ProductionConfig(Config):
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = get_database_url()  # ← PAS de @property !
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 20,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
    }
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True

# Configuration active
CurrentConfig = DevelopmentConfig if ENV == 'development' else ProductionConfig