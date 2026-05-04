# audit.py
from models import db, AuditLog
from flask_login import current_user
from flask import request
from datetime import datetime

def log_action(action, details=""):
    """Enregistre une action dans les logs d'audit"""
    try:
        log = AuditLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            action=action,
            details=details,
            ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        print(f"Erreur d'audit: {e}")
        db.session.rollback()

def get_audit_logs(limit=100):
    """Récupère les derniers logs d'audit"""
    return AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(limit).all()