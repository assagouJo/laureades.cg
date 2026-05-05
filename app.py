# app.py
from flask import Flask
from flask_login import LoginManager
import os
from dotenv import load_dotenv

load_dotenv()
import config
from config import CurrentConfig

# Importer db depuis models au lieu de créer ici
from models import db

app = Flask(__name__)

# Configurations de base
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'you-will-never-guess')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///ecole_compta.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Appliquer la configuration
app.config.from_object(CurrentConfig)

# Initialisation
db.init_app(app)  # Important: initialiser db avec l'app

# Login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# Upload folder
UPLOAD_FOLDER = os.path.join('static', 'images')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Fermeture des connexions
@app.after_request
def close_db_connection(response):
    try:
        db.session.close()
    except:
        pass
    return response

# Importer routes après l'initialisation pour éviter les imports circulaires
from routes import *
from audit import *