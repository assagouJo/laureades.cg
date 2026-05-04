# routes.py
from app import app, db, login_manager
from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_user, logout_user, login_required, current_user
from models import User, Eleve, Paiement, GroupeScolaire, SousGroupe, OptionTransport, OptionCantine, TarifFrais, TarifFraisAffecte, Parametre, TypeFrais, PaiementDepot, DepotBancaire, verifier_depot_valide, annuler_paiement_avec_verification
from audit import log_action
from datetime import datetime, timedelta
from sqlalchemy import func
from functools import wraps
from sqlalchemy import and_
from datetime import datetime



@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# ============ DECORATEURS ============

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Accès réservé aux administrateurs', 'danger')
            return redirect(url_for('liste_eleves'))
        return f(*args, **kwargs)
    return decorated_function


def comptable_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['admin', 'comptable']:
            flash('Accès réservé aux administrateurs et comptables', 'danger')
            return redirect(url_for('liste_eleves'))
        return f(*args, **kwargs)
    return decorated_function


# ========== FONCTIONS UTILITAIRES PÉRIODES ==========

def get_parametre(cle, default=None):
    """Récupère un paramètre depuis la base"""
    param = Parametre.query.filter_by(cle=cle).first()
    return param.valeur if param else default


def get_annees_scolaires():
    """Retourne la liste des années scolaires disponibles"""
    annees_str = get_parametre('annees_scolaires', '2024-2025,2025-2026,2026-2027')
    return [a.strip() for a in annees_str.split(',') if a.strip()]


def get_annee_active():
    """Retourne l'année scolaire active"""
    return get_parametre('annee_scolaire_active', '2025-2026')


def get_frais_reinscription():
    """Retourne le montant des frais de réinscription"""
    return float(get_parametre('frais_reinscription', 0))


# ============ RÉINSCRIPTIONS ============

@app.route('/changer-annee/<annee>')
@login_required
@admin_required
def changer_annee_scolaire(annee):
    """Change l'année scolaire active"""
    annees = get_annees_scolaires()
    if annee not in annees:
        flash('Année scolaire invalide', 'danger')
        return redirect(url_for('liste_eleves'))
    
    param = Parametre.query.filter_by(cle='annee_scolaire_active').first()
    if param:
        param.valeur = annee
        db.session.commit()
        flash(f'✅ Année scolaire changée pour {annee}', 'success')
    
    return redirect(url_for('liste_eleves'))


@app.route('/reinscriptions')
@login_required
def liste_reinscriptions():
    """Liste des réinscriptions"""
    if current_user.role not in ['admin', 'comptable']:
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('liste_eleves'))
    
    annee_active = get_annee_active()
    eleves_reinscrits = Eleve.query.filter_by(reinscrit=True, annee_scolaire=annee_active)\
                                    .order_by(Eleve.date_reinscription.desc()).all()
    
    total = Eleve.query.filter_by(actif=True).count()
    reinscrits = len(eleves_reinscrits)
    non_reinscrits = total - reinscrits
    
    return render_template('reinscriptions/liste.html',
                         eleves_reinscrits=eleves_reinscrits,
                         annee_active=annee_active,
                         annees=get_annees_scolaires(),
                         total=total,
                         reinscrits=reinscrits,
                         non_reinscrits=non_reinscrits)


@app.route('/reinscriptions/anciens')
@login_required
def reinscrire_anciens():
    """Page pour réinscrire les anciens élèves"""
    if current_user.role not in ['admin', 'comptable']:
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('liste_eleves'))
    
    annee_active = get_annee_active()
    annees_disponibles = get_annees_scolaires()  # 🔥 Liste depuis les paramètres
    
    # Élèves non encore réinscrits
    anciens = Eleve.query.filter(
        Eleve.actif == True,
        Eleve.reinscrit == False
    ).order_by(Eleve.nom, Eleve.prenom).all()
    
    sous_groupes = SousGroupe.query.order_by(SousGroupe.groupe_id, SousGroupe.ordre).all()
    
    # 🔥 Période par défaut = période suivante 🔥
    periode_suivante = get_periode_suivante()
    
    return render_template('reinscriptions/anciens.html',
                         eleves=anciens,
                         annee_active=annee_active,
                         annees_disponibles=annees_disponibles,  # 🔥 Ajouté
                         periode_suivante=periode_suivante,      # 🔥 Ajouté
                         sous_groupes=sous_groupes)




@app.route('/reinscriptions/inscrire/<int:eleve_id>', methods=['POST'])
@login_required
def reinscrire_eleve(eleve_id):
    """Réinscrire un élève en mettant à jour ses informations pour la nouvelle période"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    eleve = Eleve.query.get_or_404(eleve_id)
    annee_choisie = request.form.get('annee_scolaire', get_annee_active())
    
    # Vérifier si l'élève n'est pas déjà dans l'année choisie
    if eleve.annee_scolaire == annee_choisie:
        return jsonify({
            'success': False, 
            'message': f'{eleve.nom_complet} est déjà dans l\'année {annee_choisie}'
        }), 400
    
    # Vérifier le solde (utilise la propriété calculée)
    solde_du = eleve.solde  # ou eleve.frais_scolarite - eleve.montant_paye
    if solde_du > 0:
        return jsonify({
            'success': False, 
            'message': f'Solde impayé de {solde_du:,.0f} FCFA. Veuillez régler avant de réinscrire.'
        }), 400
    
    try:
        # Sauvegarder l'ancien état pour le log
        ancienne_annee = eleve.annee_scolaire
        ancienne_classe = eleve.classe
        ancien_statut = eleve.statut_paiement  # Lecture OK
        
        nouvelle_classe = request.form.get('nouvelle_classe', eleve.classe)
        nouveau_sous_groupe_id = request.form.get('nouveau_sous_groupe_id') or eleve.sous_groupe_id
        
        # Calculer les nouveaux frais
        nouveaux_frais = calculer_frais_total(
            sous_groupe_id=int(nouveau_sous_groupe_id) if nouveau_sous_groupe_id else eleve.sous_groupe_id,
            est_affecte=eleve.est_affecte_etat,
            transport_option_id=eleve.transport_option_id,
            cantine_option_id=eleve.cantine_option_id,
            renforcement_inscrit=eleve.renforcement_inscrit,
            classe=nouvelle_classe
        )
        
        # Mise à jour de l'élève
        eleve.annee_scolaire = annee_choisie
        eleve.classe = nouvelle_classe
        if nouveau_sous_groupe_id:
            eleve.sous_groupe_id = int(nouveau_sous_groupe_id)
        eleve.frais_scolarite = nouveaux_frais
        eleve.montant_paye = 0  # Réinitialiser les paiements pour la nouvelle année
        
        # Marquer la réinscription
        eleve.reinscrit = True
        eleve.date_reinscription = datetime.utcnow()
        eleve.reinscrit_par = current_user.username
        
        db.session.commit()
        
        # 🔥 IMPORTANT : Rafraîchir pour que statut_paiement se recalcule
        db.session.refresh(eleve)
        
        # Log de l'action
        log_action('REINSCRIRE_ELEVE', 
                   f"{eleve.nom_complet} ({eleve.matricule}): "
                   f"{ancienne_classe} {ancienne_annee} → {nouvelle_classe} {annee_choisie} | "
                   f"Statut avant: {ancien_statut} → Statut après: {eleve.statut_paiement} | "
                   f"Nouveaux frais: {nouveaux_frais:,.0f} FCFA")
        
        return jsonify({
            'success': True,
            'message': f'{eleve.nom_complet} réinscrit pour {annee_choisie} en {nouvelle_classe}',
            'details': {
                'matricule': eleve.matricule,
                'annee_scolaire': annee_choisie,
                'classe': nouvelle_classe,
                'frais_scolarite': nouveaux_frais,
                'solde': eleve.solde,
                'statut_paiement': eleve.statut_paiement  # Sera "Impayé" car montant_paye = 0
            }
        })
        
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Erreur réinscription élève {eleve_id}: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'message': f'Erreur: {str(e)}'}), 500
    

@app.route('/reinscriptions/reverser/<int:eleve_id>', methods=['POST'])
@login_required
@admin_required
def reverser_reinscription(eleve_id):
    """Annule la réinscription d'un élève"""
    eleve = Eleve.query.get_or_404(eleve_id)
    
    if not eleve.reinscrit:
        return jsonify({'success': False, 'message': 'Cet élève n\'est pas réinscrit'}), 400
    
    try:
        ancienne_annee = eleve.annee_scolaire
        eleve.reinscrit = False
        eleve.annee_scolaire = get_annee_active()  # Garde l'année mais marque non réinscrit
        eleve.date_reinscription = None
        eleve.reinscrit_par = None
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Réinscription de {eleve.nom_complet} annulée avec succès'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    

def get_periode_suivante():
    """Retourne la période suivante par rapport à l'année active"""
    annees = get_annees_scolaires()
    annee_active = get_annee_active()
    
    if annee_active in annees:
        index = annees.index(annee_active)
        if index + 1 < len(annees):
            return annees[index + 1]
    
    # Fallback : dernière année de la liste
    return annees[-1] if annees else annee_active


@app.route('/reinscriptions/inscrire-tous', methods=['POST'])
@login_required
@admin_required
def reinscrire_tous():
    """Réinscrire tous les élèves sans solde impayé"""
    annee_active = get_annee_active()
    
    eleves = Eleve.query.filter_by(actif=True, reinscrit=False).all()
    eleves_eligibles = [e for e in eleves if e.solde <= 0]
    eleves_bloques = [e for e in eleves if e.solde > 0]
    
    count = 0
    for eleve in eleves_eligibles:
        # 🔥 REMETTRE À ZÉRO ET RECALCULER 🔥
        eleve.montant_paye = 0
        eleve.frais_scolarite = calculer_frais_total(
            sous_groupe_id=eleve.sous_groupe_id,
            est_affecte=eleve.est_affecte_etat,
            transport_option_id=eleve.transport_option_id,
            cantine_option_id=eleve.cantine_option_id,
            renforcement_inscrit=eleve.renforcement_inscrit,
            classe=eleve.classe
        )
        eleve.reinscrit = True
        eleve.annee_scolaire = annee_active
        eleve.date_reinscription = datetime.utcnow()
        eleve.reinscrit_par = current_user.username
        count += 1
    
    db.session.commit()
    
    message = f'{count} élèves réinscrits pour {annee_active} avec frais remis à zéro.'
    if eleves_bloques:
        message += f' {len(eleves_bloques)} bloqué(s) pour solde impayé.'
    
    return jsonify({'success': True, 'message': message})



@app.route('/parametres/periodes', methods=['POST'])
@login_required
def sauvegarder_parametres_periodes():
    """Sauvegarde les paramètres de périodes scolaires"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    try:
        params = {
            'annee_scolaire_active': request.form.get('annee_scolaire_active', '2025-2026'),
            'annees_scolaires': request.form.get('annees_scolaires', ''),
            'periode_debut': request.form.get('periode_debut', ''),
            'periode_fin': request.form.get('periode_fin', ''),
            'frais_reinscription': request.form.get('frais_reinscription', '0'),
            'annee_scolaire': request.form.get('annee_scolaire', '2025-2026'),
        }
        
        for cle, valeur in params.items():
            param = Parametre.query.filter_by(cle=cle).first()
            if param:
                param.valeur = valeur
            else:
                param = Parametre(cle=cle, valeur=valeur)
                db.session.add(param)
        
        db.session.commit()
        flash('✅ Paramètres des périodes sauvegardés !', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/periodes/activer/<annee>')
@login_required
@admin_required
def activer_periode(annee):
    """Active une année scolaire"""
    annees = get_annees_scolaires()
    if annee not in annees:
        flash('Année scolaire invalide', 'danger')
        return redirect(url_for('parametres'))
    
    # Mettre à jour le paramètre
    param = Parametre.query.filter_by(cle='annee_scolaire_active').first()
    if param:
        param.valeur = annee
        db.session.commit()
        flash(f'✅ Période {annee} activée avec succès !', 'success')
    
    return redirect(url_for('parametres'))


@app.route('/periodes/desactiver')
@login_required
@admin_required
def desactiver_periode():
    """Désactive la période active (aucune période active)"""
    param = Parametre.query.filter_by(cle='annee_scolaire_active').first()
    if param:
        param.valeur = ''
        db.session.commit()
        flash('⚠️ Aucune période active actuellement', 'warning')
    
    return redirect(url_for('parametres'))


@app.context_processor
def injecter_parametres_ecole():
    """Injecte les paramètres de l'école dans tous les templates"""
    try:
        from models import Parametre
        params = Parametre.query.all()
        parametres = {p.cle: p.valeur for p in params}
    except:
        parametres = {}
    
    # Valeurs par défaut
    defaults = {
        'nom_ecole': 'GS LAUREADES',
        'adresse_ecole': 'Adresse de l\'école',
        'telephone_ecole': '',
        'email_notification': '',
        'devise': 'FCFA',
        'annee_scolaire': '2024-2025'
    }
    
    # Fusionner avec les valeurs de la DB
    for key, value in defaults.items():
        if key not in parametres or not parametres[key]:
            parametres[key] = value
    
    return dict(parametres_ecole=parametres)


# ============ ROUTES D'AUTHENTIFICATION ============

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect_based_on_role(current_user)
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            if not user.actif:
                flash('Votre compte a été désactivé. Contactez l\'administrateur.', 'danger')
                return render_template('login.html')
            
            login_user(user)
            user.update_last_login()
            
            flash(f'Bienvenue {user.username} !', 'success')
            
            return redirect_based_on_role(user)
        else:
            flash('Nom d\'utilisateur ou mot de passe incorrect', 'danger')
    
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Vous avez été déconnecté', 'info')
    return redirect(url_for('login'))


def redirect_based_on_role(user):
    """Redirige l'utilisateur selon son rôle"""
    if user.role == 'admin':
        return redirect(url_for('dashboard'))  # ou index()
    else:
        return redirect(url_for('liste_eleves'))


