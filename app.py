# app.py
from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
import os
from dotenv import load_dotenv

load_dotenv()  # Charge .env mais ne doit PAS contenir DATABASE_URL
from models import db

app = Flask(__name__)

# Configurations de base
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'you-will-never-guess')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# ✅ CORRECTION : Vérifier d'abord Render, puis .env, puis fallback
database_url = os.environ.get('DATABASE_URL')
if database_url:
    # Si Render fournit DATABASE_URL (ou .env en local)
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    print(f"✅ Base de données : {'PostgreSQL' if 'postgresql' in database_url else 'SQLite'}")
else:
    # Fallback uniquement si aucune variable définie
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///ecole_compta.db'
    print("⚠️ Aucune DATABASE_URL trouvée, utilisation SQLite par défaut")

# Initialisation
db.init_app(app)
migrate = Migrate(app, db)

# Login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# Upload folder
UPLOAD_FOLDER = os.path.join('static', 'images')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@app.after_request
def close_db_connection(response):
    try:
        db.session.close()
    except:
        pass
    return response

from routes import *
from audit import *