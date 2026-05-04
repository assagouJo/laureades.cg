import os
from dotenv import load_dotenv

load_dotenv()

ENV = os.environ.get('ENV', 'development')

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'you-will-never-guess')
    
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
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 20,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
    }

CurrentConfig = DevelopmentConfig if ENV == 'development' else ProductionConfig