@app.route('/profil', methods=['GET', 'POST'])
@login_required
def profil():
    """Page de profil pour l'utilisateur connecté"""
    
    if request.method == 'POST':
        nom = request.form.get('nom', '').strip()
        prenom = request.form.get('prenom', '').strip()
        mot_de_passe_actuel = request.form.get('mot_de_passe_actuel', '')
        nouveau_password = request.form.get('nouveau_password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        errors = []
        
        if not nom or len(nom) < 2:
            errors.append("Le nom doit contenir au moins 2 caractères")
        elif not nom.replace(' ', '').isalpha():
            errors.append("Le nom ne doit contenir que des lettres")
        
        if not prenom or len(prenom) < 2:
            errors.append("Le prénom doit contenir au moins 2 caractères")
        elif not prenom.replace(' ', '').isalpha():
            errors.append("Le prénom ne doit contenir que des lettres")
        
        if nouveau_password:
            if not current_user.check_password(mot_de_passe_actuel):
                errors.append("Mot de passe actuel incorrect")
            elif len(nouveau_password) < 4:
                errors.append("Le nouveau mot de passe doit contenir au moins 4 caractères")
            elif nouveau_password != confirm_password:
                errors.append("Les nouveaux mots de passe ne correspondent pas")
        
        if errors:
            for error in errors:
                flash(error, 'danger')
        else:
            try:
                current_user.nom = nom
                current_user.prenom = prenom
                
                if nouveau_password:
                    current_user.set_password(nouveau_password)
                    log_action('CHANGE_PASSWORD', f"Changement de mot de passe pour {current_user.username}")
                
                db.session.commit()
                
                if nouveau_password:
                    flash('Profil et mot de passe mis à jour avec succès !', 'success')
                else:
                    flash('Profil mis à jour avec succès !', 'success')
                
                return redirect(url_for('profil'))
                
            except Exception as e:
                db.session.rollback()
                flash(f'Erreur lors de la mise à jour: {str(e)}', 'danger')
    
    # GET: Statistiques
    total_eleves_inscrits = Eleve.query.count()
    total_paiements_effectues = Paiement.query.count()
    
    # ✅ CORRECTION FINALE : Utiliser Python pour filtrer
    tous_eleves = Eleve.query.all()
    eleves_avec_solde = sum(1 for e in tous_eleves if e.solde > 0)
    
    return render_template('utilisateurs/profil.html', 
                         user=current_user,
                         total_eleves_inscrits=total_eleves_inscrits,
                         total_paiements_effectues=total_paiements_effectues,
                         eleves_avec_solde=eleves_avec_solde)


# ============ ROUTES DE GESTION DES UTILISATEURS (ADMIN) ============

@app.route('/utilisateurs')
@login_required
@admin_required
def liste_utilisateurs():
    """Affiche la liste de tous les utilisateurs"""
    utilisateurs = User.query.order_by(User.created_at.desc()).all()
    
    # Statistiques
    total_users = len(utilisateurs)
    total_admins = User.query.filter_by(role='admin').count()
    total_comptables = User.query.filter_by(role='comptable').count()
    total_users_simple = User.query.filter_by(role='user').count()
    total_actifs = User.query.filter_by(actif=True).count()
    
    return render_template('utilisateurs/liste.html', 
                         utilisateurs=utilisateurs,
                         total_users=total_users,
                         total_admins=total_admins,
                         total_comptables=total_comptables,
                         total_users_simple=total_users_simple,
                         total_actifs=total_actifs)


@app.route('/utilisateurs/ajouter', methods=['GET', 'POST'])
@login_required
@admin_required
def ajouter_utilisateur():
    """Ajoute un nouvel utilisateur"""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        nom = request.form.get('nom', '').strip()
        prenom = request.form.get('prenom', '').strip()
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')
        role = request.form.get('role', 'user')
        actif = 'actif' in request.form
        
        # Validation
        errors = []
        
        if not username or len(username) < 3:
            errors.append("Le nom d'utilisateur doit contenir au moins 3 caractères")
        
        if not nom or len(nom) < 2:
            errors.append("Le nom doit contenir au moins 2 caractères")
        
        if not prenom or len(prenom) < 2:
            errors.append("Le prénom doit contenir au moins 2 caractères")
        
        if not password or len(password) < 4:
            errors.append("Le mot de passe doit contenir au moins 4 caractères")
        
        if password != password_confirm:
            errors.append("Les mots de passe ne correspondent pas")
        
        # Vérifier si username existe déjà
        if User.query.filter_by(username=username).first():
            errors.append("Ce nom d'utilisateur est déjà pris")
        
        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('utilisateurs/ajouter.html',
                                 form_data=request.form)
        
        try:
            # Créer l'utilisateur
            nouvel_utilisateur = User(
                username=username,
                nom=nom,
                prenom=prenom,
                role=role,
                actif=actif
            )
            nouvel_utilisateur.set_password(password)
            
            db.session.add(nouvel_utilisateur)
            db.session.commit()
            
            flash(f'Utilisateur {username} ({nouvel_utilisateur.nom_complet}) créé avec succès', 'success')
            return redirect(url_for('liste_utilisateurs'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erreur lors de la création: {str(e)}', 'danger')
    
    return render_template('utilisateurs/ajouter.html')


@app.route('/utilisateurs/<int:user_id>/modifier', methods=['GET', 'POST'])
@login_required
@admin_required
def modifier_utilisateur(user_id):
    """Modifie un utilisateur existant"""
    utilisateur = User.query.get_or_404(user_id)
    
    # Calculer le nombre d'administrateurs POUR LE TEMPLATE
    utilisateurs_admin_count = User.query.filter_by(role='admin', actif=True).count()
    
    # Empêcher la modification du dernier admin
    if utilisateur.role == 'admin':
        nb_admins = User.query.filter_by(role='admin').count()
        if nb_admins <= 1 and request.method == 'POST' and request.form.get('role') != 'admin':
            flash('Impossible de retirer les droits du dernier administrateur', 'warning')
            return redirect(url_for('modifier_utilisateur', user_id=user_id))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        nom = request.form.get('nom', '').strip()
        prenom = request.form.get('prenom', '').strip()
        role = request.form.get('role', 'user')
        actif = 'actif' in request.form
        nouveau_password = request.form.get('nouveau_password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # Validation
        errors = []
        
        if not username or len(username) < 3:
            errors.append("Le nom d'utilisateur doit contenir au moins 3 caractères")
        
        if not nom or len(nom) < 2:
            errors.append("Le nom doit contenir au moins 2 caractères")
        
        if not prenom or len(prenom) < 2:
            errors.append("Le prénom doit contenir au moins 2 caractères")
        
        # Vérifier si username existe déjà (sauf pour l'utilisateur actuel)
        existing_user = User.query.filter_by(username=username).first()
        if existing_user and existing_user.id != user_id:
            errors.append("Ce nom d'utilisateur est déjà pris")
        
        if nouveau_password:
            if len(nouveau_password) < 4:
                errors.append("Le nouveau mot de passe doit contenir au moins 4 caractères")
            elif nouveau_password != confirm_password:
                errors.append("Les mots de passe ne correspondent pas")
        
        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('utilisateurs/modifier.html', 
                                 utilisateur=utilisateur,
                                 utilisateurs_admin_count=utilisateurs_admin_count)  # ← AJOUTÉ
        
        try:
            utilisateur.username = username
            utilisateur.nom = nom
            utilisateur.prenom = prenom
            utilisateur.role = role
            utilisateur.actif = actif
            
            if nouveau_password:
                utilisateur.set_password(nouveau_password)
            
            db.session.commit()
            flash(f'Utilisateur {username} modifié avec succès', 'success')
            return redirect(url_for('liste_utilisateurs'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erreur lors de la modification: {str(e)}', 'danger')
    
    return render_template('utilisateurs/modifier.html', 
                         utilisateur=utilisateur,
                         utilisateurs_admin_count=utilisateurs_admin_count)  # ← AJOUTÉ


@app.route('/utilisateurs/<int:user_id>/supprimer', methods=['POST'])
@login_required
@admin_required
def supprimer_utilisateur(user_id):
    """Supprime un utilisateur"""
    utilisateur = User.query.get_or_404(user_id)
    
    # Empêcher la suppression de son propre compte
    if utilisateur.id == current_user.id:
        flash('Vous ne pouvez pas supprimer votre propre compte', 'danger')
        return redirect(url_for('liste_utilisateurs'))
    
    # Empêcher la suppression du dernier admin
    if utilisateur.role == 'admin':
        nb_admins = User.query.filter_by(role='admin').count()
        if nb_admins <= 1:
            flash('Impossible de supprimer le dernier administrateur', 'danger')
            return redirect(url_for('liste_utilisateurs'))
    
    try:
        username = utilisateur.username
        nom_complet = utilisateur.nom_complet
        db.session.delete(utilisateur)
        db.session.commit()
        flash(f'Utilisateur {username} ({nom_complet}) supprimé avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la suppression: {str(e)}', 'danger')
    
    return redirect(url_for('liste_utilisateurs'))


@app.route('/utilisateurs/<int:user_id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle_utilisateur(user_id):
    """Active/Désactive un utilisateur"""
    utilisateur = User.query.get_or_404(user_id)
    
    # Empêcher la désactivation de son propre compte
    if utilisateur.id == current_user.id:
        flash('Vous ne pouvez pas modifier votre propre statut', 'danger')
        return redirect(url_for('liste_utilisateurs'))
    
    # Empêcher la désactivation du dernier admin
    if utilisateur.role == 'admin':
        nb_admins = User.query.filter_by(role='admin', actif=True).count()
        if nb_admins <= 1 and utilisateur.actif:
            flash('Impossible de désactiver le dernier administrateur actif', 'danger')
            return redirect(url_for('liste_utilisateurs'))
    
    try:
        utilisateur.actif = not utilisateur.actif
        db.session.commit()
        
        status = "activé" if utilisateur.actif else "désactivé"
        flash(f'Utilisateur {utilisateur.username} {status} avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors du changement de statut: {str(e)}', 'danger')
    
    return redirect(url_for('liste_utilisateurs'))


# ============ DASHBOARD ============


@app.route('/')
@app.route('/dashboard')
@login_required
def dashboard():
    """Dashboard - Vue adaptée selon le rôle de l'utilisateur"""
    
    # Récupérer le rôle de l'utilisateur
    role = current_user.role
    
    # Admin : dashboard complet avec toutes les stats
    if role == 'admin':
        return dashboard_admin()
    
    # Comptable : dashboard avec focus sur les paiements
    elif role == 'comptable':
        return dashboard_comptable()
    
    # Opérateur simple : dashboard limité
    else:
        return dashboard_operateur()


@app.route('/dashboard/admin')
@login_required
def dashboard_admin():
    """Dashboard complet pour les administrateurs avec filtres"""
    
    if current_user.role != 'admin':
        flash('Accès réservé aux administrateurs', 'danger')
        return redirect(url_for('dashboard'))
    
    from datetime import datetime, date
    from sqlalchemy import func
    
    # 🔥 Année active GLOBALE (définie dans les paramètres) 🔥
    annee_active_globale = get_annee_active()
    
    # 🔥 Période du filtre dashboard (par défaut = année active globale) 🔥
    periode_filtre = request.args.get('periode', annee_active_globale)
    
    # ========== RÉCUPÉRATION DES AUTRES FILTRES ==========
    genre = request.args.get('genre', '')
    classe = request.args.get('classe', '')
    niveau = request.args.get('niveau', '')
    affecte = request.args.get('affecte', '')
    date_debut = request.args.get('date_debut', '')
    date_fin = request.args.get('date_fin', '')
    mois = request.args.get('mois', '')
    statut_paiement = request.args.get('statut', '')
    
    if mois:
        annee_actuelle = datetime.now().year
        date_debut = f"{annee_actuelle}-{int(mois):02d}-01"
        date_fin = f"{annee_actuelle}-12-31" if int(mois) == 12 else f"{annee_actuelle}-{int(mois)+1:02d}-01"
    
    # ========== REQUÊTES DE BASE (FILTRÉES PAR LA PÉRIODE CHOISIE) ==========
    query_eleves = Eleve.query.filter_by(actif=True, annee_scolaire=periode_filtre)
    query_paiements = Paiement.query.filter_by(annee_scolaire=periode_filtre)
    
    # ========== FILTRES ÉLÈVES ==========
    if genre:
        query_eleves = query_eleves.filter(Eleve.genre == genre)
    if classe:
        query_eleves = query_eleves.filter(Eleve.classe == classe)
    if niveau:
        query_eleves = query_eleves.filter(Eleve.sous_groupe_id == int(niveau))
    if affecte:
        query_eleves = query_eleves.filter(Eleve.est_affecte_etat == (affecte == '1'))
    
    # ========== FILTRES PAIEMENTS ==========
    filtre_date_actif = bool(date_debut or date_fin or mois)
    if date_debut:
        query_paiements = query_paiements.filter(func.date(Paiement.date_paiement) >= date_debut)
    if date_fin:
        query_paiements = query_paiements.filter(func.date(Paiement.date_paiement) < date_fin)
    
    # ========== EXÉCUTION ==========
    eleves_filtres = query_eleves.all()
    paiements_filtres = query_paiements.all()
    
    ids_eleves_filtres = [e.id for e in eleves_filtres]
    paiements_filtres = [p for p in paiements_filtres if p.eleve_id in ids_eleves_filtres]
    
    if filtre_date_actif:
        ids_eleves_avec_paiements = list(set(p.eleve_id for p in paiements_filtres))
        eleves_filtres = [e for e in eleves_filtres if e.id in ids_eleves_avec_paiements]
    
    if statut_paiement:
        if statut_paiement == 'Payé':
            eleves_filtres = [e for e in eleves_filtres if e.montant_paye >= e.frais_scolarite]
        elif statut_paiement == 'Partiel':
            eleves_filtres = [e for e in eleves_filtres if 0 < e.montant_paye < e.frais_scolarite]
        elif statut_paiement == 'Impayé':
            eleves_filtres = [e for e in eleves_filtres if e.montant_paye == 0]
    
    # ========== STATISTIQUES ==========
    total_eleves = len(eleves_filtres)
    total_encaisse = sum(p.montant for p in paiements_filtres if p.montant > 0 and p.statut == 'actif')
    
    eleves_payes = sum(1 for e in eleves_filtres if e.montant_paye >= e.frais_scolarite)
    eleves_partiels = sum(1 for e in eleves_filtres if 0 < e.montant_paye < e.frais_scolarite)
    eleves_impayes = sum(1 for e in eleves_filtres if e.montant_paye == 0)
    
    eleves_affectes = sum(1 for e in eleves_filtres if e.est_affecte_etat)
    eleves_non_affectes = total_eleves - eleves_affectes
    
    total_frais = sum(e.frais_scolarite for e in eleves_filtres)
    total_paye = sum(e.montant_paye for e in eleves_filtres)
    taux_recouvrement = round((total_paye / total_frais * 100) if total_frais > 0 else 0, 1)
    
    # Paiements du jour
    aujourdhui = date.today()
    paiements_jour = [p for p in paiements_filtres if p.date_paiement.date() == aujourdhui and p.montant > 0 and p.statut == 'actif']
    total_jour = sum(p.montant for p in paiements_jour)
    nb_paiements_jour = len(paiements_jour)
    
    # Paiements du mois
    debut_mois = aujourdhui.replace(day=1)
    paiements_mois = [p for p in paiements_filtres if p.date_paiement.date() >= debut_mois and p.montant > 0 and p.statut == 'actif']
    total_mois = sum(p.montant for p in paiements_mois)
    
    # Derniers paiements
    paiements_recents = sorted([p for p in paiements_filtres if p.montant > 0 and p.statut == 'actif'], 
                                key=lambda p: p.date_paiement, reverse=True)[:10]
    
    # Top 5 payeurs
    top_payeurs = sorted([e for e in eleves_filtres if e.montant_paye > 0], 
                          key=lambda e: e.montant_paye, reverse=True)[:5]
    
    # Top 5 débiteurs
    top_dette = sorted([e for e in eleves_filtres if e.solde > 0], 
                        key=lambda e: e.solde, reverse=True)[:5]
    
    # Stats par niveau
    stats_par_niveau = db.session.query(
        SousGroupe.nom,
        func.count(Eleve.id).label('total'),
        func.sum(Eleve.montant_paye).label('paye'),
        func.sum(Eleve.frais_scolarite).label('frais')
    ).outerjoin(Eleve, Eleve.sous_groupe_id == SousGroupe.id)\
     .filter(Eleve.annee_scolaire == periode_filtre)\
     .group_by(SousGroupe.id, SousGroupe.nom).all()
    
    # Derniers élèves inscrits
    derniers_eleves = Eleve.query.filter_by(annee_scolaire=periode_filtre)\
                           .order_by(Eleve.date_inscription.desc()).limit(10).all()
    
    # Utilisateurs
    total_utilisateurs = User.query.count()
    utilisateurs_actifs = User.query.filter_by(actif=True).count()
    
    # Listes pour filtres
    classes = sorted(set(e.classe for e in Eleve.query.filter_by(annee_scolaire=periode_filtre).all() if e.classe))
    sous_groupes = SousGroupe.query.order_by(SousGroupe.ordre).all()
    
    filtres_actifs = any([genre, classe, niveau, affecte, date_debut, date_fin, mois, statut_paiement])
    
    mois_noms = ['', 'Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin',
                 'Juillet', 'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre']
    mois_nom = mois_noms[int(mois)] if mois else ''
    
    niveau_nom = ''
    if niveau:
        sg = SousGroupe.query.get(int(niveau))
        niveau_nom = sg.nom if sg else ''
    
    # 🔥 Périodes disponibles pour le filtre 🔥
    annees_disponibles = get_annees_scolaires()
    
    return render_template('dashboard/admin.html',
                         annee_active=annee_active_globale,      # Période active globale
                         periode_filtre=periode_filtre,           # Période du filtre dashboard
                         annees_disponibles=annees_disponibles,   # Pour le sélecteur
                         total_eleves=total_eleves,
                         total_encaisse=total_encaisse,
                         taux_recouvrement=taux_recouvrement,
                         eleves_payes=eleves_payes,
                         eleves_partiels=eleves_partiels,
                         eleves_impayes=eleves_impayes,
                         eleves_affectes=eleves_affectes,
                         eleves_non_affectes=eleves_non_affectes,
                         total_jour=total_jour,
                         nb_paiements_jour=nb_paiements_jour,
                         total_mois=total_mois,
                         paiements_recents=paiements_recents,
                         top_payeurs=top_payeurs,
                         top_dette=top_dette,
                         stats_par_niveau=stats_par_niveau,
                         derniers_eleves=derniers_eleves,
                         total_utilisateurs=total_utilisateurs,
                         utilisateurs_actifs=utilisateurs_actifs,
                         classes=classes,
                         sous_groupes=sous_groupes,
                         filters={
                             'genre': genre, 'classe': classe, 'niveau': niveau,
                             'affecte': affecte, 'date_debut': date_debut,
                             'date_fin': date_fin, 'mois': mois, 'statut': statut_paiement,
                             'niveau_nom': niveau_nom, 'periode': periode_filtre
                         },
                         filtres_actifs=filtres_actifs,
                         mois_nom=mois_nom,
                         annee_actuelle=datetime.now().year)


def dashboard_comptable():
    """Dashboard pour les comptables - focus sur les paiements"""
    
    annee_active = get_annee_active()
    
    total_encaisse = db.session.query(func.sum(Paiement.montant))\
        .filter(Paiement.annee_scolaire == annee_active, Paiement.montant > 0, Paiement.statut == 'actif').scalar() or 0
    
    total_frais = db.session.query(func.sum(Eleve.frais_scolarite))\
        .filter(Eleve.annee_scolaire == annee_active).scalar() or 0
    
    # Montant restant à recouvrer
    reste_a_recouvrer = total_frais - total_encaisse
    
    # Taux de recouvrement
    taux_recouvrement = (total_encaisse / total_frais * 100) if total_frais > 0 else 0
    
    # Répartition des paiements par statut
    eleves_payes = Eleve.query.filter(
        Eleve.montant_paye >= Eleve.frais_scolarite
    ).count()
    
    eleves_partiels = Eleve.query.filter(
        Eleve.montant_paye < Eleve.frais_scolarite,
        Eleve.montant_paye > 0
    ).count()
    
    eleves_impayes = Eleve.query.filter(
        Eleve.montant_paye == 0
    ).count()
    
    # Paiements du jour
    aujourdhui = datetime.utcnow().date()
    paiements_jour = Paiement.query.filter(
        func.date(Paiement.date_paiement) == aujourdhui
    ).order_by(Paiement.date_paiement.desc()).all()
    total_jour = sum(p.montant for p in paiements_jour)
    
    # Paiements de la semaine
    debut_semaine = aujourdhui.replace(day=aujourdhui.day - aujourdhui.weekday())
    paiements_semaine = Paiement.query.filter(
        func.date(Paiement.date_paiement) >= debut_semaine
    ).all()
    total_semaine = sum(p.montant for p in paiements_semaine)
    
    # Paiements du mois
    debut_mois = aujourdhui.replace(day=1)
    paiements_mois = Paiement.query.filter(
        func.date(Paiement.date_paiement) >= debut_mois
    ).all()
    total_mois = sum(p.montant for p in paiements_mois)
    
    # Paiements récents
    paiements_recents = Paiement.query.order_by(
        Paiement.date_paiement.desc()
    ).limit(20).all()
    
    # Répartition par mode de paiement
    stats_modes = db.session.query(
        Paiement.type_paiement,
        func.count(Paiement.id).label('nb'),
        func.sum(Paiement.montant).label('total')
    ).group_by(Paiement.type_paiement).all()
    
    # Élèves avec solde (ceux qui doivent encore payer)
    eleves_avec_solde = Eleve.query.filter(
        Eleve.solde > 0
    ).order_by(Eleve.solde.desc()).limit(20).all()
    
    return render_template('dashboard/comptable.html',
                         total_encaisse=total_encaisse,
                         total_frais=total_frais,
                         reste_a_recouvrer=reste_a_recouvrer,
                         taux_recouvrement=round(taux_recouvrement, 2),
                         eleves_payes=eleves_payes,
                         eleves_partiels=eleves_partiels,
                         eleves_impayes=eleves_impayes,
                         total_jour=total_jour,
                         paiements_jour=paiements_jour,
                         total_semaine=total_semaine,
                         total_mois=total_mois,
                         paiements_recents=paiements_recents,
                         stats_modes=stats_modes,
                         eleves_avec_solde=eleves_avec_solde)


def dashboard_operateur():
    """Dashboard simple pour les opérateurs"""
    
    # Statistiques de base
    total_eleves = Eleve.query.count()
    
    # Paiements du jour
    aujourdhui = datetime.utcnow().date()
    paiements_jour = Paiement.query.filter(
        func.date(Paiement.date_paiement) == aujourdhui
    ).order_by(Paiement.date_paiement.desc()).limit(10).all()
    total_jour = sum(p.montant for p in paiements_jour)
    nb_paiements_jour = len(paiements_jour)
    
    # Derniers paiements
    derniers_paiements = Paiement.query.order_by(
        Paiement.date_paiement.desc()
    ).limit(10).all()
    
    # Derniers élèves inscrits
    derniers_eleves = Eleve.query.order_by(
        Eleve.date_inscription.desc()
    ).limit(10).all()
    
    return render_template('dashboard/operateur.html',
                         total_eleves=total_eleves,
                         total_jour=total_jour,
                         nb_paiements_jour=nb_paiements_jour,
                         paiements_jour=paiements_jour,
                         derniers_paiements=derniers_paiements,
                         derniers_eleves=derniers_eleves)



@app.route('/changer-dossier/<dossier>')
@login_required
def changer_dossier(dossier):
    """Change le dossier actif (maternelle, primaire, secondaire ou tous)"""
    
    if dossier == 'tous':
        session.pop('dossier_actif', None)
        session.pop('groupe_dossier', None)
        flash('Tous les dossiers sont maintenant affichés', 'info')
    elif dossier == 'maternelle':
        session['dossier_actif'] = 'maternelle'
        session['groupe_dossier'] = 'Maternelle'
        flash('🧸 Dossier Maternelle activé', 'success')
    elif dossier == 'primaire':
        session['dossier_actif'] = 'primaire'
        session['groupe_dossier'] = 'Primaire'
        flash('📚 Dossier Primaire activé', 'success')
    elif dossier == 'secondaire':
        session['dossier_actif'] = 'secondaire'
        session['groupe_dossier'] = 'Secondaire'  # ✅ PAS 'all'
        flash('🎓 Dossier Secondaire activé', 'success')
    else:
        flash('Dossier invalide', 'danger')
    
    return redirect(url_for('liste_eleves'))


# Route pour la gestion des groupes
@app.route('/groupes')
@login_required
def gestion_groupes():
    if current_user.role not in ['admin']:
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('dashboard'))
    
    groupes = GroupeScolaire.query.order_by(GroupeScolaire.ordre).all()
    return render_template('gestion_groupes.html', groupes=groupes)

# API pour obtenir les sous-groupes d'un groupe
@app.route('/api/groupes/<int:groupe_id>/sous-groupes')
@login_required
def get_sous_groupes(groupe_id):
    sous_groupes = SousGroupe.query.filter_by(groupe_id=groupe_id).order_by(SousGroupe.ordre).all()
    return jsonify([{'id': sg.id, 'nom': sg.nom} for sg in sous_groupes])

# Route pour ajouter un sous-groupe
@app.route('/api/sous-groupe/ajouter', methods=['POST'])
@login_required
def api_ajouter_sous_groupe():
    if current_user.role != 'admin':
        return jsonify({'error': 'Non autorisé'}), 403
    
    data = request.json
    sg = SousGroupe(
        nom=data['nom'],
        groupe_id=data['groupe_id']
    )
    db.session.add(sg)
    db.session.commit()
    return jsonify({'id': sg.id, 'nom': sg.nom})

# Route pour supprimer un sous-groupe
@app.route('/api/sous-groupe/<int:id>/supprimer', methods=['DELETE'])
@login_required
def api_supprimer_sous_groupe(id):
    if current_user.role != 'admin':
        return jsonify({'error': 'Non autorisé'}), 403
    
    sg = SousGroupe.query.get_or_404(id)
    
    # Vérifier si des élèves sont associés
    if sg.eleves:
        return jsonify({'error': 'Des élèves sont associés à ce sous-groupe'}), 400
    
    db.session.delete(sg)
    db.session.commit()
    return jsonify({'success': True})



@app.route('/parametres')
@login_required
def parametres():
    """Page des paramètres généraux de l'application"""
    # Vérifier que l'utilisateur est admin
    if current_user.role != 'admin':
        flash('Accès réservé aux administrateurs', 'danger')
        return redirect(url_for('dashboard'))
    
    # Récupérer tous les paramètres actuels
    parametres = Parametre.query.all()
    parametres_dict = {p.cle: p.valeur for p in parametres}
    
    # Récupérer les groupes scolaires
    groupes = GroupeScolaire.query.order_by(GroupeScolaire.ordre).all()
    
    # Récupérer les sous-groupes
    sous_groupes = SousGroupe.query.order_by(SousGroupe.groupe_id, SousGroupe.ordre).all()
    
    # Récupérer les types de frais
    types_frais = TypeFrais.query.order_by(TypeFrais.ordre).all()
    
    # Récupérer les options de transport
    transports = OptionTransport.query.order_by(OptionTransport.ordre).all()
    
    # Récupérer les options de cantine
    cantines = OptionCantine.query.order_by(OptionCantine.ordre).all()
    
    # Récupérer les tarifs
    tarifs = TarifFrais.query.order_by(TarifFrais.sous_groupe_id, TarifFrais.type_frais_id).all()
    tarifs_affectes = TarifFraisAffecte.query.order_by(TarifFraisAffecte.sous_groupe_id).all()
    
    return render_template('parametres.html',
                         parametres=parametres_dict,
                         groupes=groupes,
                         sous_groupes=sous_groupes,
                         types_frais=types_frais,
                         transports=transports,
                         cantines=cantines,
                         tarifs=tarifs,
                         tarifs_affectes=tarifs_affectes)


@app.route('/parametres/generaux', methods=['POST'])
@login_required
def sauvegarder_parametres_generaux():
    """Sauvegarde les paramètres généraux"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    try:
        # Paramètres généraux
        params = {
            'nom_ecole': request.form.get('nom_ecole', 'GestScolaire'),
            'devise': request.form.get('devise', 'FCFA'),
            'annee_scolaire': request.form.get('annee_scolaire', '2024-2025'),
            'frais_inscription': request.form.get('frais_inscription', '0'),
            'delai_paiement': request.form.get('delai_paiement', '30'),
            'penalite_retard': request.form.get('penalite_retard', '0'),
            'email_notification': request.form.get('email_notification', ''),
            'telephone_ecole': request.form.get('telephone_ecole', ''),
            'adresse_ecole': request.form.get('adresse_ecole', ''),
        }
        
        for cle, valeur in params.items():
            param = Parametre.query.filter_by(cle=cle).first()
            if param:
                param.valeur = valeur
            else:
                param = Parametre(cle=cle, valeur=valeur)
                db.session.add(param)
        
        db.session.commit()
        log_action('PARAMETRES', 'Mise à jour des paramètres généraux')
        flash('Paramètres généraux sauvegardés avec succès', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la sauvegarde : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/groupes/ajouter', methods=['POST'])
@login_required
def param_ajouter_groupe():
    """Ajoute un nouveau groupe scolaire"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    try:
        nom = request.form.get('nom')
        code = request.form.get('code')
        ordre = request.form.get('ordre', 0)
        description = request.form.get('description', '')
        
        if not nom or not code:
            flash('Le nom et le code sont obligatoires', 'warning')
            return redirect(url_for('parametres'))
        
        # Vérifier si le code existe déjà
        if GroupeScolaire.query.filter_by(code=code).first():
            flash(f'Le code {code} existe déjà', 'warning')
            return redirect(url_for('parametres'))
        
        groupe = GroupeScolaire(
            nom=nom,
            code=code,
            ordre=int(ordre),
            description=description
        )
        db.session.add(groupe)
        db.session.commit()
        
        log_action('AJOUT_GROUPE', f'Ajout du groupe {nom}')
        flash(f'Groupe {nom} ajouté avec succès', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de l\'ajout : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/groupes/<int:id>/modifier', methods=['POST'])
@login_required
def param_modifier_groupe(id):
    """Modifie un groupe scolaire"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    groupe = GroupeScolaire.query.get_or_404(id)
    
    try:
        groupe.nom = request.form.get('nom')
        groupe.code = request.form.get('code')
        groupe.ordre = int(request.form.get('ordre', 0))
        groupe.description = request.form.get('description', '')
        groupe.actif = 'actif' in request.form
        
        db.session.commit()
        log_action('MODIFIER_GROUPE', f'Modification du groupe {groupe.nom}')
        flash(f'Groupe {groupe.nom} modifié avec succès', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la modification : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/groupes/<int:id>/supprimer')
@login_required
def param_supprimer_groupe(id):
    """Supprime un groupe scolaire"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    groupe = GroupeScolaire.query.get_or_404(id)
    
    try:
        # Vérifier s'il y a des sous-groupes
        if groupe.sous_groupes:
            flash(f'Impossible de supprimer {groupe.nom} car il contient des sous-groupes', 'danger')
            return redirect(url_for('parametres'))
        
        nom = groupe.nom
        db.session.delete(groupe)
        db.session.commit()
        
        log_action('SUPPRIMER_GROUPE', f'Suppression du groupe {nom}')
        flash(f'Groupe {nom} supprimé avec succès', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la suppression : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/sous-groupes/ajouter', methods=['POST'])
@login_required
def param_ajouter_sous_groupe():
    """Ajoute un nouveau sous-groupe"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    try:
        nom = request.form.get('nom')
        code = request.form.get('code')
        groupe_id = request.form.get('groupe_id')
        ordre = request.form.get('ordre', 0)
        description = request.form.get('description', '')
        
        if not nom or not code or not groupe_id:
            flash('Le nom, le code et le groupe sont obligatoires', 'warning')
            return redirect(url_for('parametres'))
        
        # Vérifier si le code existe déjà
        if SousGroupe.query.filter_by(code=code).first():
            flash(f'Le code {code} existe déjà', 'warning')
            return redirect(url_for('parametres'))
        
        sous_groupe = SousGroupe(
            nom=nom,
            code=code,
            groupe_id=int(groupe_id),
            ordre=int(ordre),
            description=description
        )
        db.session.add(sous_groupe)
        db.session.commit()
        
        log_action('AJOUT_SOUS_GROUPE', f'Ajout du sous-groupe {nom}')
        flash(f'Sous-groupe {nom} ajouté avec succès', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de l\'ajout : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/sous-groupes/<int:id>/modifier', methods=['POST'])
@login_required
def param_modifier_sous_groupe(id):
    """Modifie un sous-groupe"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    sous_groupe = SousGroupe.query.get_or_404(id)
    
    try:
        sous_groupe.nom = request.form.get('nom')
        sous_groupe.code = request.form.get('code')
        sous_groupe.groupe_id = int(request.form.get('groupe_id'))
        sous_groupe.ordre = int(request.form.get('ordre', 0))
        sous_groupe.description = request.form.get('description', '')
        sous_groupe.actif = 'actif' in request.form
        
        db.session.commit()
        log_action('MODIFIER_SOUS_GROUPE', f'Modification du sous-groupe {sous_groupe.nom}')
        flash(f'Sous-groupe {sous_groupe.nom} modifié avec succès', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la modification : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/sous-groupes/<int:id>/supprimer')
@login_required
def param_supprimer_sous_groupe(id):
    """Supprime un sous-groupe"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    sous_groupe = SousGroupe.query.get_or_404(id)
    
    try:
        # Vérifier s'il y a des élèves
        if sous_groupe.eleves:
            flash(f'Impossible de supprimer {sous_groupe.nom} car il contient des élèves', 'danger')
            return redirect(url_for('parametres'))
        
        nom = sous_groupe.nom
        db.session.delete(sous_groupe)
        db.session.commit()
        
        log_action('SUPPRIMER_SOUS_GROUPE', f'Suppression du sous-groupe {nom}')
        flash(f'Sous-groupe {nom} supprimé avec succès', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la suppression : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/tarifs/ajouter', methods=['POST'])
@login_required
def param_ajouter_tarif():
    """Ajoute un nouveau tarif"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    try:
        sous_groupe_id = request.form.get('sous_groupe_id')
        type_frais_id = request.form.get('type_frais_id')
        montant = request.form.get('montant', 0)
        
        if not sous_groupe_id or not type_frais_id:
            flash('Le sous-groupe et le type de frais sont obligatoires', 'warning')
            return redirect(url_for('parametres'))
        
        # Vérifier si le tarif existe déjà
        tarif_existant = TarifFrais.query.filter_by(
            sous_groupe_id=int(sous_groupe_id),
            type_frais_id=int(type_frais_id)
        ).first()
        
        if tarif_existant:
            # Mettre à jour le tarif existant
            tarif_existant.montant = float(montant)
            message = 'Tarif mis à jour avec succès'
        else:
            # Créer un nouveau tarif
            tarif = TarifFrais(
                sous_groupe_id=int(sous_groupe_id),
                type_frais_id=int(type_frais_id),
                montant=float(montant)
            )
            db.session.add(tarif)
            message = 'Tarif ajouté avec succès'
        
        db.session.commit()
        log_action('AJOUT_TARIF', f'Ajout/Modification tarif')
        flash(message, 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de l\'ajout : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/tarifs-affectes/ajouter', methods=['POST'])
@login_required
def param_ajouter_tarif_affecte():
    """Ajoute un tarif pour les élèves affectés par l'État"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    try:
        sous_groupe_id = request.form.get('sous_groupe_id')
        montant = request.form.get('montant', 0)
        est_affecte = 'est_affecte' in request.form
        
        if not sous_groupe_id:
            flash('Le sous-groupe est obligatoire', 'warning')
            return redirect(url_for('parametres'))
        
        # Vérifier si le tarif existe déjà
        tarif_existant = TarifFraisAffecte.query.filter_by(
            sous_groupe_id=int(sous_groupe_id),
            est_affecte=est_affecte
        ).first()
        
        if tarif_existant:
            # Mettre à jour le tarif existant
            tarif_existant.montant = float(montant)
            message = 'Tarif affecté mis à jour avec succès'
        else:
            # Créer un nouveau tarif
            tarif = TarifFraisAffecte(
                sous_groupe_id=int(sous_groupe_id),
                est_affecte=est_affecte,
                montant=float(montant)
            )
            db.session.add(tarif)
            message = 'Tarif affecté ajouté avec succès'
        
        db.session.commit()
        log_action('AJOUT_TARIF_AFFECTE', f'Ajout/Modification tarif affecté')
        flash(message, 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de l\'ajout : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/transports/ajouter', methods=['POST'])
@login_required
def param_ajouter_transport():
    """Ajoute une option de transport"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    try:
        nom = request.form.get('nom')
        montant_supplement = request.form.get('montant_supplement', 0)
        ordre = request.form.get('ordre', 0)
        description = request.form.get('description', '')
        
        if not nom:
            flash('Le nom est obligatoire', 'warning')
            return redirect(url_for('parametres'))
        
        transport = OptionTransport(
            nom=nom,
            montant_supplement=float(montant_supplement),
            ordre=int(ordre),
            description=description
        )
        db.session.add(transport)
        db.session.commit()
        
        log_action('AJOUT_TRANSPORT', f'Ajout du transport {nom}')
        flash(f'Option de transport {nom} ajoutée avec succès', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de l\'ajout : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/cantines/ajouter', methods=['POST'])
@login_required
def param_ajouter_cantine():
    """Ajoute une option de cantine"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    try:
        nom = request.form.get('nom')
        montant = request.form.get('montant', 0)
        description = request.form.get('description', '')
        
        if not nom:
            flash('Le nom est obligatoire', 'warning')
            return redirect(url_for('parametres'))
        
        cantine = OptionCantine(
            nom=nom,
            montant=float(montant),
            description=description
        )
        db.session.add(cantine)
        db.session.commit()
        
        log_action('AJOUT_CANTINE', f'Ajout de la cantine {nom}')
        flash(f'Option de cantine {nom} ajoutée avec succès', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de l\'ajout : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/transports/<int:id>/modifier', methods=['POST'])
@login_required
def param_modifier_transport(id):
    """Modifie une option de transport"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    transport = OptionTransport.query.get_or_404(id)
    
    try:
        transport.nom = request.form.get('nom')
        transport.code = request.form.get('code')
        transport.montant_supplement = float(request.form.get('montant_supplement', 0))
        transport.ordre = int(request.form.get('ordre', 0))
        transport.description = request.form.get('description', '')
        transport.actif = 'actif' in request.form
        
        db.session.commit()
        log_action('MODIFIER_TRANSPORT', f'Modification du transport {transport.nom}')
        flash(f'Transport {transport.nom} modifié avec succès', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la modification : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/cantines/<int:id>/modifier', methods=['POST'])
@login_required
def param_modifier_cantine(id):
    """Modifie une option de cantine"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    cantine = OptionCantine.query.get_or_404(id)
    
    try:
        cantine.nom = request.form.get('nom')
        cantine.code = request.form.get('code')
        cantine.montant = float(request.form.get('montant', 0))
        cantine.ordre = int(request.form.get('ordre', 0))
        cantine.description = request.form.get('description', '')
        cantine.actif = 'actif' in request.form
        
        db.session.commit()
        log_action('MODIFIER_CANTINE', f'Modification de la cantine {cantine.nom}')
        flash(f'Cantine {cantine.nom} modifiée avec succès', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la modification : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


from datetime import date, datetime, timedelta

@app.route('/eleves')
@login_required
def liste_eleves():
    """Liste des élèves avec filtres selon le dossier actif"""

    dossier_actif = session.get('dossier_actif')
    groupe_dossier = session.get('groupe_dossier')
    
    sous_groupe_id = request.args.get('sous_groupe', '')
    classe = request.args.get('classe', '')
    affectation = request.args.get('affectation', '')
    statut = request.args.get('statut', '')
    cycle = request.args.get('cycle', '')
    
    query = Eleve.query.filter_by(actif=True)
    
    # 🔥 FILTRE PAR ANNÉE SCOLAIRE ACTIVE 🔥
    annee_active = get_annee_active()
    query = query.filter(Eleve.annee_scolaire == annee_active)
    
    # Filtre par dossier actif
    if dossier_actif and groupe_dossier:
        groupe = GroupeScolaire.query.filter_by(nom=groupe_dossier).first()
        if groupe:
            if dossier_actif == 'secondaire' and cycle:
                niveaux_premier = ['6ème', '5ème', '4ème', '3ème']
                niveaux_second = ['Seconde', 'Première', 'Terminale']
                niveaux = niveaux_premier if cycle == 'premier_cycle' else (niveaux_second if cycle == 'second_cycle' else niveaux_premier + niveaux_second)
                sous_groupes = SousGroupe.query.filter(SousGroupe.groupe_id == groupe.id, SousGroupe.nom.in_(niveaux)).all()
            else:
                sous_groupes = SousGroupe.query.filter_by(groupe_id=groupe.id).all()
            
            sous_groupes_ids = [sg.id for sg in sous_groupes]
            if sous_groupes_ids:
                query = query.filter(Eleve.sous_groupe_id.in_(sous_groupes_ids))
            else:
                query = query.filter(False)
        else:
            query = query.filter(False)
    
    if sous_groupe_id and sous_groupe_id.isdigit():
        query = query.filter(Eleve.sous_groupe_id == int(sous_groupe_id))
    if classe:
        query = query.filter(Eleve.classe.ilike(f'%{classe}%'))
    if affectation == 'affecte':
        query = query.filter_by(est_affecte_etat=True)
    elif affectation == 'non_affecte':
        query = query.filter_by(est_affecte_etat=False)
    if statut == 'paye':
        query = query.filter(Eleve.montant_paye >= Eleve.frais_scolarite)
    elif statut == 'partiel':
        query = query.filter(Eleve.montant_paye > 0, Eleve.montant_paye < Eleve.frais_scolarite)
    elif statut == 'impaye':
        query = query.filter(Eleve.montant_paye == 0)
    
    eleves = query.order_by(Eleve.nom, Eleve.prenom).all()
    
    # Statistiques globales
    total_eleves = len(eleves)
    total_frais = sum(e.frais_scolarite_total for e in eleves)
    total_paye = sum(e.montant_paye for e in eleves)
    reste_a_payer = total_frais - total_paye
    
    # Totaux globaux
    if dossier_actif and groupe_dossier:
        groupe_temp = GroupeScolaire.query.filter_by(nom=groupe_dossier).first()
        if groupe_temp:
            sg_ids_global = [sg.id for sg in SousGroupe.query.filter_by(groupe_id=groupe_temp.id).all()]
            total_eleves_global = Eleve.query.filter(Eleve.sous_groupe_id.in_(sg_ids_global), Eleve.annee_scolaire == annee_active).count() if sg_ids_global else 0
        else:
            total_eleves_global = 0
    else:
        total_eleves_global = Eleve.query.filter_by(annee_scolaire=annee_active).count()
    
    total_frais_global = db.session.query(func.sum(Eleve.frais_scolarite)).filter(Eleve.annee_scolaire == annee_active).scalar() or 0
    total_paye_global = db.session.query(func.sum(Eleve.montant_paye)).filter(Eleve.annee_scolaire == annee_active).scalar() or 0
    
    # ========== STATISTIQUES JOURNALIÈRES ==========
    aujourdhui = date.today()
    date_aujourdhui = aujourdhui.strftime('%d/%m/%Y')
    
    sous_groupes_ids_stats = []
    if dossier_actif and groupe_dossier:
        groupe_stats = GroupeScolaire.query.filter_by(nom=groupe_dossier).first()
        if groupe_stats:
            tous_sg = SousGroupe.query.filter_by(groupe_id=groupe_stats.id).all()
            sous_groupes_ids_stats = [sg.id for sg in tous_sg]
    
    # Élèves inscrits aujourd'hui
    if sous_groupes_ids_stats:
        eleves_inscrits_aujourdhui = Eleve.query.filter(
            func.date(Eleve.date_inscription) == aujourdhui,
            Eleve.actif == True,
            Eleve.annee_scolaire == annee_active,
            Eleve.sous_groupe_id.in_(sous_groupes_ids_stats)
        ).count()
    else:
        if not dossier_actif:
            eleves_inscrits_aujourdhui = Eleve.query.filter(
                func.date(Eleve.date_inscription) == aujourdhui,
                Eleve.actif == True,
                Eleve.annee_scolaire == annee_active
            ).count()
        else:
            eleves_inscrits_aujourdhui = 0
    
    # 🔥 Paiements du jour filtrés par période 🔥
    if sous_groupes_ids_stats:
        tous_paiements = Paiement.query.join(Eleve).filter(
            func.date(Paiement.date_paiement) == aujourdhui,
            Paiement.annee_scolaire == annee_active,
            Eleve.actif == True,
            Eleve.sous_groupe_id.in_(sous_groupes_ids_stats)
        ).all()
    else:
        if not dossier_actif:
            tous_paiements = Paiement.query.join(Eleve).filter(
                func.date(Paiement.date_paiement) == aujourdhui,
                Paiement.annee_scolaire == annee_active,
                Eleve.actif == True
            ).all()
        else:
            tous_paiements = []
    
    paiements_valides = [p for p in tous_paiements if p.montant > 0 and p.statut == 'actif']
    
    montant_total_paye_aujourdhui = sum(p.montant for p in paiements_valides)
    nombre_paiements_aujourdhui = len(paiements_valides)
    
    # Valeurs pour le template
    paiements_jour = nombre_paiements_aujourdhui
    montant_jour = montant_total_paye_aujourdhui
    montant_semaine = 0
    montant_mois = 0
    
    # Groupes pour le filtre
    if dossier_actif and groupe_dossier:
        groupes = GroupeScolaire.query.filter_by(nom=groupe_dossier).order_by(GroupeScolaire.ordre).all()
    else:
        groupes = GroupeScolaire.query.order_by(GroupeScolaire.ordre).all()
    
    # Sous-groupes pour le filtre
    if sous_groupe_id and sous_groupe_id.isdigit():
        sg = SousGroupe.query.get(int(sous_groupe_id))
        sous_groupes = SousGroupe.query.filter_by(groupe_id=sg.groupe_id).order_by(SousGroupe.ordre).all() if sg else []
    elif dossier_actif and groupe_dossier:
        groupe_sg = GroupeScolaire.query.filter_by(nom=groupe_dossier).first()
        sous_groupes = SousGroupe.query.filter_by(groupe_id=groupe_sg.id).order_by(SousGroupe.ordre).all() if groupe_sg else []
    else:
        sous_groupes = SousGroupe.query.order_by(SousGroupe.ordre).all()
    
    # Classes pour le filtre
    classes_query = db.session.query(Eleve.classe).distinct()
    if dossier_actif and groupe_dossier:
        groupe_cl = GroupeScolaire.query.filter_by(nom=groupe_dossier).first()
        if groupe_cl:
            sg_ids_cl = [sg.id for sg in SousGroupe.query.filter_by(groupe_id=groupe_cl.id).all()]
            if sg_ids_cl:
                classes_query = classes_query.filter(Eleve.sous_groupe_id.in_(sg_ids_cl))
    classes = sorted([c[0] for c in classes_query.all() if c[0]])
    
    show_affectation_filter = (dossier_actif == 'secondaire' or not dossier_actif)
    
    return render_template('eleves.html', 
                         eleves=eleves,
                         groupes=groupes,
                         sous_groupes=sous_groupes,
                         classes=classes,
                         total_eleves=total_eleves,
                         total_frais=total_frais,
                         total_paye=total_paye,
                         reste_a_payer=reste_a_payer,
                         total_eleves_global=total_eleves_global,
                         total_frais_global=total_frais_global,
                         total_paye_global=total_paye_global,
                         sous_groupe_id_active=sous_groupe_id,
                         classe_active=classe,
                         affectation_active=affectation,
                         statut_actif=statut,
                         cycle_actif=cycle,
                         dossier_actif=dossier_actif,
                         groupe_dossier=groupe_dossier,
                         show_affectation_filter=show_affectation_filter,
                         date_aujourdhui=date_aujourdhui,
                         paiements_jour=paiements_jour,
                         montant_jour=montant_jour,
                         montant_semaine=montant_semaine,
                         montant_mois=montant_mois,
                         eleves_inscrits_aujourdhui=eleves_inscrits_aujourdhui,
                         montant_total_paye_aujourdhui=montant_total_paye_aujourdhui,
                         nombre_paiements_aujourdhui=nombre_paiements_aujourdhui) 


@app.route('/api/stats-journalieres')
@login_required
def api_stats_journalieres():
    """API pour les statistiques journalières filtrées par dossier"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False}), 403
    
    aujourdhui = date.today()
    
    dossier_actif = session.get('dossier_actif')
    groupe_dossier = session.get('groupe_dossier')
    
    sous_groupes_ids = []
    if dossier_actif and groupe_dossier:
        groupe = GroupeScolaire.query.filter_by(nom=groupe_dossier).first()
        if groupe:
            tous_sous_groupes = SousGroupe.query.filter_by(groupe_id=groupe.id).all()
            sous_groupes_ids = [sg.id for sg in tous_sous_groupes]
    
    # Élèves inscrits aujourd'hui
    if sous_groupes_ids:
        eleves_inscrits = Eleve.query.filter(
            func.date(Eleve.date_inscription) == aujourdhui,
            Eleve.actif == True,
            Eleve.sous_groupe_id.in_(sous_groupes_ids)
        ).count()
    else:
        if not dossier_actif:
            eleves_inscrits = Eleve.query.filter(
                func.date(Eleve.date_inscription) == aujourdhui,
                Eleve.actif == True
            ).count()
        else:
            eleves_inscrits = 0
    
    # Récupérer TOUS les paiements du jour
    if sous_groupes_ids:
        tous_paiements = Paiement.query.join(Eleve).filter(
            func.date(Paiement.date_paiement) == aujourdhui,
            Eleve.actif == True,
            Eleve.sous_groupe_id.in_(sous_groupes_ids)
        ).all()
    else:
        if not dossier_actif:
            tous_paiements = Paiement.query.join(Eleve).filter(
                func.date(Paiement.date_paiement) == aujourdhui,
                Eleve.actif == True
            ).all()
        else:
            tous_paiements = []
    
    # 🔥 FILTRE : Uniquement les paiements ACTIFS avec montant positif 🔥
    paiements_valides = [p for p in tous_paiements if p.montant > 0 and p.statut == 'actif']
    
    montant_total = sum(p.montant for p in paiements_valides)
    nombre_paiements = len(paiements_valides)
    
    nom_dossier = dossier_actif.capitalize() if dossier_actif else 'Tous'
    
    return jsonify({
        'success': True,
        'date': aujourdhui.strftime('%d/%m/%Y'),
        'dossier': nom_dossier,
        'eleves_inscrits': eleves_inscrits,
        'montant_total_paye': montant_total,
        'nombre_paiements': nombre_paiements
    })


@app.route('/eleve/ajouter', methods=['GET', 'POST'])
@login_required
def ajouter_eleve():
    # Récupérer le dossier actif depuis la session
    dossier_actif = session.get('dossier_actif')
    groupe_dossier = session.get('groupe_dossier')
    
    if request.method == 'POST':
        try:
            # Récupération des informations de base
            nom = request.form.get('nom')
            prenom = request.form.get('prenom')
            genre = request.form.get('genre', '').upper()
            classe = request.form.get('classe')
            sous_groupe_id = request.form.get('sous_groupe_id')
            matricule = request.form.get('matricule')
            
            # === VÉRIFICATION : L'élève appartient bien au dossier actif ===
            if dossier_actif and sous_groupe_id:
                sous_groupe = SousGroupe.query.get(int(sous_groupe_id))
                if sous_groupe and sous_groupe.groupe_parent:
                    nom_groupe_eleve = sous_groupe.groupe_parent.nom
                    # Vérifier que le groupe correspond au dossier actif
                    if nom_groupe_eleve != groupe_dossier:
                        flash(f'Erreur : Vous ne pouvez pas ajouter un élève de {nom_groupe_eleve} depuis le dossier {dossier_actif}', 'danger')
                        return redirect(url_for('ajouter_eleve'))
            
            # Nouvelles colonnes
            date_naissance_str = request.form.get('date_naissance', '').strip()
            date_naissance = None
            if date_naissance_str:
                try:
                    date_naissance = datetime.strptime(date_naissance_str, '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    date_naissance = None
            lieu_naissance = request.form.get('lieu_naissance', '')
            adresse = request.form.get('adresse', '')
            nom_parent = request.form.get('nom_parent', '')
            telephone_parent = request.form.get('telephone_parent', '')
            
            # Générer le matricule automatiquement si vide
            if not matricule:
                matricule = generate_matricule()
            
            # Affectation État
            est_affecte_etat = 'est_affecte_etat' in request.form
            reference_affectation = request.form.get('reference_affectation') if est_affecte_etat else None
            organisme_affectation = request.form.get('organisme_affectation', 'État')
            
            # Options
            transport_option_id = request.form.get('transport_option_id') or None
            cantine_option_id = request.form.get('cantine_option_id') or None
            renforcement_inscrit = 'renforcement_inscrit' in request.form
            
            # Calcul du montant total des frais
            frais_total = calculer_frais_total(
                sous_groupe_id=sous_groupe_id,
                est_affecte=est_affecte_etat,
                transport_option_id=transport_option_id,
                cantine_option_id=cantine_option_id,
                renforcement_inscrit=renforcement_inscrit,
                classe=classe
            )
            
            # Validation du genre
            if genre and genre not in ['M', 'F']:
                flash('Genre invalide', 'danger')
                return redirect(url_for('ajouter_eleve'))
            
            # Création de l'élève
            eleve = Eleve(
                nom=nom,
                prenom=prenom,
                genre=genre,
                classe=classe,
                sous_groupe_id=sous_groupe_id,
                matricule=matricule,
                date_naissance=date_naissance,
                lieu_naissance=lieu_naissance,
                adresse=adresse,
                nom_parent=nom_parent,
                telephone_parent=telephone_parent,
                est_affecte_etat=est_affecte_etat,
                reference_affectation=reference_affectation,
                organisme_affectation=organisme_affectation,
                transport_option_id=transport_option_id,
                cantine_option_id=cantine_option_id,
                renforcement_inscrit=renforcement_inscrit,
                frais_scolarite=frais_total,
                montant_paye=0,
                date_inscription=datetime.utcnow()
            )
            
            db.session.add(eleve)
            db.session.commit()
            
            log_action('AJOUT_ELEVE', 
                       f"Ajout de l'élève {eleve.nom_complet} - Genre: {genre} - Classe: {classe}")
            flash(f'Élève {eleve.nom_complet} ajouté avec succès !', 'success')
            return redirect(url_for('liste_eleves'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erreur lors de l\'ajout : {str(e)}', 'danger')
    
    # ========== GET : Filtrer les groupes selon le dossier actif ==========
    if dossier_actif and groupe_dossier:
        # Afficher UNIQUEMENT le groupe correspondant au dossier actif
        groupes = GroupeScolaire.query.filter_by(nom=groupe_dossier).order_by(GroupeScolaire.ordre).all()
    else:
        # Aucun dossier sélectionné : afficher tous les groupes
        groupes = GroupeScolaire.query.order_by(GroupeScolaire.ordre).all()
    
    transports = OptionTransport.query.filter_by(actif=True).order_by(OptionTransport.ordre).all()
    cantines = OptionCantine.query.filter_by(actif=True).all()
    
    return render_template('ajouter_eleve.html', 
                         groupes=groupes,
                         transports=transports,
                         cantines=cantines,
                         dossier_actif=dossier_actif)




@app.route('/eleve/<int:id>/modifier', methods=['GET', 'POST'])
@login_required
def modifier_eleve(id):
    eleve = Eleve.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            # Vérifier l'unicité du matricule AVANT modification
            nouveau_matricule = request.form.get('matricule', '').strip()
            if nouveau_matricule and nouveau_matricule != eleve.matricule:
                matricule_existant = Eleve.query.filter(
                    Eleve.matricule == nouveau_matricule,
                    Eleve.id != id
                ).first()
                if matricule_existant:
                    flash(f'Le matricule {nouveau_matricule} existe déjà pour un autre élève', 'danger')
                    return redirect(url_for('modifier_eleve', id=id))
            
            # Mise à jour des informations de base
            eleve.nom = request.form.get('nom')
            eleve.prenom = request.form.get('prenom')
            eleve.genre = request.form.get('genre', '').upper()
            eleve.classe = request.form.get('classe')
            eleve.sous_groupe_id = request.form.get('sous_groupe_id')
            eleve.matricule = nouveau_matricule
            
            # Nouvelles colonnes - CONVERSION DE LA DATE
            date_naissance_str = request.form.get('date_naissance')
            if date_naissance_str:
                try:
                    # Conversion de la chaîne en objet date Python
                    eleve.date_naissance = datetime.strptime(date_naissance_str, '%Y-%m-%d').date()
                except (ValueError, TypeError):
                    flash('Format de date de naissance invalide. Utilisez le format AAAA-MM-JJ', 'warning')
                    eleve.date_naissance = None
            else:
                eleve.date_naissance = None
                
            eleve.lieu_naissance = request.form.get('lieu_naissance', '')
            eleve.adresse = request.form.get('adresse', '')
            eleve.nom_parent = request.form.get('nom_parent', '')
            eleve.telephone_parent = request.form.get('telephone_parent', '')
            
            # Mise à jour affectation État
            ancien_statut = eleve.est_affecte_etat
            eleve.est_affecte_etat = 'est_affecte_etat' in request.form
            eleve.reference_affectation = request.form.get('reference_affectation') if eleve.est_affecte_etat else None
            eleve.organisme_affectation = request.form.get('organisme_affectation', 'État')
            
            # Mise à jour des options
            eleve.transport_option_id = request.form.get('transport_option_id') or None
            eleve.cantine_option_id = request.form.get('cantine_option_id') or None
            eleve.renforcement_inscrit = 'renforcement_inscrit' in request.form
            
            # Recalcul du montant total des frais
            nouveau_frais_total = calculer_frais_total(
                sous_groupe_id=eleve.sous_groupe_id,
                est_affecte=eleve.est_affecte_etat,
                transport_option_id=eleve.transport_option_id,
                cantine_option_id=eleve.cantine_option_id,
                renforcement_inscrit=eleve.renforcement_inscrit,
                classe=eleve.classe
            )
            
            # Si le montant total change, ajuster
            if nouveau_frais_total != eleve.frais_scolarite:
                eleve.frais_scolarite = nouveau_frais_total
            
            db.session.commit()
            
            log_action('MODIFIER_ELEVE', 
                       f"Modification de {eleve.nom_complet} - Genre: {eleve.genre}")
            flash('Élève modifié avec succès !', 'success')
            return redirect(url_for('liste_eleves'))
            
        except Exception as e:
            db.session.rollback()
            # Log l'erreur complète pour le débogage
            app.logger.error(f"Erreur modification élève {id}: {str(e)}", exc_info=True)
            flash(f'Erreur lors de la modification : {str(e)}', 'danger')
    
    # GET: Afficher le formulaire avec les données existantes
    groupes = GroupeScolaire.query.order_by(GroupeScolaire.ordre).all()
    transports = OptionTransport.query.filter_by(actif=True).order_by(OptionTransport.ordre).all()
    cantines = OptionCantine.query.filter_by(actif=True).all()
    
    # Récupérer les sous-groupes du groupe de l'élève
    sous_groupes = []
    if eleve.sous_groupe:
        sous_groupes = SousGroupe.query.filter_by(
            groupe_id=eleve.sous_groupe.groupe_parent.id
        ).order_by(SousGroupe.ordre).all()
    
    return render_template('modifier_eleve.html', 
                         eleve=eleve,
                         groupes=groupes,
                         sous_groupes=sous_groupes,
                         transports=transports,
                         cantines=cantines)


@app.route('/eleve/<int:id>/supprimer')
@login_required
def supprimer_eleve(id):
    eleve = Eleve.query.get_or_404(id)
    nom_eleve = f"{eleve.prenom} {eleve.nom}"
    
    try:
        # Vérifier s'il y a des paiements associés
        from models import Paiement
        paiements = Paiement.query.filter_by(eleve_id=id).count()
        
        if paiements > 0:
            flash(f'Impossible de supprimer {nom_eleve} car il a {paiements} paiement(s) associé(s).', 'danger')
            return redirect(url_for('liste_eleves'))
        
        db.session.delete(eleve)
        db.session.commit()
        log_action('SUPPRIMER_ELEVE', f"Suppression de l'élève {nom_eleve}")
        flash(f'Élève {nom_eleve} supprimé avec succès !', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la suppression : {str(e)}', 'danger')
    
    return redirect(url_for('liste_eleves'))


# Fonctions utilitaires
def generate_matricule():
    """Génère un matricule unique au format ANNEE-XXXX"""
    from models import Eleve
    import random
    import string
    
    annee = datetime.now().year
    while True:
        suffixe = ''.join(random.choices(string.digits, k=4))
        matricule = f"{annee}-{suffixe}"
        existing = Eleve.query.filter_by(matricule=matricule).first()
        if not existing:
            return matricule


def calculer_frais_total(sous_groupe_id, est_affecte, transport_option_id=None, 
                         cantine_option_id=None, renforcement_inscrit=False, classe=None):
    """Calcule le montant total des frais pour un élève"""
    total = 0
    
    # 1. Frais de scolarité selon niveau et statut d'affectation
    if sous_groupe_id:
        tarif = TarifFraisAffecte.query.filter_by(
            sous_groupe_id=sous_groupe_id,
            est_affecte=est_affecte,
            actif=True
        ).first()
        
        if tarif:
            total += tarif.montant
        else:
            # Fallback: chercher le tarif normal
            tarif_normal = TarifFraisAffecte.query.filter_by(
                sous_groupe_id=sous_groupe_id,
                est_affecte=False,
                actif=True
            ).first()
            if tarif_normal:
                total += tarif_normal.montant
    
    # 2. Frais de transport
    if transport_option_id:
        transport = OptionTransport.query.get(transport_option_id)
        if transport:
            total += transport.montant_supplement
    
    # 3. Frais de cantine
    if cantine_option_id:
        cantine = OptionCantine.query.get(cantine_option_id)
        if cantine:
            total += cantine.montant
    
    # 4. Frais de renforcement (si obligatoire ou inscrit)
    if renforcement_inscrit:
        # Vérifier si le renforcement est obligatoire pour cette classe
        classes_renforcement = ['CM1', 'CM2', '3ème', 'Terminale']
        if classe in classes_renforcement or renforcement_inscrit:
            tarif_renf = TarifFrais.query.filter_by(
                type_frais_id=4,  # Renforcement
                sous_groupe_id=sous_groupe_id,
                actif=True
            ).first()
            if tarif_renf:
                total += tarif_renf.montant
    
    return total


# API Routes
@app.route('/api/groupes/<int:groupe_id>/sous-groupes')
@login_required
def api_get_sous_groupes(groupe_id):
    """API : Récupère les sous-groupes d'un groupe"""
    groupe = GroupeScolaire.query.get_or_404(groupe_id)
    
    if groupe.nom == 'Secondaire':
        # SECONDaire : retourne uniquement les CYCLES
        sous_groupes = SousGroupe.query.filter(
            SousGroupe.groupe_id == groupe_id,
            SousGroupe.code.in_(['CYCLE_PREMIER', 'CYCLE_SECOND']),
            SousGroupe.actif == True
        ).order_by(SousGroupe.ordre).all()
    else:
        # MATERNELLE et PRIMAIRE : retourne tous les niveaux
        sous_groupes = SousGroupe.query.filter_by(
            groupe_id=groupe_id,
            actif=True
        ).order_by(SousGroupe.ordre).all()
    
    return jsonify([{
        'id': sg.id,
        'nom': sg.nom,
        'code': sg.code,
        'description': sg.description or ''
    } for sg in sous_groupes])


@app.route('/api/groupes/<int:groupe_id>/sous-groupes/all')
@login_required
def api_get_sous_groupes_all(groupe_id):
    """API : Récupère TOUS les sous-groupes d'un groupe (sans filtre cycle)"""
    groupe = GroupeScolaire.query.get_or_404(groupe_id)
    
    # Retourne TOUS les sous-groupes actifs du groupe
    sous_groupes = SousGroupe.query.filter_by(
        groupe_id=groupe_id,
        actif=True
    ).order_by(SousGroupe.ordre).all()
    
    return jsonify([{
        'id': sg.id,
        'nom': sg.nom,
        'code': sg.code,
        'description': sg.description or ''
    } for sg in sous_groupes])


@app.route('/api/sous-groupe/<int:sous_groupe_id>/tarifs-affectation')
@login_required
def api_get_tarifs_affectation(sous_groupe_id):
    """API: Récupère les tarifs normal et affecté pour un niveau"""
    tarif_normal = TarifFraisAffecte.query.filter_by(
        sous_groupe_id=sous_groupe_id,
        est_affecte=False,
        actif=True
    ).first()
    
    tarif_affecte = TarifFraisAffecte.query.filter_by(
        sous_groupe_id=sous_groupe_id,
        est_affecte=True,
        actif=True
    ).first()
    
    return jsonify({
        'tarif_normal': tarif_normal.montant if tarif_normal else 0,
        'tarif_affecte': tarif_affecte.montant if tarif_affecte else 0
    })


@app.route('/api/groupes/<int:groupe_id>/sous-groupes/all')
@login_required
def api_get_all_sous_groupes(groupe_id):
    """API : Récupère TOUS les sous-groupes (sans filtre cycle)"""
    sous_groupes = SousGroupe.query.filter_by(
        groupe_id=groupe_id,
        actif=True
    ).order_by(SousGroupe.ordre).all()
    
    return jsonify([{
        'id': sg.id,
        'nom': sg.nom,
        'code': sg.code
    } for sg in sous_groupes])


@app.route('/api/options-transport')
@login_required
def api_get_options_transport():
    """API: Récupère les options de transport"""
    options = OptionTransport.query.filter_by(actif=True).order_by(OptionTransport.ordre).all()
    return jsonify([{'id': o.id, 'nom': o.nom, 'montant': o.montant_supplement} for o in options])


@app.route('/api/options-cantine')
@login_required
def api_get_options_cantine():
    """API: Récupère les options de cantine"""
    options = OptionCantine.query.filter_by(actif=True).all()
    return jsonify([{'id': o.id, 'nom': o.nom, 'montant': o.montant} for o in options])


@app.route('/api/sous-groupe/<int:sous_groupe_id>/tarifs')
@login_required
def api_get_tarifs_by_niveau(sous_groupe_id):
    """API: Récupère les tarifs pour un niveau donné (scolarité, renforcement)"""
    tarifs = TarifFrais.query.filter_by(sous_groupe_id=sous_groupe_id, actif=True).all()
    return jsonify([{
        'type_frais_id': t.type_frais_id,
        'type_frais_nom': t.type_frais.nom,
        'montant': t.montant,
        'est_obligatoire': t.est_obligatoire
    } for t in tarifs])


@app.route('/api/eleve/<int:eleve_id>/frais-details')
@login_required
def api_get_eleve_frais_details(eleve_id):
    """API: Récupère les détails des frais d'un élève"""
    eleve = Eleve.query.get_or_404(eleve_id)
    
    return jsonify({
        'scolarite_base': eleve.frais_scolarite_base,
        'transport': {
            'montant': eleve.frais_transport,
            'option': eleve.transport_option.nom if eleve.transport_option else None
        },
        'cantine': {
            'montant': eleve.frais_cantine,
            'option': eleve.cantine_option.nom if eleve.cantine_option else None
        },
        'renforcement': {
            'montant': eleve.frais_renforcement,
            'obligatoire': eleve.est_renforcement_obligatoire,
            'inscrit': eleve.renforcement_inscrit
        },
        'total': eleve.frais_scolarite_total,
        'paye': eleve.montant_paye,
        'solde': eleve.solde,
        'est_affecte': eleve.est_affecte_etat
    })



# ============ GESTION DES PAIEMENTS ============

@app.route('/eleve/<int:id>/paiements')
@login_required
def voir_paiements(id):
    """Affiche l'historique des paiements d'un élève"""
    eleve = Eleve.query.get_or_404(id)
    paiements = Paiement.query.filter_by(eleve_id=id)\
                              .order_by(Paiement.date_paiement.desc())\
                              .all()
    
    return render_template('paiements_eleve.html', 
                         eleve=eleve, 
                         paiements=paiements)



@app.route('/paiement/ajouter/<int:eleve_id>', methods=['POST'])
@login_required
def ajouter_paiement(eleve_id):
    """Ajoute un paiement pour un élève (AJAX)"""
    
    # === DEBUG : Voir tout ce qui est reçu ===
    print("=" * 50)
    print("DEBUG PAIEMENT")
    print(f"eleve_id: {eleve_id}")
    print(f"Method: {request.method}")
    print(f"Content-Type: {request.content_type}")
    print(f"Form data: {dict(request.form)}")
    print(f"All data: {request.form}")
    print("=" * 50)
    
    if current_user.role not in ['admin', 'comptable']:
        print("ERREUR: Accès non autorisé")
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    eleve = Eleve.query.get(eleve_id)
    if not eleve:
        print(f"ERREUR: Élève {eleve_id} non trouvé")
        return jsonify({'success': False, 'message': 'Élève non trouvé'}), 404
    
    try:
        # Récupération du montant
        montant_str = request.form.get('montant', '')
        print(f"Montant brut reçu: '{montant_str}' (type: {type(montant_str)})")
        
        # Nettoyage
        montant_str = str(montant_str).strip().replace(' ', '').replace(',', '.')
        print(f"Montant après nettoyage: '{montant_str}'")
        
        if not montant_str:
            print("ERREUR: Montant vide")
            return jsonify({'success': False, 'message': 'Le montant est requis'}), 400
        
        montant = float(montant_str)
        print(f"Montant converti: {montant}")
        
        if montant <= 0:
            print(f"ERREUR: Montant négatif ou nul ({montant})")
            return jsonify({'success': False, 'message': 'Le montant doit être supérieur à 0 FCFA'}), 400
        
        if montant > eleve.solde:
            print(f"ERREUR: Montant {montant} > solde {eleve.solde}")
            return jsonify({
                'success': False, 
                'message': f'Le montant ({montant:,.0f} FCFA) dépasse le solde ({eleve.solde:,.0f} FCFA)'
            }), 400
        
        # Récupération des autres champs
        type_paiement = request.form.get('type_paiement', 'especes')
        reference = request.form.get('reference', '')
        description = request.form.get('description', '')
        
        print(f"Type paiement: {type_paiement}")
        print(f"Référence: {reference}")
        print(f"Description: {description}")
        
        # Génération reçu
        try:
            num_recu = generer_numero_recu()
            print(f"Reçu généré: {num_recu}")
        except Exception as e:
            print(f"ERREUR génération reçu: {e}")
            num_recu = f"TEMP-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            print(f"Reçu fallback: {num_recu}")
        
        # Création paiement
        print("Création du paiement...")
        paiement = Paiement(
            eleve_id=eleve.id,
            montant=montant,
            type_paiement=type_paiement,
            reference=reference if reference else None,
            description=description if description else None,
            recu=num_recu,
            encaisse_par=current_user.username,
            annee_scolaire=get_annee_active()
        )
        
        # Mise à jour élève
        ancien_paye = eleve.montant_paye
        eleve.montant_paye += montant
        print(f"Montant payé: {ancien_paye} -> {eleve.montant_paye}")
        
        # Sauvegarde
        db.session.add(paiement)
        db.session.commit()
        print("✅ Paiement enregistré avec succès!")
        
        return jsonify({
            'success': True,
            'message': f'Paiement de {montant:,.0f} FCFA enregistré !',
            'montant': montant,
            'recu': num_recu,
            'nouveau_solde': eleve.solde,
            'nouveau_montant_paye': eleve.montant_paye,
            'nouveau_statut': eleve.statut_paiement,
            'nouveau_taux': eleve.taux_paiement
        })
        
    except ValueError as e:
        db.session.rollback()
        print(f"❌ ERREUR ValueError: {e}")
        return jsonify({'success': False, 'message': f'Montant invalide: {montant_str}'}), 400
    except Exception as e:
        db.session.rollback()
        import traceback
        print(f"❌ ERREUR Exception: {e}")
        print(traceback.format_exc())
        return jsonify({'success': False, 'message': f'Erreur: {str(e)}'}), 500
    


@app.route('/paiement/<int:id>/annuler', methods=['GET', 'POST'])
@login_required
def annuler_paiement(id):
    """Annule un paiement existant et crée un avoir"""
    if current_user.role not in ['admin', 'comptable']:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('liste_eleves'))
    
    paiement = Paiement.query.get_or_404(id)
    eleve = paiement.eleve
    montant = paiement.montant
    num_recu = paiement.recu
    
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json
    
    if request.method == 'POST':
        try:
            raison = request.form.get('raison', '').strip()
            
            if is_ajax:
                confirmation = request.form.get('confirmation')
                if confirmation != 'CONFIRMER':
                    return jsonify({'success': False, 'message': 'Confirmation incorrecte'})
            
            # --- REVERSE LE MONTANT ---
            eleve.montant_paye -= montant
            
            # --- GÉNÉRER LE NUMÉRO D'AVOIR ---
            if num_recu and '-' in num_recu:
                suffixe = num_recu.split('-', 1)[1]
                numero_avoir = f"AV-{suffixe}"
            else:
                aujourdhui = datetime.utcnow().strftime('%Y%m%d')
                numero_avoir = f"AV-{aujourdhui}-{num_recu or '0001'}"
            
            # --- NETTOYER LES DÉPENDANCES ---
            PaiementDepot.query.filter_by(paiement_id=id).delete()
            
            # 🔥 MARQUER LE PAIEMENT COMME ANNULÉ (sans le supprimer) 🔥
            paiement.statut = 'annule'
            paiement.annule_le = datetime.utcnow()
            paiement.annule_par = current_user.prenom + ' ' + current_user.nom if hasattr(current_user, 'prenom') else current_user.username
            paiement.motif_annulation = raison
            
            # --- CRÉER L'AVOIR ---
            avoir = Paiement(
                eleve_id=eleve.id,
                montant=-montant,
                type_paiement=paiement.type_paiement,
                date_paiement=datetime.utcnow(),
                recu=numero_avoir,
                reference=f"Annulation reçu {num_recu}",
                description=f"AVOIR - Annulation paiement {num_recu}" + (f" | Raison: {raison}" if raison else ""),
                encaisse_par=current_user.prenom + ' ' + current_user.nom if hasattr(current_user, 'prenom') else current_user.username,
                statut='avoir'
            )
            db.session.add(avoir)
            
            db.session.commit()
            
            log_action('ANNULER_PAIEMENT', 
                       f"Annulation du paiement {num_recu} de {montant:,.0f} FCFA. Avoir créé: {numero_avoir}")
            
            if is_ajax:
                return jsonify({
                    'success': True,
                    'message': f'Paiement de {montant:,.0f} FCFA annulé | Avoir: {numero_avoir}',
                    'eleve_id': eleve.id,
                    'nouveau_montant_paye': eleve.montant_paye,
                    'nouveau_solde': eleve.solde,
                    'nouveau_statut': eleve.statut_paiement,
                    'avoir_id': avoir.id,
                    'avoir_recu': numero_avoir
                })
            else:
                flash(f'✅ Paiement annulé ! Avoir: {numero_avoir}', 'success')
                return redirect(url_for('voir_paiements', id=eleve.id))
            
        except Exception as e:
            db.session.rollback()
            import traceback
            traceback.print_exc()
            if is_ajax:
                return jsonify({'success': False, 'message': str(e)}), 500
            flash(f'Erreur : {str(e)}', 'danger')
            return redirect(url_for('voir_paiements', id=eleve.id))
    
    return render_template('paiements/annuler_paiement.html',
                         paiement=paiement,
                         eleve=eleve,
                         montant=montant)


# ========== API ROUTES POUR LES PAIEMENTS (AJAX) ==========


@app.route('/api/paiement/<int:paiement_id>')
@login_required
def api_get_paiement(paiement_id):
    """Récupère les détails d'un paiement spécifique (pour AJAX)"""
    try:
        paiement = Paiement.query.get_or_404(paiement_id)
        eleve = paiement.eleve
        
        # Vérifier les permissions
        if current_user.role not in ['admin', 'comptable'] and eleve.user_id != current_user.id:
            return jsonify({'error': 'Accès non autorisé'}), 403
        
        # CORRECTION : Utiliser date_paiement si date_creation n'existe pas
        return jsonify({
            'paiement': {
                'id': paiement.id,
                'montant': float(paiement.montant),
                'date_paiement': paiement.date_paiement.strftime('%Y-%m-%d') if paiement.date_paiement else None,
                'date_creation': paiement.date_paiement.strftime('%Y-%m-%d %H:%M:%S') if paiement.date_paiement else None,  # ← Correction ici
                'mode_paiement': getattr(paiement, 'type_paiement', 'especes'),
                'reference': getattr(paiement, 'reference', None),
                'recu': getattr(paiement, 'recu', None),
                'description': getattr(paiement, 'description', None),
                'observations': getattr(paiement, 'description', None)
            },
            'eleve': {
                'id': eleve.id,
                'nom': eleve.nom,
                'prenom': eleve.prenom,
                'matricule': eleve.matricule,
                'classe': eleve.classe
            }
        })
        
    except Exception as e:
        import traceback
        print(f"Erreur dans api_get_paiement: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
    
# routes.py

def get_parametres_ecole():
    """Récupère les paramètres de l'école depuis la base de données"""
    from models import Parametre
    
    params = Parametre.query.all()
    parametres = {p.cle: p.valeur for p in params}
    
    # Valeurs par défaut si vides
    defaults = {
        'nom_ecole': 'GS LAUREADES',
        'devise': 'FCFA',
        'annee_scolaire': '2024-2025',
        'adresse_ecole': '',
        'telephone_ecole': '',
        'email_notification': ''
    }
    
    for key, value in defaults.items():
        if key not in parametres or not parametres.get(key):
            parametres[key] = value
    
    return parametres


@app.route('/paiement/<int:paiement_id>/recu')
@login_required
def imprimer_recu_unique(paiement_id):
    paiement = Paiement.query.get_or_404(paiement_id)
    eleve = paiement.eleve
    
    # 🟢 FORCER la récupération de la DB
    from models import Parametre
    params = Parametre.query.all()
    parametres_ecole = {p.cle: p.valeur for p in params}
    
    # 🟢 Afficher dans la console pour déboguer
    print("nom_ecole =", parametres_ecole.get('nom_ecole'))
    print("annee_scolaire =", parametres_ecole.get('annee_scolaire'))
    print("devise =", parametres_ecole.get('devise'))
    
    return render_template('recus/recu_unique.html',
                         paiement=paiement,
                         eleve=eleve,
                         parametres_ecole=parametres_ecole,  # ← NE PAS OUBLIER
                         date_edition=datetime.now())


@app.route('/eleve/<int:id>/recus')
@login_required
def imprimer_tous_recus(id):
    """Affiche tous les reçus d'un élève pour impression"""
    eleve = Eleve.query.get_or_404(id)
    paiements = Paiement.query.filter_by(eleve_id=id)\
                              .order_by(Paiement.date_paiement.desc())\
                              .all()
    
    # 🟢 RÉCUPÉRER LES PARAMÈTRES
    parametres_ecole = get_parametres_ecole()
    
    return render_template('recus/recus_tous.html',
                         eleve=eleve,
                         paiements=paiements,
                         parametres_ecole=parametres_ecole,  # ← IMPORTANT
                         date_edition=datetime.now())



@app.route('/paiement/<int:paiement_id>/modifier', methods=['POST'])
@login_required
def modifier_paiement(paiement_id):
    """Modifie un paiement existant (AJAX)"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    paiement = Paiement.query.get_or_404(paiement_id)
    eleve = paiement.eleve
    ancien_montant = paiement.montant
    
    try:
        nouveau_montant = float(request.form.get('montant', 0))
        nouvelle_date = datetime.strptime(request.form.get('date_paiement'), '%Y-%m-%d').date()
        nouveau_mode = request.form.get('mode_paiement')
        nouvelle_reference = request.form.get('reference', '')
        nouvelles_observations = request.form.get('observations', '')
        
        # Validation
        if nouveau_montant <= 0:
            return jsonify({'success': False, 'message': 'Le montant doit être supérieur à 0'})
        
        # Calculer le total des autres paiements
        total_autres = sum(p.montant for p in eleve.paiements if p.id != paiement_id)
        if total_autres + nouveau_montant > eleve.frais_scolarite_total:
            return jsonify({'success': False, 'message': 'Le montant total dépasse les frais de scolarité'})
        
        # Mettre à jour le paiement
        paiement.montant = nouveau_montant
        paiement.date_paiement = nouvelle_date
        paiement.type_paiement = nouveau_mode
        paiement.reference = nouvelle_reference if nouvelle_reference else None
        paiement.description = nouvelles_observations if nouvelles_observations else None
        
        # Mettre à jour le montant payé de l'élève
        difference = nouveau_montant - ancien_montant
        eleve.montant_paye += difference
        
        db.session.commit()
        
        # Log action
        log_action('MODIFIER_PAIEMENT',
                   f"Modification paiement {paiement.recu}: {ancien_montant:,.0f} → {nouveau_montant:,.0f} FCFA pour {eleve.prenom} {eleve.nom}")
        
        return jsonify({
            'success': True,
            'message': 'Paiement modifié avec succès',
            'eleve_id': eleve.id,
            'nouveau_montant_paye': eleve.montant_paye,
            'nouveau_solde': eleve.solde,
            'nouveau_statut': eleve.statut_paiement
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/paiement/<int:paiement_id>/annuler_api', methods=['POST'])
@login_required
def annuler_paiement_api(paiement_id):
    """Annule un paiement via AJAX (sans rechargement)"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    paiement = Paiement.query.get_or_404(paiement_id)
    eleve = paiement.eleve
    montant = paiement.montant
    num_recu = paiement.recu
    raison = request.form.get('raison', '')
    confirmation = request.form.get('confirmation')
    
    if confirmation != 'CONFIRMER':
        return jsonify({'success': False, 'message': 'Confirmation incorrecte'})
    
    try:
        # Déduire le montant du total payé
        eleve.montant_paye -= montant
        
        # Supprimer le paiement
        db.session.delete(paiement)
        db.session.commit()
        
        log_action('ANNULER_PAIEMENT',
                   f"Annulation paiement {num_recu} de {montant:,.0f} FCFA pour {eleve.prenom} {eleve.nom}. Raison: {raison}")
        
        return jsonify({
            'success': True,
            'message': f'Paiement de {montant:,.0f} FCFA annulé',
            'eleve_id': eleve.id,
            'nouveau_montant_paye': eleve.montant_paye,
            'nouveau_solde': eleve.solde,
            'nouveau_statut': eleve.statut_paiement
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500



# ========== GESTION BANCAIRE ==========

@app.route('/bank')
@login_required
def bank():
    """Page principale des dépôts bancaires"""
    if current_user.role not in ['admin', 'comptable']:
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('liste_eleves'))
    
    # Récupérer tous les dépôts
    depots = DepotBancaire.query.order_by(DepotBancaire.date_depot.desc()).all()
    
    # Récupérer les paiements non encore déposés
    paiements_non_deposes = Paiement.query.filter(
        ~Paiement.depots_lies.any()
    ).order_by(Paiement.date_paiement.desc()).all()
    
    return render_template('bank.html',
                         depots=depots,
                         paiements_non_deposes=paiements_non_deposes)


@app.route('/bank/generer_depot', methods=['POST'])
@login_required
def generer_depot():
    """Génère un nouveau dépôt bancaire"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    try:
        paiements_ids = request.form.getlist('paiements_ids')
        banque = request.form.get('banque', 'Banque')
        observations = request.form.get('observations', '')
        
        if not paiements_ids:
            return jsonify({'success': False, 'message': 'Aucun paiement sélectionné'})
        
        # Récupérer les paiements
        paiements = Paiement.query.filter(Paiement.id.in_(paiements_ids)).all()
        montant_total = sum(float(p.montant) for p in paiements)
        
        # Générer le numéro de dépôt
        today = datetime.now()
        numero_depot = f"DEP-{today.strftime('%Y%m%d')}-{DepotBancaire.query.count() + 1:04d}"
        
        # Créer le dépôt
        depot = DepotBancaire(
            numero_depot=numero_depot,
            montant_total=montant_total,
            statut='en_attente',
            effectue_par=current_user.username,
            banque=banque,
            observations=observations
        )
        db.session.add(depot)
        db.session.flush()
        
        # Lier les paiements
        for paiement in paiements:
            paiement_depot = PaiementDepot(
                paiement_id=paiement.id,
                depot_id=depot.id
            )
            db.session.add(paiement_depot)
        
        db.session.commit()
        
        log_action('GENERER_DEPOT',
                   f"Dépôt {numero_depot} généré: {len(paiements_ids)} paiements pour {montant_total:,.0f} FCFA")
        
        return jsonify({
            'success': True,
            'message': f'Dépôt {numero_depot} généré avec succès',
            'depot_id': depot.id
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/bank/depot/<int:depot_id>/valider', methods=['POST'])
@login_required
def valider_depot(depot_id):
    """Valide un dépôt bancaire"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    depot = DepotBancaire.query.get_or_404(depot_id)
    
    try:
        reference_banque = request.form.get('reference_banque', '')
        
        depot.statut = 'valide'
        depot.date_validation = datetime.now()
        depot.reference_banque = reference_banque
        
        db.session.commit()
        
        log_action('VALIDER_DEPOT',
                   f"Dépôt {depot.numero_depot} validé - Réf: {reference_banque}")
        
        return jsonify({
            'success': True,
            'message': f'Dépôt {depot.numero_depot} validé'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/bank/depot/<int:depot_id>/annuler', methods=['POST'])
@login_required
def annuler_depot(depot_id):
    """Annule un dépôt bancaire"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    depot = DepotBancaire.query.get_or_404(depot_id)
    
    try:
        # Supprimer les liaisons
        for lien in depot.paiements:
            db.session.delete(lien)
        
        db.session.delete(depot)
        db.session.commit()
        
        log_action('ANNULER_DEPOT',
                   f"Dépôt {depot.numero_depot} annulé")
        
        return jsonify({
            'success': True,
            'message': f'Dépôt {depot.numero_depot} annulé'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/bank/depot/<int:depot_id>/details')
@login_required
def bank_depot_details(depot_id):
    """Récupère les détails d'un dépôt avec tous les paiements liés"""
    depot = DepotBancaire.query.get_or_404(depot_id)
    
    # Récupérer tous les paiements liés
    paiements_lies = []
    for lien in depot.paiements:
        p = lien.paiement
        eleve = p.eleve
        paiements_lies.append({
            'id': p.id,
            'recu': p.recu,
            'montant': float(p.montant),
            'date_paiement': p.date_paiement.strftime('%d/%m/%Y'),
            'type_paiement': p.type_paiement,
            'reference': p.reference,
            'eleve': {
                'id': eleve.id,
                'nom': eleve.nom,
                'prenom': eleve.prenom,
                'matricule': eleve.matricule,
                'classe': eleve.classe,
                'sous_groupe': eleve.sous_groupe.nom if eleve.sous_groupe else None
            }
        })
    
    return jsonify({
        'depot': {
            'id': depot.id,
            'numero_depot': depot.numero_depot,
            'montant_total': float(depot.montant_total),
            'date_depot': depot.date_depot.strftime('%d/%m/%Y à %H:%M'),
            'date_validation': depot.date_validation.strftime('%d/%m/%Y à %H:%M') if depot.date_validation else None,
            'statut': depot.statut,
            'effectue_par': depot.effectue_par,
            'banque': depot.banque,
            'reference_banque': depot.reference_banque,
            'observations': depot.observations,
            'nombre_paiements': len(paiements_lies)
        },
        'paiements': paiements_lies
    })


# ============ API ROUTES ============

@app.route('/api/eleve/<int:eleve_id>/paiements')
@login_required
def api_eleve_paiements(eleve_id):
    """Récupère tous les paiements d'un élève pour l'année active"""
    eleve = Eleve.query.get_or_404(eleve_id)
    
    if current_user.role not in ['admin', 'comptable'] and eleve.user_id != current_user.id:
        return jsonify({'error': 'Accès non autorisé'}), 403
    
    # 🔥 Filtrer par année scolaire active 🔥
    annee_active = get_annee_active()
    paiements = Paiement.query.filter_by(
        eleve_id=eleve_id,
        annee_scolaire=annee_active
    ).order_by(Paiement.date_paiement.desc()).all()
    
    montant_paye = max(0, eleve.montant_paye)
    taux = (montant_paye / eleve.frais_scolarite_total * 100) if eleve.frais_scolarite_total > 0 else 0
    
    return jsonify({
        'eleve': {
            'id': eleve.id,
            'nom': eleve.nom,
            'prenom': eleve.prenom,
            'matricule': eleve.matricule,
            'classe': eleve.classe,
            'frais_scolarite_total': eleve.frais_scolarite_total,
            'montant_paye': montant_paye,
            'solde': eleve.frais_scolarite_total - montant_paye,
            'taux_paiement': round(taux, 1)
        },
        'paiements': [{
            'id': p.id,
            'montant': float(p.montant) if p.montant else 0,
            'date_paiement': p.date_paiement.strftime('%Y-%m-%d') if p.date_paiement else None,
            'date_creation': p.date_paiement.strftime('%Y-%m-%d %H:%M:%S') if p.date_paiement else None,
            'mode_paiement': getattr(p, 'type_paiement', 'especes'),
            'type_paiement': getattr(p, 'type_paiement', 'especes'),
            'reference': getattr(p, 'reference', None),
            'recu': getattr(p, 'recu', None),
            'description': getattr(p, 'description', None),
            'observations': getattr(p, 'description', None),
            'statut': getattr(p, 'statut', 'actif'),
            'motif_annulation': getattr(p, 'motif_annulation', None),
            'annee_scolaire': getattr(p, 'annee_scolaire', '')
        } for p in paiements]
    })


# ============ RAPPORTS ============

@app.route('/rapports')
@login_required
def rapports():
    """Page des rapports financiers"""
    if current_user.role not in ['admin', 'comptable']:
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('liste_eleves'))
    
    # Période par défaut : mois en cours
    maintenant = datetime.utcnow()
    debut_mois = maintenant.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Calculer la fin du mois
    if maintenant.month == 12:
        fin_mois = maintenant.replace(year=maintenant.year + 1, month=1, day=1) - timedelta(seconds=1)
    else:
        fin_mois = maintenant.replace(month=maintenant.month + 1, day=1) - timedelta(seconds=1)
    
    # Paiements du mois
    paiements_mois = Paiement.query.filter(
        Paiement.date_paiement >= debut_mois,
        Paiement.date_paiement <= fin_mois
    ).all()
    total_mois = sum(p.montant for p in paiements_mois)
    nb_paiements_mois = len(paiements_mois)
    
    # Paiements par classe
    paiements_par_classe = db.session.query(
        Eleve.classe,
        func.sum(Paiement.montant).label('total'),
        func.count(Paiement.id).label('nombre')
    ).join(Paiement).group_by(Eleve.classe).all()
    
    # Paiements par mode de paiement
    paiements_par_mode = db.session.query(
        Paiement.type_paiement,
        func.sum(Paiement.montant).label('total'),
        func.count(Paiement.id).label('nombre')
    ).group_by(Paiement.type_paiement).all()
    
    # Top 10 des plus gros paiements
    top_paiements = Paiement.query.order_by(Paiement.montant.desc()).limit(10).all()
    
    # Top 10 des meilleurs payeurs (utiliser montant_paye)
    top_payeurs = Eleve.query.filter(Eleve.montant_paye > 0)\
                             .order_by(Eleve.montant_paye.desc())\
                             .limit(10)\
                             .all()
    
    # Top 10 des plus gros débiteurs - filtrer en Python
    tous_les_eleves = Eleve.query.all()
    top_debiteurs = sorted([e for e in tous_les_eleves if e.solde > 0], 
                           key=lambda e: e.solde, reverse=True)[:10]
    
    # Statistiques globales - CORRECTION : utiliser frais_scolarite (colonne)
    total_eleves = Eleve.query.count()
    total_encaisse = db.session.query(func.sum(Paiement.montant)).scalar() or 0
    total_frais_attendus = db.session.query(func.sum(Eleve.frais_scolarite)).scalar() or 0
    
    # CORRECTION ICI : remplacer frais_scolarite_total par frais_scolarite
    eleves_payes = Eleve.query.filter(Eleve.montant_paye >= Eleve.frais_scolarite).count()
    eleves_partiels = Eleve.query.filter(
        Eleve.montant_paye < Eleve.frais_scolarite,
        Eleve.montant_paye > 0
    ).count()
    eleves_impayes = Eleve.query.filter(Eleve.montant_paye == 0).count()
    
    stats = {
        'total_eleves': total_eleves,
        'total_encaisse': total_encaisse,
        'total_frais_attendus': total_frais_attendus,
        'moyenne_par_eleve': total_encaisse / total_eleves if total_eleves > 0 else 0,
        'taux_global_paiement': (total_encaisse / total_frais_attendus * 100) if total_frais_attendus > 0 else 0,
        'eleves_payes': eleves_payes,
        'eleves_partiels': eleves_partiels,
        'eleves_impayes': eleves_impayes
    }
    
    return render_template('rapports.html',
                         total_mois=total_mois,
                         nb_paiements_mois=nb_paiements_mois,
                         paiements_par_classe=paiements_par_classe,
                         paiements_par_mode=paiements_par_mode,
                         top_paiements=top_paiements,
                         top_payeurs=top_payeurs,
                         top_debiteurs=top_debiteurs,
                         stats=stats,
                         debut_mois=debut_mois,
                         fin_mois=fin_mois)

@app.route('/rapports/api/paiements')
@login_required
def api_paiements():
    """API pour les graphiques (paiements des 6 derniers mois)"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'error': 'Non autorisé'}), 403
    
    # Paiements des 6 derniers mois
    six_mois = datetime.utcnow() - timedelta(days=180)
    paiements = Paiement.query.filter(Paiement.date_paiement >= six_mois).all()
    
    # Regrouper par mois
    data = {}
    for p in paiements:
        mois = p.date_paiement.strftime('%B %Y')
        data[mois] = data.get(mois, 0) + p.montant
    
    # Ordonner les mois
    mois_ordonnes = sorted(data.keys(), key=lambda x: datetime.strptime(x, '%B %Y'))
    
    return jsonify({
        'labels': mois_ordonnes,
        'values': [data[m] for m in mois_ordonnes]
    })


# ============ FONCTIONS UTILITAIRES ============

def generer_numero_recu():
    """Génère un numéro de reçu unique au format REC-YYYYMMDD-XXXX"""
    from models import Paiement
    
    today = datetime.utcnow().strftime('%Y%m%d')
    
    # Compter les reçus du jour
    count = Paiement.query.filter(
        Paiement.recu.like(f'REC-{today}-%')
    ).count()
    
    # Générer le numéro
    numero = count + 1
    return f"REC-{today}-{numero:04d}"


def log_action(action, details):
    """Journalise les actions importantes"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {action}: {details}")
    # Vous pouvez aussi enregistrer dans une table Log si vous en avez une


# ============ RECHERCHE DE PAIEMENTS ============

@app.route('/paiements/recherche')
@login_required
def rechercher_paiements():
    """Recherche des paiements par numéro de reçu"""
    if current_user.role not in ['admin', 'comptable']:
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('liste_eleves'))
    
    query = request.args.get('q', '')
    paiements = []
    
    if query:
        paiements = Paiement.query.filter(
            Paiement.recu.ilike(f'%{query}%')
        ).order_by(Paiement.date_paiement.desc()).limit(50).all()
    
    return render_template('paiements_recherche.html', 
                         paiements=paiements, 
                         query=query)


# ============ STATISTIQUES RAPIDES (pour le dashboard) ============

@app.route('/api/stats/paiements')
@login_required
def api_stats_paiements():
    """API pour les statistiques rapides (dashboard)"""
    aujourdhui = datetime.utcnow().date()
    debut_jour = datetime(aujourdhui.year, aujourdhui.month, aujourdhui.day)
    fin_jour = debut_jour + timedelta(days=1)
    
    # Paiements du jour
    paiements_jour = Paiement.query.filter(
        Paiement.date_paiement >= debut_jour,
        Paiement.date_paiement < fin_jour
    ).all()
    total_jour = sum(p.montant for p in paiements_jour)
    nb_jour = len(paiements_jour)
    
    # Paiements du mois
    debut_mois = aujourdhui.replace(day=1)
    paiements_mois = Paiement.query.filter(
        Paiement.date_paiement >= debut_mois
    ).all()
    total_mois = sum(p.montant for p in paiements_mois)
    
    # Répartition par mode de paiement
    repartition_mode = db.session.query(
        Paiement.type_paiement,
        func.sum(Paiement.montant).label('total')
    ).group_by(Paiement.type_paiement).all()
    
    return jsonify({
        'aujourdhui': {
            'total': total_jour,
            'nombre': nb_jour
        },
        'mois': {
            'total': total_mois
        },
        'repartition_mode': [{'mode': m[0], 'total': m[1]} for m in repartition_mode]
    })


@app.route('/paiement/<int:id>/recu')
@login_required
def imprimer_recu(id):
    paiement = Paiement.query.get_or_404(id)
    eleve = paiement.eleve
    
    return render_template('recus/recu_unique.html', 
                         paiement=paiement, 
                         eleve=eleve,
                         datetime=datetime) 



