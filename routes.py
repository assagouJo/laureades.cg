# routes.py
from app import app, db, login_manager
from flask import render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_user, logout_user, login_required, current_user
from models import User, Eleve, Paiement, GroupeScolaire, SousGroupe, OptionTransport, OptionCantine, \
    TarifFrais, TarifFraisAffecte, Parametre, TypeFrais, PaiementDepot, DepotBancaire, \
    verifier_depot_valide, annuler_paiement_avec_verification, calculer_frais_total
from audit import log_action
from datetime import datetime, timedelta, date
from sqlalchemy import func
from functools import wraps


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


# ========== FONCTIONS UTILITAIRES ==========

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


def get_periode_suivante():
    """Retourne la période suivante par rapport à l'année active"""
    annees = get_annees_scolaires()
    annee_active = get_annee_active()
    
    if annee_active in annees:
        index = annees.index(annee_active)
        if index + 1 < len(annees):
            return annees[index + 1]
    
    return annees[-1] if annees else annee_active


def sauvegarder_ou_creer_tarif(groupe_id, tarif_id, montant, est_affecte, type_tarif='scolarite'):
    """Sauvegarde ou crée un tarif pour un groupe"""
    if tarif_id and tarif_id > 0:
        tarif = TarifFraisAffecte.query.get(tarif_id)
        if tarif:
            tarif.montant = montant
            tarif.date_modification = datetime.utcnow()
            return tarif
    
    # Chercher si un tarif existe déjà pour ce groupe/type/affectation
    tarif_existant = TarifFraisAffecte.query.filter_by(
        groupe_id=groupe_id,
        est_affecte=est_affecte,
        type_tarif=type_tarif
    ).first()
    
    if tarif_existant:
        tarif_existant.montant = montant
        tarif_existant.date_modification = datetime.utcnow()
        return tarif_existant
    
    # Créer un nouveau tarif
    tarif = TarifFraisAffecte(
        groupe_id=groupe_id,
        est_affecte=est_affecte,
        type_tarif=type_tarif,
        montant=montant
    )
    db.session.add(tarif)
    return tarif


def mettre_a_jour_frais_eleves_groupe(groupe_id):
    """Met à jour les frais de scolarité pour tous les élèves d'un groupe"""
    groupe = GroupeScolaire.query.get(groupe_id)
    if not groupe:
        return
    
    eleves = Eleve.query.join(SousGroupe).filter(
        SousGroupe.groupe_id == groupe_id,
        Eleve.actif == True
    ).all()
    
    for eleve in eleves:
        eleve.mettre_a_jour_frais_scolarite()
    
    db.session.commit()


def generate_matricule():
    """Génère un matricule unique au format ANNEE-XXXX"""
    import random
    import string
    
    annee = datetime.now().year
    while True:
        suffixe = ''.join(random.choices(string.digits, k=4))
        matricule = f"{annee}-{suffixe}"
        existing = Eleve.query.filter_by(matricule=matricule).first()
        if not existing:
            return matricule


def generer_numero_recu():
    """Génère un numéro de reçu unique au format REC-YYYYMMDD-XXXX"""
    today = datetime.utcnow().strftime('%Y%m%d')
    count = Paiement.query.filter(Paiement.recu.like(f'REC-{today}-%')).count()
    numero = count + 1
    return f"REC-{today}-{numero:04d}"


def get_parametres_ecole():
    """Récupère les paramètres de l'école depuis la base de données"""
    params = Parametre.query.all()
    parametres = {p.cle: p.valeur for p in params}
    
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


# ============ DASHBOARD (FONCTIONS) ============

def dashboard_admin():
    """Dashboard complet pour les administrateurs avec filtres"""
    
    if current_user.role != 'admin':
        flash('Accès réservé aux administrateurs', 'danger')
        return redirect(url_for('dashboard'))
    
    from datetime import datetime, date
    from sqlalchemy import func
    from utils import calculer_subventions_etat
    
    annee_active_globale = get_annee_active()
    subventions = calculer_subventions_etat()

    subvention_totale = subventions['total_subvention']
    subvention_eleves = subventions['eleves_affectes']
    subvention_niveaux = subventions['subvention_par_niveau']

    subvention_par_eleve = 0
    if subvention_eleves > 0:
        subvention_par_eleve = subvention_totale // subvention_eleves
    
    periode_filtre = request.args.get('periode', annee_active_globale)
    
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
    
    query_eleves = Eleve.query.filter_by(actif=True, annee_scolaire=periode_filtre)
    query_paiements = Paiement.query.filter_by(annee_scolaire=periode_filtre)
    
    if genre:
        query_eleves = query_eleves.filter(Eleve.genre == genre)
    if classe:
        query_eleves = query_eleves.filter(Eleve.classe == classe)
    if niveau:
        query_eleves = query_eleves.filter(Eleve.sous_groupe_id == int(niveau))
    if affecte:
        query_eleves = query_eleves.filter(Eleve.est_affecte_etat == (affecte == '1'))
    
    filtre_date_actif = bool(date_debut or date_fin or mois)
    if date_debut:
        query_paiements = query_paiements.filter(func.date(Paiement.date_paiement) >= date_debut)
    if date_fin:
        query_paiements = query_paiements.filter(func.date(Paiement.date_paiement) < date_fin)
    
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
    
    total_eleves = len(eleves_filtres)
    total_encaisse = sum(p.montant for p in paiements_filtres if p.montant > 0 and p.statut == 'actif')
    
    # ===== CORRECTION : Utiliser montant_paye_reel au lieu de montant_paye =====
    eleves_payes = sum(1 for e in eleves_filtres if e.montant_paye_reel >= e.frais_scolarite_total)
    eleves_partiels = sum(1 for e in eleves_filtres if 0 < e.montant_paye_reel < e.frais_scolarite_total)
    eleves_impayes = sum(1 for e in eleves_filtres if e.montant_paye_reel == 0)
    
    eleves_affectes = sum(1 for e in eleves_filtres if e.est_affecte_etat)
    eleves_non_affectes = total_eleves - eleves_affectes
    
    # ===== CORRECTION : Utiliser frais_scolarite_total et montant_paye_reel =====
    total_frais = sum(e.frais_scolarite_total for e in eleves_filtres)
    total_paye = sum(e.montant_paye_reel for e in eleves_filtres)
    
    # ===== CORRECTION : reste_a_payer doit être positif ou zéro =====
    reste_a_payer = max(0, total_frais - total_paye)
    
    # ===== CORRECTION : taux_recouvrement basé sur montant_paye_reel =====
    taux_recouvrement = round((total_paye / total_frais * 100), 1) if total_frais > 0 else 0
    # Limiter à 100% maximum
    taux_recouvrement = min(taux_recouvrement, 100.0)
    
    aujourdhui = date.today()
    paiements_jour = [p for p in paiements_filtres if p.date_paiement.date() == aujourdhui and p.montant > 0 and p.statut == 'actif']
    total_jour = sum(p.montant for p in paiements_jour)
    nb_paiements_jour = len(paiements_jour)
    
    debut_mois = aujourdhui.replace(day=1)
    paiements_mois = [p for p in paiements_filtres if p.date_paiement.date() >= debut_mois and p.montant > 0 and p.statut == 'actif']
    total_mois = sum(p.montant for p in paiements_mois)
    nb_paiements_mois = len(paiements_mois)
    
    paiements_recents = sorted([p for p in paiements_filtres if p.montant > 0 and p.statut == 'actif'], 
                                key=lambda p: p.date_paiement, reverse=True)[:10]
    
    # ===== CORRECTION : Utiliser montant_paye_reel pour le top payeurs =====
    top_payeurs = sorted([e for e in eleves_filtres if e.montant_paye_reel > 0], 
                          key=lambda e: e.montant_paye_reel, reverse=True)[:5]
    
    # ===== CORRECTION : Utiliser solde (propriété) pour le top dette =====
    top_dette = sorted([e for e in eleves_filtres if e.solde > 0], 
                        key=lambda e: e.solde, reverse=True)[:5]
    
    # ===== CORRECTION : Stats par niveau avec les bonnes propriétés =====
    stats_par_niveau = []
    for sg in SousGroupe.query.order_by(SousGroupe.ordre).all():
        eleves_niveau = [e for e in eleves_filtres if e.sous_groupe_id == sg.id]
        if eleves_niveau:
            stats_par_niveau.append({
                'nom': sg.nom,
                'total': len(eleves_niveau),
                'paye': sum(e.montant_paye_reel for e in eleves_niveau),
                'frais': sum(e.frais_scolarite_total for e in eleves_niveau)
            })
    
    derniers_eleves = Eleve.query.filter_by(annee_scolaire=periode_filtre)\
                           .order_by(Eleve.date_inscription.desc()).limit(10).all()
    
    total_utilisateurs = User.query.count()
    utilisateurs_actifs = User.query.filter_by(actif=True).count()
    
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
    
    annees_disponibles = get_annees_scolaires()
    
    return render_template('dashboard/admin.html',
                         annee_active=annee_active_globale,
                         annee_scolaire=annee_active_globale,  # ← AJOUTER pour le template
                         periode_filtre=periode_filtre,
                         annees_disponibles=annees_disponibles,
                         total_eleves=total_eleves,
                         total_encaisse=total_encaisse,
                         reste_a_payer=reste_a_payer,  # ← AJOUTER
                         taux_recouvrement=taux_recouvrement,
                         eleves_payes=eleves_payes,
                         eleves_partiels=eleves_partiels,
                         eleves_impayes=eleves_impayes,
                         subvention_totale=subvention_totale,
                         subvention_eleves=subvention_eleves,
                         subvention_niveaux=subvention_niveaux,
                         subvention_par_eleve=subvention_par_eleve,
                         eleves_affectes=eleves_affectes,
                         eleves_non_affectes=eleves_non_affectes,
                         total_jour=total_jour,
                         nb_paiements_jour=nb_paiements_jour,
                         total_mois=total_mois,
                         nb_paiements_mois=nb_paiements_mois,  # ← AJOUTER
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
    
    reste_a_recouvrer = total_frais - total_encaisse
    taux_recouvrement = (total_encaisse / total_frais * 100) if total_frais > 0 else 0
    
    eleves_payes = Eleve.query.filter(Eleve.montant_paye >= Eleve.frais_scolarite).count()
    eleves_partiels = Eleve.query.filter(Eleve.montant_paye < Eleve.frais_scolarite, Eleve.montant_paye > 0).count()
    eleves_impayes = Eleve.query.filter(Eleve.montant_paye == 0).count()
    
    aujourdhui = datetime.utcnow().date()
    paiements_jour = Paiement.query.filter(func.date(Paiement.date_paiement) == aujourdhui).order_by(Paiement.date_paiement.desc()).all()
    total_jour = sum(p.montant for p in paiements_jour)
    
    debut_semaine = aujourdhui.replace(day=aujourdhui.day - aujourdhui.weekday())
    paiements_semaine = Paiement.query.filter(func.date(Paiement.date_paiement) >= debut_semaine).all()
    total_semaine = sum(p.montant for p in paiements_semaine)
    
    debut_mois = aujourdhui.replace(day=1)
    paiements_mois = Paiement.query.filter(func.date(Paiement.date_paiement) >= debut_mois).all()
    total_mois = sum(p.montant for p in paiements_mois)
    
    paiements_recents = Paiement.query.order_by(Paiement.date_paiement.desc()).limit(20).all()
    
    stats_modes = db.session.query(
        Paiement.type_paiement,
        func.count(Paiement.id).label('nb'),
        func.sum(Paiement.montant).label('total')
    ).group_by(Paiement.type_paiement).all()
    
    eleves_avec_solde = Eleve.query.filter(Eleve.solde > 0).order_by(Eleve.solde.desc()).limit(20).all()
    
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
    
    total_eleves = Eleve.query.count()
    
    aujourdhui = datetime.utcnow().date()
    paiements_jour = Paiement.query.filter(func.date(Paiement.date_paiement) == aujourdhui).order_by(Paiement.date_paiement.desc()).limit(10).all()
    total_jour = sum(p.montant for p in paiements_jour)
    nb_paiements_jour = len(paiements_jour)
    
    derniers_paiements = Paiement.query.order_by(Paiement.date_paiement.desc()).limit(10).all()
    derniers_eleves = Eleve.query.order_by(Eleve.date_inscription.desc()).limit(10).all()
    
    return render_template('dashboard/operateur.html',
                         total_eleves=total_eleves,
                         total_jour=total_jour,
                         nb_paiements_jour=nb_paiements_jour,
                         paiements_jour=paiements_jour,
                         derniers_paiements=derniers_paiements,
                         derniers_eleves=derniers_eleves)


# ============ ROUTES PRINCIPALES ============

@app.route('/')
@app.route('/dashboard')
@login_required
def dashboard():
    """Dashboard - Vue adaptée selon le rôle de l'utilisateur"""
    role = current_user.role
    
    if role == 'admin':
        return dashboard_admin()
    elif role == 'comptable':
        return dashboard_comptable()
    else:
        return dashboard_operateur()


# ============ AUTHENTIFICATION ============

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
        return redirect(url_for('dashboard'))
    else:
        return redirect(url_for('liste_eleves'))


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


# ============ CHANGEMENT DE DOSSIER ============

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
        session['groupe_dossier'] = 'Secondaire'
        flash('🎓 Dossier Secondaire activé', 'success')
    else:
        flash('Dossier invalide', 'danger')
    
    return redirect(url_for('liste_eleves'))


# ============ PROFIL UTILISATEUR ============

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
                
                db.session.commit()
                
                if nouveau_password:
                    flash('Profil et mot de passe mis à jour avec succès !', 'success')
                else:
                    flash('Profil mis à jour avec succès !', 'success')
                
                return redirect(url_for('profil'))
                
            except Exception as e:
                db.session.rollback()
                flash(f'Erreur lors de la mise à jour: {str(e)}', 'danger')
    
    total_eleves_inscrits = Eleve.query.count()
    total_paiements_effectues = Paiement.query.count()
    eleves_avec_solde = sum(1 for e in Eleve.query.all() if e.solde > 0)
    
    return render_template('utilisateurs/profil.html', 
                         user=current_user,
                         total_eleves_inscrits=total_eleves_inscrits,
                         total_paiements_effectues=total_paiements_effectues,
                         eleves_avec_solde=eleves_avec_solde)


# ============ GESTION DES UTILISATEURS ============

@app.route('/utilisateurs')
@login_required
@admin_required
def liste_utilisateurs():
    """Affiche la liste de tous les utilisateurs"""
    utilisateurs = User.query.order_by(User.created_at.desc()).all()
    
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
        if User.query.filter_by(username=username).first():
            errors.append("Ce nom d'utilisateur est déjà pris")
        
        if errors:
            for error in errors:
                flash(error, 'danger')
            return render_template('utilisateurs/ajouter.html', form_data=request.form)
        
        try:
            nouvel_utilisateur = User(
                username=username, nom=nom, prenom=prenom,
                role=role, actif=actif
            )
            nouvel_utilisateur.set_password(password)
            db.session.add(nouvel_utilisateur)
            db.session.commit()
            flash(f'Utilisateur {username} créé avec succès', 'success')
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
    utilisateurs_admin_count = User.query.filter_by(role='admin', actif=True).count()
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        nom = request.form.get('nom', '').strip()
        prenom = request.form.get('prenom', '').strip()
        role = request.form.get('role', 'user')
        actif = 'actif' in request.form
        nouveau_password = request.form.get('nouveau_password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        errors = []
        
        if not username or len(username) < 3:
            errors.append("Le nom d'utilisateur doit contenir au moins 3 caractères")
        if not nom or len(nom) < 2:
            errors.append("Le nom doit contenir au moins 2 caractères")
        if not prenom or len(prenom) < 2:
            errors.append("Le prénom doit contenir au moins 2 caractères")
        
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
                                 utilisateurs_admin_count=utilisateurs_admin_count)
        
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
                         utilisateurs_admin_count=utilisateurs_admin_count)


@app.route('/utilisateurs/<int:user_id>/supprimer', methods=['POST'])
@login_required
@admin_required
def supprimer_utilisateur(user_id):
    """Supprime un utilisateur"""
    utilisateur = User.query.get_or_404(user_id)
    
    if utilisateur.id == current_user.id:
        flash('Vous ne pouvez pas supprimer votre propre compte', 'danger')
        return redirect(url_for('liste_utilisateurs'))
    
    if utilisateur.role == 'admin' and User.query.filter_by(role='admin').count() <= 1:
        flash('Impossible de supprimer le dernier administrateur', 'danger')
        return redirect(url_for('liste_utilisateurs'))
    
    try:
        username = utilisateur.username
        db.session.delete(utilisateur)
        db.session.commit()
        flash(f'Utilisateur {username} supprimé avec succès', 'success')
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
    
    if utilisateur.id == current_user.id:
        flash('Vous ne pouvez pas modifier votre propre statut', 'danger')
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


# ============ PAIEMENTS ============

@app.route('/eleve/<int:id>/paiements')
@login_required
def voir_paiements(id):
    """Affiche l'historique des paiements d'un élève"""
    eleve = Eleve.query.get_or_404(id)
    paiements = Paiement.query.filter_by(eleve_id=id)\
                              .order_by(Paiement.date_paiement.desc()).all()
    
    return render_template('paiements_eleve.html', eleve=eleve, paiements=paiements)


@app.route('/paiement/ajouter/<int:eleve_id>', methods=['POST'])
@login_required
def ajouter_paiement(eleve_id):
    """Ajoute un paiement pour un élève (AJAX)"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    eleve = Eleve.query.get(eleve_id)
    if not eleve:
        return jsonify({'success': False, 'message': 'Élève non trouvé'}), 404
    
    try:
        montant = float(request.form.get('montant', 0))
        
        if montant <= 0:
            return jsonify({'success': False, 'message': 'Le montant doit être supérieur à 0 FCFA'}), 400
        
        if montant > eleve.solde:
            return jsonify({'success': False, 'message': f'Le montant dépasse le solde ({eleve.solde:,.0f} FCFA)'}), 400
        
        type_paiement = request.form.get('type_paiement', 'especes')
        reference = request.form.get('reference', '')
        description = request.form.get('description', '')
        num_recu = generer_numero_recu()
        
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
        
        eleve.montant_paye += montant
        
        db.session.add(paiement)
        db.session.commit()
        
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
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Erreur: {str(e)}'}), 500


@app.route('/paiement/<int:id>/annuler', methods=['GET', 'POST'])
@login_required
def annuler_paiement(id):
    """Annule un paiement - compatible AJAX et formulaire classique"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    paiement = Paiement.query.get_or_404(id)
    eleve = paiement.eleve
    
    # Vérifications
    if paiement.statut == 'annule':
        return jsonify({'success': False, 'message': 'Ce paiement est déjà annulé'}), 400
    
    if paiement.est_verrouille_par_depot():
        return jsonify({'success': False, 'message': 'Ce paiement est dans un dépôt validé'}), 400
    
    # Si c'est une requête AJAX (POST)
    if request.method == 'POST':
        try:
            raison = request.form.get('raison', '')
            confirmation = request.form.get('confirmation', '')
            
            if confirmation != 'CONFIRMER':
                return jsonify({'success': False, 'message': 'Tapez CONFIRMER pour valider'}), 400
            
            # 1. Annuler le paiement
            paiement.statut = 'annule'
            paiement.annule_le = datetime.utcnow()
            paiement.annule_par = current_user.username
            paiement.motif_annulation = raison
            
            # 2. Mettre à jour montant_paye legacy
            if paiement.montant > 0:
                eleve.montant_paye = max(0, eleve.montant_paye - paiement.montant)
            
            # 3. Créer l'avoir
            avoir = Paiement(
                eleve_id=eleve.id,
                montant=-abs(paiement.montant),
                type_paiement=paiement.type_paiement,
                reference=f'AVOIR-{paiement.recu}',
                description=f'Annulation paiement {paiement.recu}: {raison}',
                recu=f'AV-{datetime.utcnow().strftime("%Y%m%d%H%M%S")}',
                date_paiement=datetime.utcnow(),
                encaisse_par=current_user.username,
                statut='actif',
                annee_scolaire=paiement.annee_scolaire,
                categorie_frais=paiement.categorie_frais,
                details={}
            )
            
            # Copier les détails en négatif
            if paiement.details:
                avoir_details = {}
                for rubrique, montant in paiement.details.items():
                    if montant and float(montant) > 0:
                        avoir_details[rubrique] = -float(montant)
                avoir.details = avoir_details
            
            db.session.add(avoir)
            db.session.commit()
            
            return jsonify({
                'success': True,
                'message': f'Paiement {paiement.recu} annulé avec succès',
                'eleve_id': eleve.id
            })
            
        except Exception as e:
            db.session.rollback()
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'message': f'Erreur: {str(e)}'}), 500
    
    # Si GET (accès direct à la page)
    return render_template('paiements/annuler_paiement.html',
                         paiement=paiement, eleve=eleve, montant=paiement.montant)


@app.route('/api/paiement/<int:paiement_id>')
@login_required
def api_paiement_details(paiement_id):
    """API pour obtenir les détails d'un paiement"""
    paiement = Paiement.query.get_or_404(paiement_id)
    
    return jsonify({
        'success': True,
        'paiement': {
            'id': paiement.id,
            'eleve_id': paiement.eleve_id,
            'recu': paiement.recu,
            'montant': float(paiement.montant),
            'date_paiement': paiement.date_paiement.strftime('%Y-%m-%d') if paiement.date_paiement else None,
            'type_paiement': paiement.type_paiement,
            'mode_paiement': paiement.type_paiement,
            'reference': paiement.reference,
            'description': paiement.description,
            'statut': paiement.statut,
            'categorie_frais': paiement.categorie_frais,
            'details': paiement.details if paiement.details else {}
        }
    })


@app.route('/utilisateurs/<int:user_id>/reinitialiser-mot-de-passe', methods=['GET', 'POST'])
@login_required
def reinitialiser_mot_de_passe(user_id):
    """Réinitialise le mot de passe d'un utilisateur"""
    if current_user.role != 'admin':
        flash('Accès réservé aux administrateurs', 'danger')
        return redirect(url_for('liste_utilisateurs'))
    
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        nouveau_mdp = request.form.get('nouveau_mot_de_passe', 'changemoi123')
        user.set_password(nouveau_mdp)
        db.session.commit()
        flash(f'Mot de passe de {user.nom_complet} réinitialisé avec succès !', 'success')
        return redirect(url_for('liste_utilisateurs'))
    
    # Si GET, rediriger ou faire la réinitialisation directe
    # Option 1 : Réinitialiser directement
    nouveau_mdp = 'changemoi123'
    user.set_password(nouveau_mdp)
    db.session.commit()
    flash(f'Mot de passe de {user.nom_complet} réinitialisé avec succès !', 'success')
    return redirect(url_for('liste_utilisateurs'))


@app.route('/paiement/<int:paiement_id>/recu')
@login_required
def imprimer_recu_unique(paiement_id):
    """Affiche un reçu de paiement"""
    paiement = Paiement.query.get_or_404(paiement_id)
    eleve = paiement.eleve
    parametres_ecole = get_parametres_ecole()
    
    return render_template('recus/recu_unique.html',
                         paiement=paiement,
                         eleve=eleve,
                         parametres_ecole=parametres_ecole,
                         date_edition=datetime.now())


@app.route('/eleve/<int:id>/recus')
@login_required
def imprimer_tous_recus(id):
    """Affiche tous les reçus d'un élève"""
    eleve = Eleve.query.get_or_404(id)
    paiements = Paiement.query.filter_by(eleve_id=id)\
                              .order_by(Paiement.date_paiement.desc()).all()
    parametres_ecole = get_parametres_ecole()
    
    return render_template('recus/recus_tous.html',
                         eleve=eleve,
                         paiements=paiements,
                         parametres_ecole=parametres_ecole,
                         date_edition=datetime.now())


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
    
    return render_template('paiements_recherche.html', paiements=paiements, query=query)


# ============ RAPPORTS ============

@app.route('/rapports')
@login_required
def rapports():
    """Page des rapports financiers"""
    if current_user.role not in ['admin', 'comptable']:
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('liste_eleves'))
    
    maintenant = datetime.utcnow()
    debut_mois = maintenant.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    if maintenant.month == 12:
        fin_mois = maintenant.replace(year=maintenant.year + 1, month=1, day=1) - timedelta(seconds=1)
    else:
        fin_mois = maintenant.replace(month=maintenant.month + 1, day=1) - timedelta(seconds=1)
    
    paiements_mois = Paiement.query.filter(
        Paiement.date_paiement >= debut_mois,
        Paiement.date_paiement <= fin_mois
    ).all()
    total_mois = sum(p.montant for p in paiements_mois)
    nb_paiements_mois = len(paiements_mois)
    
    total_eleves = Eleve.query.count()
    total_encaisse = db.session.query(func.sum(Paiement.montant)).scalar() or 0
    total_frais_attendus = db.session.query(func.sum(Eleve.frais_scolarite)).scalar() or 0
    
    eleves_payes = Eleve.query.filter(Eleve.montant_paye >= Eleve.frais_scolarite).count()
    eleves_partiels = Eleve.query.filter(Eleve.montant_paye < Eleve.frais_scolarite, Eleve.montant_paye > 0).count()
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
                         stats=stats,
                         debut_mois=debut_mois,
                         fin_mois=fin_mois)


# ============ GESTION BANCAIRE ============

@app.route('/bank')
@login_required
def bank():
    """Page principale des dépôts bancaires"""
    if current_user.role not in ['admin', 'comptable']:
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('liste_eleves'))
    
    periode_active = get_annee_active()
    depots = DepotBancaire.query.filter_by(annee_scolaire=periode_active)\
                                .order_by(DepotBancaire.date_depot.desc()).all()
    
    # Exclure les avoirs (montants négatifs) et les paiements annulés
    paiements_non_deposes = Paiement.query.filter(
        ~Paiement.depots_lies.any(),
        Paiement.statut == 'actif',
        Paiement.montant > 0  # ← Exclure les montants négatifs (avoirs)
    ).order_by(Paiement.date_paiement.desc()).all()
    
    return render_template('bank.html',
                         depots=depots,
                         paiements_non_deposes=paiements_non_deposes,
                         info_periode={'annee': periode_active})


@app.route('/bank/generer_depot', methods=['POST'])
@login_required
def generer_depot():
    """Génère un nouveau dépôt bancaire"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    try:
        # Récupérer les IDs des paiements (peut venir de FormData ou JSON)
        if request.is_json:
            data = request.get_json()
            paiement_ids = data.get('paiement_ids', [])
        else:
            paiement_ids = request.form.getlist('paiements_ids')
        
        if not paiement_ids:
            return jsonify({'success': False, 'message': 'Aucun paiement sélectionné'}), 400
        
        # Convertir en entiers
        paiement_ids = [int(pid) for pid in paiement_ids]
        
        # Récupérer la banque et les observations
        banque = request.form.get('banque', 'Banque')
        observations = request.form.get('observations', '')
        
        # Récupérer les paiements
        paiements = Paiement.query.filter(Paiement.id.in_(paiement_ids)).all()
        
        if not paiements:
            return jsonify({'success': False, 'message': 'Aucun paiement trouvé'}), 404
        
        # Vérifier que les paiements ne sont pas déjà déposés
        for p in paiements:
            if p.depots_lies:
                return jsonify({
                    'success': False, 
                    'message': f'Le paiement {p.recu} est déjà dans un dépôt'
                }), 400
        
        # Calculer le montant total
        montant_total = sum(p.montant for p in paiements if p.montant > 0 and p.statut == 'actif')
        
        if montant_total <= 0:
            return jsonify({'success': False, 'message': 'Le montant total doit être supérieur à 0'}), 400
        
        # Générer un numéro de dépôt unique
        date_str = datetime.now().strftime('%Y%m%d')
        count = DepotBancaire.query.filter(
            DepotBancaire.numero_depot.like(f'DEP-{date_str}-%')
        ).count()
        numero_depot = f'DEP-{date_str}-{count+1:03d}'
        
        # Créer le dépôt
        annee_active = get_annee_active()
        depot = DepotBancaire(
            numero_depot=numero_depot,
            montant_total=montant_total,
            date_depot=datetime.now(),
            statut='en_attente',
            effectue_par=current_user.username,
            banque=banque,
            observations=observations,
            annee_scolaire=annee_active
        )
        
        db.session.add(depot)
        db.session.flush()
        
        # Lier les paiements au dépôt
        for p in paiements:
            if p.montant > 0 and p.statut == 'actif':
                liaison = PaiementDepot(
                    paiement_id=p.id,
                    depot_id=depot.id,
                    date_liaison=datetime.now()
                )
                db.session.add(liaison)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Dépôt {numero_depot} créé avec {len(paiements)} paiement(s) pour un montant de {montant_total:,.0f} FCFA',
            'depot': {
                'id': depot.id,
                'numero': depot.numero_depot,
                'montant': float(depot.montant_total),
                'date': depot.date_depot.strftime('%d/%m/%Y %H:%M')
            }
        })
        
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Erreur: {str(e)}'}), 500
    

@app.route('/api/bank/depot/<int:depot_id>/details')
@login_required
def api_details_depot(depot_id):
    """API pour les détails d'un dépôt"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    depot = DepotBancaire.query.get_or_404(depot_id)
    
    return jsonify({
        'success': True,
        'depot': {
            'numero_depot': depot.numero_depot,
            'date_depot': depot.date_depot.strftime('%d/%m/%Y %H:%M') if depot.date_depot else '-',
            'montant_total': float(depot.montant_total),
            'statut': depot.statut,
            'banque': depot.banque or 'Non spécifiée',
            'reference_banque': depot.reference_banque or '',
            'effectue_par': depot.effectue_par or '-',
            'observations': depot.observations or '',
            'date_validation': depot.date_validation.strftime('%d/%m/%Y %H:%M') if depot.date_validation else None,
            'nombre_paiements': len(depot.paiements)
        },
        'paiements': [{
            'recu': p.paiement.recu,
            'date_paiement': p.paiement.date_paiement.strftime('%d/%m/%Y') if p.paiement.date_paiement else '-',
            'montant': float(p.paiement.montant),
            'type_paiement': p.paiement.type_paiement or '-',
            'eleve': {
                'prenom': p.paiement.eleve.prenom,
                'nom': p.paiement.eleve.nom,
                'classe': p.paiement.eleve.classe,
                'sous_groupe': p.paiement.eleve.sous_groupe.nom if p.paiement.eleve.sous_groupe else '-'
            }
        } for p in depot.paiements]
    })


@app.route('/bank/depot/<int:depot_id>/valider', methods=['POST'])
@login_required
def valider_depot(depot_id):
    """Valide un dépôt bancaire"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    depot = DepotBancaire.query.get_or_404(depot_id)
    
    if depot.statut != 'en_attente':
        return jsonify({'success': False, 'message': f'Le dépôt est déjà {depot.statut}'}), 400
    
    try:
        reference_banque = request.form.get('reference_banque', '')
        
        depot.statut = 'valide'
        depot.date_validation = datetime.now()
        if reference_banque:
            depot.reference_banque = reference_banque
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Dépôt {depot.numero_depot} validé avec succès'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Erreur: {str(e)}'}), 500


@app.route('/bank/depot/<int:depot_id>/annuler', methods=['POST'])
@login_required
def annuler_depot(depot_id):
    """Annule un dépôt bancaire"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    depot = DepotBancaire.query.get_or_404(depot_id)
    
    if depot.statut == 'valide':
        return jsonify({'success': False, 'message': 'Impossible d\'annuler un dépôt déjà validé'}), 400
    
    try:
        depot.statut = 'annule'
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Dépôt {depot.numero_depot} annulé'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Erreur: {str(e)}'}), 500


# ============ API STATS ============

@app.route('/api/stats-journalieres')
@login_required
def api_stats_journalieres():
    """API pour les statistiques journalières filtrées par dossier"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False}), 403
    
    aujourdhui = date.today()
    
    # Récupérer le dossier depuis les paramètres
    dossier = request.args.get('dossier', 'tous')
    
    # Déterminer les IDs de groupes selon le dossier
    groupes_ids = []
    if dossier == 'maternelle':
        groupes_ids = [1]
    elif dossier == 'primaire':
        groupes_ids = [2]
    elif dossier == 'secondaire':
        groupes_ids = [3, 4]
    
    # Requête de base pour les élèves
    base_query = Eleve.query.filter_by(actif=True)
    annee_active = get_annee_active()
    base_query = base_query.filter(Eleve.annee_scolaire == annee_active)
    
    # Filtrer par dossier si nécessaire
    if groupes_ids:
        sous_groupes = SousGroupe.query.filter(SousGroupe.groupe_id.in_(groupes_ids)).all()
        sous_groupes_ids = [sg.id for sg in sous_groupes]
        if sous_groupes_ids:
            base_query = base_query.filter(Eleve.sous_groupe_id.in_(sous_groupes_ids))
        else:
            # Aucun sous-groupe pour ce dossier
            return jsonify({
                'success': True,
                'date': aujourdhui.strftime('%d/%m/%Y'),
                'eleves_inscrits': 0,
                'montant_total_paye': 0,
                'nombre_paiements': 0
            })
    
    # Élèves inscrits aujourd'hui
    eleves_inscrits = base_query.filter(func.date(Eleve.date_inscription) == aujourdhui).count()
    
    # Paiements du jour
    # D'abord récupérer les IDs des élèves du dossier
    eleves_ids = [e.id for e in base_query.all()]
    
    # Filtrer les paiements par ces élèves
    if eleves_ids:
        tous_paiements = Paiement.query.filter(
            func.date(Paiement.date_paiement) == aujourdhui,
            Paiement.eleve_id.in_(eleves_ids)
        ).all()
    else:
        tous_paiements = []
    
    paiements_valides = [p for p in tous_paiements if p.montant > 0 and p.statut == 'actif']
    
    return jsonify({
        'success': True,
        'date': aujourdhui.strftime('%d/%m/%Y'),
        'eleves_inscrits': eleves_inscrits,
        'montant_total_paye': sum(p.montant for p in paiements_valides),
        'nombre_paiements': len(paiements_valides)
    })

@app.route('/api/eleve/<int:eleve_id>/paiements')
@login_required
def api_eleve_paiements(eleve_id):
    """Récupère tous les paiements d'un élève avec détails par rubrique"""
    eleve = Eleve.query.get_or_404(eleve_id)
    annee_active = get_annee_active()
    paiements = Paiement.query.filter_by(eleve_id=eleve_id, annee_scolaire=annee_active)\
                              .order_by(Paiement.date_paiement.desc()).all()
    
    # Calculer les montants payés par rubrique
    montant_paye_inscription = 0
    montant_paye_tenue = 0
    montant_paye_examen = 0
    montant_paye_scolarite = 0
    montant_paye_transport = 0
    montant_paye_cantine = 0
    montant_paye_renforcement = 0
    
    for p in paiements:
        if p.statut == 'actif' and p.montant > 0:
            # Si le paiement a des détails (nouveau système)
            if hasattr(p, 'details') and p.details:
                details = p.details
                montant_paye_inscription += float(details.get('inscription', 0) or 0)
                montant_paye_tenue += float(details.get('tenue', 0) or 0)
                montant_paye_examen += float(details.get('examen', 0) or 0)
                montant_paye_scolarite += float(details.get('scolarite', 0) or 0)
                montant_paye_transport += float(details.get('transport', 0) or 0)
                montant_paye_cantine += float(details.get('cantine', 0) or 0)
                montant_paye_renforcement += float(details.get('renforcement', 0) or 0)
            else:
                # Ancien système : répartir selon la catégorie
                if p.categorie_frais == 'inscription':
                    montant_paye_inscription += p.montant
                elif p.categorie_frais == 'tenue':
                    montant_paye_tenue += p.montant
                elif p.categorie_frais == 'examen':
                    montant_paye_examen += p.montant
                elif p.categorie_frais == 'scolarite':
                    montant_paye_scolarite += p.montant
                elif p.categorie_frais == 'transport':
                    montant_paye_transport += p.montant
                elif p.categorie_frais == 'cantine':
                    montant_paye_cantine += p.montant
                elif p.categorie_frais == 'renforcement':
                    montant_paye_renforcement += p.montant
                else:
                    # Par défaut, mettre en scolarité
                    montant_paye_scolarite += p.montant
    
    montant_paye_reel = (montant_paye_inscription + montant_paye_tenue + 
                         montant_paye_examen + montant_paye_scolarite + 
                         montant_paye_transport + montant_paye_cantine + 
                         montant_paye_renforcement)
    
    return jsonify({
        'success': True,
        'eleve': {
            'id': eleve.id,
            'nom': eleve.nom,
            'prenom': eleve.prenom,
            'matricule': eleve.matricule,
            'classe': eleve.classe,
            'frais_scolarite_total': eleve.frais_scolarite_total,
            'frais_inscription_montant': eleve.frais_inscription_montant or 0,
            'frais_tenue_montant': eleve.frais_tenue_montant or 0,
            'frais_droit_examen_montant': eleve.frais_droit_examen_montant or 0,
            'frais_scolarite_base': eleve.frais_scolarite_base or 0,
            'frais_transport': eleve.frais_transport or 0,
            'frais_cantine': eleve.frais_cantine or 0,
            'frais_renforcement': eleve.frais_renforcement or 0,
            # Montants payés par rubrique
            'montant_paye_inscription': montant_paye_inscription,
            'montant_paye_tenue': montant_paye_tenue,
            'montant_paye_examen': montant_paye_examen,
            'montant_paye_scolarite': montant_paye_scolarite,
            'montant_paye_transport': montant_paye_transport,
            'montant_paye_cantine': montant_paye_cantine,
            'montant_paye_renforcement': montant_paye_renforcement,
            # Totaux
            'montant_paye': eleve.montant_paye,
            'montant_paye_reel': montant_paye_reel,
            'solde': eleve.frais_scolarite_total - montant_paye_reel,
            'taux_paiement': round((montant_paye_reel / eleve.frais_scolarite_total * 100) if eleve.frais_scolarite_total > 0 else 0, 1),
            # Options
            'est_classe_examen': eleve.est_classe_examen if hasattr(eleve, 'est_classe_examen') else False,
            'transport_option': bool(eleve.transport_option),
            'cantine_option': bool(eleve.cantine_option),
            'renforcement_inscrit': bool(eleve.renforcement_inscrit),
        },
        'paiements': [{
            'id': p.id,
            'montant': float(p.montant),
            'date_paiement': p.date_paiement.strftime('%Y-%m-%d') if p.date_paiement else None,
            'type_paiement': p.type_paiement,
            'mode_paiement': p.type_paiement,  # Pour compatibilité
            'recu': p.recu,
            'statut': p.statut,
            'reference': p.reference if hasattr(p, 'reference') else None,
            'description': p.description if hasattr(p, 'description') else None,
            'categorie_frais': p.categorie_frais if hasattr(p, 'categorie_frais') else 'scolarite',
            'details': p.details if hasattr(p, 'details') else None,
        } for p in paiements]
    })

# ============ TRANSPORTS ET CANTINES ============

@app.route('/parametres/transports/ajouter', methods=['POST'])
@login_required
def param_ajouter_transport():
    """Ajoute une option de transport"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    try:
        transport = OptionTransport(
            nom=request.form.get('nom'),
            code=request.form.get('code', request.form.get('nom').lower().replace(' ', '_')),
            montant_supplement=float(request.form.get('montant_supplement', 0)),
            ordre=int(request.form.get('ordre', 0)),
            description=request.form.get('description', '')
        )
        db.session.add(transport)
        db.session.commit()
        flash(f'Option de transport {transport.nom} ajoutée avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur : {str(e)}', 'danger')
    
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
        flash(f'Transport {transport.nom} modifié avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/cantines/ajouter', methods=['POST'])
@login_required
def param_ajouter_cantine():
    """Ajoute une option de cantine"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    try:
        cantine = OptionCantine(
            nom=request.form.get('nom'),
            code=request.form.get('code', request.form.get('nom').lower().replace(' ', '_')),
            montant=float(request.form.get('montant', 0)),
            ordre=int(request.form.get('ordre', 0)),
            description=request.form.get('description', '')
        )
        db.session.add(cantine)
        db.session.commit()
        flash(f'Option de cantine {cantine.nom} ajoutée avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur : {str(e)}', 'danger')
    
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
        flash(f'Cantine {cantine.nom} modifiée avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


# ============ API SUBVENTIONS ============

@app.route('/api/subventions-etat')
@login_required
def api_subventions_etat():
    """API pour récupérer les statistiques des subventions État"""
    from utils import calculer_subventions_etat
    
    stats = calculer_subventions_etat()
    stats['total_subvention_formate'] = "{:,.0f}".format(stats['total_subvention'])
    
    return jsonify({'success': True, 'data': stats})


@app.route('/reinscriptions/anciens')
@login_required
def reinscrire_anciens():
    """Page pour réinscrire les anciens élèves"""
    if current_user.role not in ['admin', 'comptable']:
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('liste_eleves'))
    
    annee_active = get_annee_active()
    annees_disponibles = get_annees_scolaires()
    
    anciens = Eleve.query.filter(Eleve.actif == True, Eleve.reinscrit == False)\
                  .order_by(Eleve.nom, Eleve.prenom).all()
    
    sous_groupes = SousGroupe.query.order_by(SousGroupe.groupe_id, SousGroupe.ordre).all()
    periode_suivante = get_periode_suivante()
    
    return render_template('reinscriptions/anciens.html',
                         eleves=anciens,
                         annee_active=annee_active,
                         annees_disponibles=annees_disponibles,
                         periode_suivante=periode_suivante,
                         sous_groupes=sous_groupes)


@app.route('/reinscriptions/inscrire/<int:eleve_id>', methods=['POST'])
@login_required
def reinscrire_eleve(eleve_id):
    """Réinscrire un élève"""
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    eleve = Eleve.query.get_or_404(eleve_id)
    annee_choisie = request.form.get('annee_scolaire', get_annee_active())
    
    if eleve.annee_scolaire == annee_choisie:
        return jsonify({'success': False, 'message': f'{eleve.nom_complet} est déjà dans l\'année {annee_choisie}'}), 400
    
    if eleve.solde > 0:
        return jsonify({'success': False, 'message': f'Solde impayé de {eleve.solde:,.0f} FCFA'}), 400
    
    try:
        ancienne_annee = eleve.annee_scolaire
        ancienne_classe = eleve.classe
        ancien_statut = eleve.statut_paiement
        
        nouvelle_classe = request.form.get('nouvelle_classe', eleve.classe)
        nouveau_sous_groupe_id = request.form.get('nouveau_sous_groupe_id') or eleve.sous_groupe_id
        
        nouveaux_frais = calculer_frais_total(
            sous_groupe_id=int(nouveau_sous_groupe_id) if nouveau_sous_groupe_id else eleve.sous_groupe_id,
            est_affecte=eleve.est_affecte_etat,
            transport_option_id=eleve.transport_option_id,
            cantine_option_id=eleve.cantine_option_id,
            renforcement_inscrit=eleve.renforcement_inscrit,
            classe=nouvelle_classe
        )
        
        eleve.annee_scolaire = annee_choisie
        eleve.classe = nouvelle_classe
        if nouveau_sous_groupe_id:
            eleve.sous_groupe_id = int(nouveau_sous_groupe_id)
        eleve.frais_scolarite = nouveaux_frais
        eleve.montant_paye = 0
        eleve.reinscrit = True
        eleve.date_reinscription = datetime.utcnow()
        eleve.reinscrit_par = current_user.username
        
        db.session.commit()
        db.session.refresh(eleve)
        
        log_action('REINSCRIRE_ELEVE', f"{eleve.nom_complet}: {ancienne_classe} {ancienne_annee} → {nouvelle_classe} {annee_choisie}")
        
        return jsonify({
            'success': True,
            'message': f'{eleve.nom_complet} réinscrit pour {annee_choisie} en {nouvelle_classe}',
            'details': {
                'matricule': eleve.matricule,
                'annee_scolaire': annee_choisie,
                'classe': nouvelle_classe,
                'frais_scolarite': nouveaux_frais,
                'solde': eleve.solde,
                'statut_paiement': eleve.statut_paiement
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
        eleve.reinscrit = False
        eleve.date_reinscription = None
        eleve.reinscrit_par = None
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'Réinscription de {eleve.nom_complet} annulée'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


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
    
    message = f'{count} élèves réinscrits pour {annee_active}.'
    if eleves_bloques:
        message += f' {len(eleves_bloques)} bloqué(s) pour solde impayé.'
    
    return jsonify({'success': True, 'message': message})


# ============ PARAMÈTRES ============

@app.route('/parametres')
@login_required
def parametres():
    """Page des paramètres généraux"""
    if current_user.role != 'admin':
        flash('Accès réservé aux administrateurs', 'danger')
        return redirect(url_for('dashboard'))
    
    parametres = Parametre.query.all()
    parametres_dict = {p.cle: p.valeur for p in parametres}
    
    groupes = GroupeScolaire.query.order_by(GroupeScolaire.ordre).all()
    groupes_scolaires = GroupeScolaire.query.filter_by(actif=True).order_by(GroupeScolaire.ordre).all()
    sous_groupes = SousGroupe.query.order_by(SousGroupe.groupe_id, SousGroupe.ordre).all()
    types_frais = TypeFrais.query.order_by(TypeFrais.ordre).all()
    transports = OptionTransport.query.order_by(OptionTransport.ordre).all()
    cantines = OptionCantine.query.order_by(OptionCantine.ordre).all()
    tarifs = TarifFrais.query.order_by(TarifFrais.sous_groupe_id, TarifFrais.type_frais_id).all()
    tarifs_affectes = TarifFraisAffecte.query.order_by(TarifFraisAffecte.sous_groupe_id).all()
    
    return render_template('parametres.html',
                         parametres=parametres_dict,
                         groupes=groupes,
                         groupes_scolaires=groupes_scolaires,
                         sous_groupes=sous_groupes,
                         types_frais=types_frais,
                         transports=transports,
                         cantines=cantines,
                         tarifs=tarifs,
                         tarifs_affectes=tarifs_affectes)


@app.route('/parametres/sauvegarder-tout', methods=['POST'])
@login_required
def sauvegarder_tous_parametres():
    """Sauvegarde tous les paramètres"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    try:
        params_generaux = {
            'nom_ecole': request.form.get('nom_ecole', ''),
            'devise': request.form.get('devise', 'FCFA'),
            'annee_scolaire': request.form.get('annee_scolaire', '2025-2026'),
            'frais_inscription': request.form.get('frais_inscription', '0'),
            'delai_paiement': request.form.get('delai_paiement', '30'),
            'penalite_retard': request.form.get('penalite_retard', '0'),
            'email_notification': request.form.get('email_notification', ''),
            'telephone_ecole': request.form.get('telephone_ecole', ''),
            'adresse_ecole': request.form.get('adresse_ecole', ''),
        }
        for cle, valeur in params_generaux.items():
            Parametre.set(cle, valeur)
        
        params_periodes = {
            'annee_scolaire_active': request.form.get('annee_scolaire_active', ''),
            'annees_scolaires': request.form.get('annees_scolaires', ''),
            'frais_reinscription': request.form.get('frais_reinscription', '0'),
        }
        for cle, valeur in params_periodes.items():
            if valeur:
                Parametre.set(cle, valeur)
        
        params_tenues = {
            'montant_tenue_maternelle': request.form.get('montant_tenue_maternelle', '15000'),
            'montant_tenue_primaire': request.form.get('montant_tenue_primaire', '15000'),
            'montant_tenue_secondaire': request.form.get('montant_tenue_secondaire', '20000'),
        }
        for cle, valeur in params_tenues.items():
            Parametre.set(cle, valeur)
        
        params_examens = {
            'droit_examen_cm2_ministere': request.form.get('droit_examen_cm2_ministere', '5000'),
            'droit_examen_cm2_ecole': request.form.get('droit_examen_cm2_ecole', '3000'),
            'droit_examen_3eme_ministere': request.form.get('droit_examen_3eme_ministere', '8000'),
            'droit_examen_3eme_ecole': request.form.get('droit_examen_3eme_ecole', '5000'),
            'droit_examen_tle_ministere': request.form.get('droit_examen_tle_ministere', '10000'),
            'droit_examen_tle_ecole': request.form.get('droit_examen_tle_ecole', '7000'),
        }
        for cle, valeur in params_examens.items():
            Parametre.set(cle, valeur)
        
        db.session.commit()
        flash('✅ Paramètres sauvegardés avec succès !', 'success')
        log_action('PARAMETRES', 'Mise à jour des paramètres')
        
    except Exception as e:
        db.session.rollback()
        flash(f'❌ Erreur : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/periodes', methods=['POST'])
@login_required
def sauvegarder_parametres_periodes():
    """Sauvegarde les paramètres de périodes"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    try:
        params = {
            'annee_scolaire_active': request.form.get('annee_scolaire_active', ''),
            'annees_scolaires': request.form.get('annees_scolaires', ''),
            'periode_debut': request.form.get('periode_debut', ''),
            'periode_fin': request.form.get('periode_fin', ''),
            'frais_reinscription': request.form.get('frais_reinscription', '0'),
        }
        for cle, valeur in params.items():
            param = Parametre.query.filter_by(cle=cle).first()
            if param:
                param.valeur = valeur
            else:
                db.session.add(Parametre(cle=cle, valeur=valeur))
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
    
    param = Parametre.query.filter_by(cle='annee_scolaire_active').first()
    if param:
        param.valeur = annee
        db.session.commit()
        flash(f'✅ Période {annee} activée !', 'success')
    
    return redirect(url_for('parametres'))


@app.route('/periodes/desactiver')
@login_required
@admin_required
def desactiver_periode():
    """Désactive la période active"""
    param = Parametre.query.filter_by(cle='annee_scolaire_active').first()
    if param:
        param.valeur = ''
        db.session.commit()
        flash('⚠️ Aucune période active', 'warning')
    
    return redirect(url_for('parametres'))


@app.route('/api/paiement/<int:paiement_id>')
@login_required
def api_get_paiement(paiement_id):
    """Récupère les détails d'un paiement pour l'API"""
    paiement = Paiement.query.get_or_404(paiement_id)
    
    if current_user.role not in ['admin', 'comptable']:
        return jsonify({'success': False, 'message': 'Accès non autorisé'}), 403
    
    return jsonify({
        'success': True,
        'paiement': {
            'id': paiement.id,
            'montant': paiement.montant,
            'date_paiement': paiement.date_paiement.strftime('%Y-%m-%d'),
            'mode_paiement': paiement.type_paiement,
            'categorie_frais': getattr(paiement, 'categorie_frais', 'scolarite'),
            'reference': paiement.reference or '',
            'description': paiement.description or '',
            'recu': paiement.recu,
            'statut': paiement.statut,
            'eleve_id': paiement.eleve_id,
            'eleve_nom': paiement.eleve.nom_complet if paiement.eleve else '',
            'details': getattr(paiement, 'details', {})  # ← RÉCUPÉRER LES DÉTAILS
        }
    })


@app.route('/api/eleve/<int:eleve_id>/options')
@login_required
def api_eleve_options(eleve_id):
    """Récupère les options souscrites par l'élève"""
    eleve = Eleve.query.get_or_404(eleve_id)
    
    return jsonify({
        'success': True,
        'has_transport': eleve.transport_option_id is not None,
        'has_cantine': eleve.cantine_option_id is not None,
        'has_renforcement': eleve.renforcement_inscrit,
        'est_classe_examen': eleve.est_classe_examen,
        'transport_nom': eleve.transport_option.nom if eleve.transport_option else None,
        'cantine_nom': eleve.cantine_option.nom if eleve.cantine_option else None
    })


@app.route('/paiement/<int:id>/recu')
@login_required
def imprimer_recu(id):
    """Génère et imprime un reçu pour un paiement spécifique"""
    paiement = Paiement.query.get_or_404(id)
    eleve = paiement.eleve
    
    # Vérifier que l'utilisateur a accès
    if current_user.role not in ['admin', 'comptable']:
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('liste_eleves'))
    
    # Récupérer les paramètres de l'école
    nom_ecole = Parametre.get('nom_ecole', 'École')
    adresse_ecole = Parametre.get('adresse_ecole', '')
    telephone_ecole = Parametre.get('telephone_ecole', '')
    
    # Détail du paiement par rubrique
    details_paiement = {}
    if hasattr(paiement, 'details') and paiement.details:
        details_paiement = paiement.details
    
    return render_template('recu_paiement.html', 
                         paiement=paiement, 
                         eleve=eleve,
                         nom_ecole=nom_ecole,
                         adresse_ecole=adresse_ecole,
                         telephone_ecole=telephone_ecole,
                         details_paiement=details_paiement)

@app.route('/parametres/sauvegarder-tarif-groupe', methods=['POST'])
def sauvegarder_tarif_groupe():
    try:
        data = request.json
        groupe_id = data.get('groupe_id')
        
        # Validation
        if not groupe_id or groupe_id == 0:
            return jsonify({'success': False, 'message': 'Groupe non spécifié'}), 400
        
        groupe = GroupeScolaire.query.get(groupe_id)
        if not groupe:
            return jsonify({'success': False, 'message': f'Groupe "{groupe_id}" inexistant'}), 400
        
        print(f"Sauvegarde des tarifs pour le groupe: {groupe.nom} (id={groupe_id})")
        
        # Configurations des tarifs
        tarifs_config = [
            ('tarif_inscription', 'inscription', False),
            ('tarif_normal', 'scolarite', False),
            ('tarif_affecte', 'scolarite', True),
        ]
        
        for config_key, type_tarif, est_affecte in tarifs_config:
            tarif_data = data.get(config_key)
            if not tarif_data:
                continue
            
            # Chercher le tarif existant
            tarif = TarifFraisAffecte.query.filter_by(
                groupe_id=groupe_id,
                sous_groupe_id=None,
                type_tarif=type_tarif,
                est_affecte=est_affecte
            ).first()
            
            if not tarif:
                tarif = TarifFraisAffecte()
                tarif.groupe_id = groupe_id
                tarif.sous_groupe_id = None
                tarif.type_tarif = type_tarif
                tarif.est_affecte = est_affecte
            
            # Mettre à jour le montant
            tarif.montant = float(tarif_data.get('montant', 0))
            tarif.actif = True
            tarif.date_modification = datetime.utcnow()
            
            db.session.add(tarif)
            print(f"  - {config_key}: {tarif.montant} FCFA")
        
        db.session.commit()
        return jsonify({'success': True, 'message': 'Tarifs enregistrés avec succès'})
        
    except Exception as e:
        db.session.rollback()
        print(f"Erreur: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


# ============ ROUTES GROUPES ET SOUS-GROUPES ============

@app.route('/parametres/groupes/ajouter', methods=['POST'])
@login_required
def param_ajouter_groupe():
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    try:
        nom = request.form.get('nom')
        code = request.form.get('code')
        if not nom or not code:
            flash('Le nom et le code sont obligatoires', 'warning')
            return redirect(url_for('parametres'))
        
        if GroupeScolaire.query.filter_by(code=code).first():
            flash(f'Le code {code} existe déjà', 'warning')
            return redirect(url_for('parametres'))
        
        groupe = GroupeScolaire(
            nom=nom, code=code,
            ordre=int(request.form.get('ordre', 0)),
            description=request.form.get('description', '')
        )
        db.session.add(groupe)
        db.session.commit()
        flash(f'Groupe {nom} ajouté avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/groupes/<int:id>/modifier', methods=['POST'])
@login_required
def param_modifier_groupe(id):
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
        flash(f'Groupe {groupe.nom} modifié avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/groupes/<int:id>/supprimer')
@login_required
def param_supprimer_groupe(id):
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    groupe = GroupeScolaire.query.get_or_404(id)
    try:
        if groupe.sous_groupes:
            flash(f'Impossible de supprimer {groupe.nom} car il contient des sous-groupes', 'danger')
            return redirect(url_for('parametres'))
        
        db.session.delete(groupe)
        db.session.commit()
        flash(f'Groupe {groupe.nom} supprimé avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/sous-groupes/ajouter', methods=['POST'])
@login_required
def param_ajouter_sous_groupe():
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    try:
        nom = request.form.get('nom')
        code = request.form.get('code')
        groupe_id = request.form.get('groupe_id')
        if not nom or not code or not groupe_id:
            flash('Le nom, le code et le groupe sont obligatoires', 'warning')
            return redirect(url_for('parametres'))
        
        if SousGroupe.query.filter_by(code=code).first():
            flash(f'Le code {code} existe déjà', 'warning')
            return redirect(url_for('parametres'))
        
        sous_groupe = SousGroupe(
            nom=nom, code=code, groupe_id=int(groupe_id),
            ordre=int(request.form.get('ordre', 0)),
            description=request.form.get('description', '')
        )
        db.session.add(sous_groupe)
        db.session.commit()
        flash(f'Sous-groupe {nom} ajouté avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/sous-groupes/<int:id>/modifier', methods=['POST'])
@login_required
def param_modifier_sous_groupe(id):
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
        flash(f'Sous-groupe {sous_groupe.nom} modifié avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


@app.route('/parametres/sous-groupes/<int:id>/supprimer')
@login_required
def param_supprimer_sous_groupe(id):
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('parametres'))
    
    sous_groupe = SousGroupe.query.get_or_404(id)
    try:
        if sous_groupe.eleves:
            flash(f'Impossible de supprimer {sous_groupe.nom} car il contient des élèves', 'danger')
            return redirect(url_for('parametres'))
        
        db.session.delete(sous_groupe)
        db.session.commit()
        flash(f'Sous-groupe {sous_groupe.nom} supprimé avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur : {str(e)}', 'danger')
    
    return redirect(url_for('parametres'))


# ============ ROUTES ÉLÈVES ============

@app.route('/eleves')
@app.route('/eleves/<dossier>')
@login_required
def liste_eleves(dossier=None):
    """Liste des élèves avec filtres par dossier"""
    
    # Si un dossier est spécifié dans l'URL, le sauvegarder en session
    if dossier and dossier in ['maternelle', 'primaire', 'secondaire']:
        session['dossier_actif'] = dossier
    
    dossier_actif = session.get('dossier_actif', 'tous')
    
    # ===== CORRESPONDANCE DOSSIER → GROUPES =====
    groupes_ids = []
    sous_groupes_ids = []  # ← INITIALISER ICI
    
    if dossier_actif == 'maternelle':
        groupes_ids = [1]
        groupe_dossier = 'Maternelle'
    elif dossier_actif == 'primaire':
        groupes_ids = [2]
        groupe_dossier = 'Primaire'
    elif dossier_actif == 'secondaire':
        groupes_ids = [3, 4]
        groupe_dossier = 'Secondaire'
    else:
        groupe_dossier = None
    
    session['groupe_dossier'] = groupe_dossier
    
    # Récupérer les filtres additionnels
    sous_groupe_id = request.args.get('sous_groupe', '')
    classe = request.args.get('classe', '')
    affectation = request.args.get('affectation', '')
    statut = request.args.get('statut', '')
    cycle = request.args.get('cycle', '')
    
    # Requête de base
    query = Eleve.query.filter_by(actif=True)
    annee_active = get_annee_active()
    query = query.filter(Eleve.annee_scolaire == annee_active)
    
    # ===== FILTRAGE PAR DOSSIER =====
    if dossier_actif and dossier_actif != 'tous' and groupes_ids:
        # Récupérer tous les sous-groupes des groupes sélectionnés
        if dossier_actif == 'secondaire' and cycle:
            if cycle == 'premier_cycle':
                sous_groupes = SousGroupe.query.filter_by(groupe_id=3).all()
            elif cycle == 'second_cycle':
                sous_groupes = SousGroupe.query.filter_by(groupe_id=4).all()
            else:
                sous_groupes = SousGroupe.query.filter(SousGroupe.groupe_id.in_(groupes_ids)).all()
        else:
            sous_groupes = SousGroupe.query.filter(SousGroupe.groupe_id.in_(groupes_ids)).all()
        
        sous_groupes_ids = [sg.id for sg in sous_groupes]
        
        if sous_groupes_ids:
            query = query.filter(Eleve.sous_groupe_id.in_(sous_groupes_ids))
        else:
            query = query.filter(False)
    
    # Appliquer les filtres additionnels
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
    
    # Exécuter la requête
    eleves = query.order_by(Eleve.nom, Eleve.prenom).all()
    
    # Récupérer les IDs des élèves pour les stats
    eleves_ids = [e.id for e in eleves]
    
    # Statistiques
    total_eleves = len(eleves)
    total_frais = sum(e.frais_scolarite_total for e in eleves)
    total_paye = sum(e.montant_paye for e in eleves)
    reste_a_payer = total_frais - total_paye
    
    # Récupérer les listes pour les filtres
    if dossier_actif and dossier_actif != 'tous' and groupes_ids:
        if dossier_actif == 'secondaire' and cycle:
            if cycle == 'premier_cycle':
                sous_groupes_list = SousGroupe.query.filter_by(groupe_id=3).order_by(SousGroupe.ordre).all()
            elif cycle == 'second_cycle':
                sous_groupes_list = SousGroupe.query.filter_by(groupe_id=4).order_by(SousGroupe.ordre).all()
            else:
                sous_groupes_list = SousGroupe.query.filter(SousGroupe.groupe_id.in_(groupes_ids)).order_by(SousGroupe.ordre).all()
        else:
            sous_groupes_list = SousGroupe.query.filter(SousGroupe.groupe_id.in_(groupes_ids)).order_by(SousGroupe.ordre).all()
    else:
        sous_groupes_list = SousGroupe.query.order_by(SousGroupe.ordre).all()
    
    # Classes
    classes_disponibles = set(e.classe for e in eleves if e.classe)
    classes = sorted(classes_disponibles)
    
    groupes = GroupeScolaire.query.order_by(GroupeScolaire.ordre).all()
    
    # Statistiques du jour
    from datetime import date
    aujourdhui = date.today()
    
    # Élèves inscrits aujourd'hui (filtrés si dossier actif)
    stats_query = Eleve.query.filter(Eleve.actif == True, Eleve.annee_scolaire == annee_active)
    if dossier_actif and dossier_actif != 'tous' and sous_groupes_ids:
        stats_query = stats_query.filter(Eleve.sous_groupe_id.in_(sous_groupes_ids))
    
    eleves_inscrits_aujourdhui = stats_query.filter(Eleve.date_inscription >= aujourdhui).count()
    
    # Paiements du jour
    paiements_jour = Paiement.query.filter(
        Paiement.annee_scolaire == annee_active,
        Paiement.statut == 'actif',
        Paiement.date_paiement >= aujourdhui
    )
    
    # Filtrer par les élèves du dossier si nécessaire
    if dossier_actif and dossier_actif != 'tous' and eleves_ids:
        paiements_jour = paiements_jour.filter(Paiement.eleve_id.in_(eleves_ids))
    
    paiements_jour = paiements_jour.all()
    montant_total_paye_aujourdhui = sum(p.montant for p in paiements_jour if p.montant > 0)
    # nombre_paiements_aujourdhui = len(paiements_jour)
    
    return render_template('eleves.html',
                         eleves=eleves,
                         groupes=groupes,
                         sous_groupes=sous_groupes_list,
                         classes=classes,
                         total_eleves=total_eleves,
                         total_frais=total_frais,
                         total_paye=total_paye,
                         reste_a_payer=reste_a_payer,
                         dossier_actif=dossier_actif,
                         cycle=cycle,
                         date_aujourdhui=aujourdhui.strftime('%d/%m/%Y'),
                         eleves_inscrits_aujourdhui=eleves_inscrits_aujourdhui,
                         montant_total_paye_aujourdhui=montant_total_paye_aujourdhui
                        #  nombre_paiements_aujourdhui=nombre_paiements_aujourdhui
                        )


@app.route('/eleve/ajouter', methods=['GET', 'POST'])
@login_required
def ajouter_eleve():
    """Ajoute un nouvel élève"""
    # Récupérer le dossier actif depuis la session
    dossier_actif = session.get('dossier_actif', 'tous')
    
    if request.method == 'POST':
        try:
            nom = request.form.get('nom')
            prenom = request.form.get('prenom')
            genre = request.form.get('genre', '').upper()
            classe = request.form.get('classe')
            sous_groupe_id = request.form.get('sous_groupe_id')
            matricule = request.form.get('matricule') or generate_matricule()
            
            est_affecte_etat = 'est_affecte_etat' in request.form
            transport_option_id = request.form.get('transport_option_id') or None
            cantine_option_id = request.form.get('cantine_option_id') or None
            renforcement_inscrit = 'renforcement_inscrit' in request.form
            
            # ========== RÉCUPÉRATION DES INFORMATIONS PARENTS 1 ==========
            nom_parent = request.form.get('nom_parent', '').strip()
            profession_parent = request.form.get('profession_parent', '').strip()
            employeur = request.form.get('employeur', '').strip()
            telephone_parent = request.form.get('telephone_parent', '').strip()
            adresse = request.form.get('adresse', '').strip()
            affiliation = request.form.get('affiliation', '').strip()
            observation = request.form.get('observation', '').strip()
            
            # ========== RÉCUPÉRATION DES INFORMATIONS PARENTS 2 ==========
            nom_parent1 = request.form.get('nom_parent1', '').strip()
            profession_parent1 = request.form.get('profession_parent1', '').strip()
            employeur2 = request.form.get('employeur2', '').strip()
            telephone_parent2 = request.form.get('telephone_parent2', '').strip()
            adresse1 = request.form.get('adresse1', '').strip()
            affiliation1 = request.form.get('affiliation1', '').strip()
            observation1 = request.form.get('observation1', '').strip()
            
            # ========== RÉCUPÉRATION DES INFORMATIONS AFFECTATION ==========
            reference_affectation = request.form.get('reference_affectation', '').strip() if est_affecte_etat else None
            organisme_affectation = request.form.get('organisme_affectation', '').strip() if est_affecte_etat else None
            observation_affectation = request.form.get('observation_affectation', '').strip() if est_affecte_etat else None
            
            # ========== RÉCUPÉRATION DE LA DATE ET LIEU DE NAISSANCE ==========
            date_naissance_str = request.form.get('date_naissance')
            date_naissance = datetime.strptime(date_naissance_str, '%Y-%m-%d').date() if date_naissance_str else None
            lieu_naissance = request.form.get('lieu_naissance', '').strip()
            
            # Calcul des frais
            frais_total = calculer_frais_total(
                sous_groupe_id=sous_groupe_id,
                est_affecte=est_affecte_etat,
                transport_option_id=transport_option_id,
                cantine_option_id=cantine_option_id,
                renforcement_inscrit=renforcement_inscrit,
                classe=classe
            )
            
            eleve = Eleve(
                # Informations personnelles
                nom=nom, 
                prenom=prenom, 
                genre=genre, 
                classe=classe,
                sous_groupe_id=sous_groupe_id, 
                matricule=matricule,
                date_naissance=date_naissance,
                lieu_naissance=lieu_naissance,
                
                # Affectation
                est_affecte_etat=est_affecte_etat,
                reference_affectation=reference_affectation,
                organisme_affectation=organisme_affectation,
                commentaire=observation_affectation,
                
                # Options
                transport_option_id=transport_option_id,
                cantine_option_id=cantine_option_id,
                renforcement_inscrit=renforcement_inscrit,
                
                # Frais
                frais_scolarite=frais_total,
                montant_paye=0,
                
                # Dates
                date_inscription=datetime.utcnow(),
                annee_scolaire=get_annee_active(),
                
                # Parent 1
                nom_parent=nom_parent or None,
                profession_parent=profession_parent or None,
                employeur=employeur or None,
                telephone_parent=telephone_parent or None,
                adresse=adresse or None,
                affiliation=affiliation or None,
                observation=observation or None,
                
                # Parent 2
                nom_parent1=nom_parent1 or None,
                profession_parent1=profession_parent1 or None,
                employeur2=employeur2 or None,
                telephone_parent2=telephone_parent2 or None,
                adresse1=adresse1 or None,
                affiliation1=affiliation1 or None,
                observation1=observation1 or None
            )
            
            db.session.add(eleve)
            db.session.commit()
            flash(f'Élève {eleve.nom_complet} ajouté avec succès !', 'success')
            return redirect(url_for('liste_eleves'))
            
        except Exception as e:
            db.session.rollback()
            import traceback
            traceback.print_exc()
            flash(f'Erreur : {str(e)}', 'danger')
    
    groupes = GroupeScolaire.query.order_by(GroupeScolaire.ordre).all()
    transports = OptionTransport.query.filter_by(actif=True).order_by(OptionTransport.ordre).all()
    cantines = OptionCantine.query.filter_by(actif=True).order_by(OptionCantine.ordre).all()
    
    return render_template('ajouter_eleve.html',
                         groupes=groupes,
                         transports=transports,
                         cantines=cantines,
                         dossier_actif=dossier_actif)


@app.route('/paiement/ajouter-multiple/<int:eleve_id>', methods=['POST'])
@login_required
def ajouter_paiement_multiple(eleve_id):
    try:
        eleve = Eleve.query.get_or_404(eleve_id)
        
        # Récupérer les montants individuels
        montant_inscription = float(request.form.get('inscription_montant', 0))
        montant_tenue = float(request.form.get('tenue_montant', 0))
        montant_examen = float(request.form.get('examen_montant', 0))
        montant_scolarite = float(request.form.get('scolarite_montant', 0))
        montant_transport = float(request.form.get('transport_montant', 0))
        montant_cantine = float(request.form.get('cantine_montant', 0))
        montant_renforcement = float(request.form.get('renforcement_montant', 0))
        
        # Calculer le total
        total = (montant_inscription + montant_tenue + montant_examen + 
                 montant_scolarite + montant_transport + montant_cantine + 
                 montant_renforcement)
        
        if total <= 0:
            return jsonify({'success': False, 'message': 'Montant invalide'}), 400
        
        # Générer un numéro de reçu
        recu = f"REC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{eleve.id}"
        
        # Déterminer la catégorie principale
        categories = {
            'inscription': montant_inscription,
            'tenue': montant_tenue,
            'droit_examen': montant_examen,
            'scolarite': montant_scolarite,
            'transport': montant_transport,
            'cantine': montant_cantine,
            'renforcement': montant_renforcement
        }
        categorie_principale = max(categories, key=categories.get) if max(categories.values()) > 0 else 'scolarite'
        
        # Créer le paiement AVEC les nouveaux champs
        paiement = Paiement(
            eleve_id=eleve.id,
            montant=total,
            type_paiement=request.form.get('type_paiement', 'especes'),
            reference=request.form.get('reference', ''),
            description=request.form.get('description', ''),
            recu=recu,
            date_paiement=datetime.utcnow(),
            encaisse_par=current_user.nom_complet,
            annee_scolaire=get_annee_active(),
            statut='actif',
            categorie_frais=categorie_principale,
            details={  # ← STOCKER TOUS LES DÉTAILS
                'inscription': montant_inscription,
                'tenue': montant_tenue,
                'examen': montant_examen,
                'scolarite': montant_scolarite,
                'transport': montant_transport,
                'cantine': montant_cantine,
                'renforcement': montant_renforcement
            }
        )
        
        db.session.add(paiement)
        
        # Mettre à jour le montant payé de l'élève
        eleve.montant_paye = sum(p.montant for p in eleve.paiements if p.statut == 'actif')
        eleve.date_modification = datetime.utcnow()
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Paiement de {total:,.0f} FCFA enregistré avec succès',
            'recu': recu,
            'nouveau_solde': eleve.solde,
            'nouveau_montant_paye': eleve.montant_paye
        })
        
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/calcul-frais/<int:sous_groupe_id>')
@login_required
def api_calcul_frais(sous_groupe_id):
    """Calcule les frais pour un niveau donné"""
    est_affecte = request.args.get('est_affecte', 'false').lower() == 'true'
    transport_montant = float(request.args.get('transport', 0))
    cantine_montant = float(request.args.get('cantine', 0))
    renforcement_actif = request.args.get('renforcement', 'false').lower() == 'true'
    
    sous_groupe = SousGroupe.query.get(sous_groupe_id)
    if not sous_groupe:
        return jsonify({'success': False, 'message': 'Sous-groupe non trouvé'})
    
    # Scolarité de base
    tarif = TarifFraisAffecte.query.filter_by(
        groupe_id=sous_groupe.groupe_id,
        est_affecte=est_affecte,
        type_tarif='scolarite',
        actif=True
    ).first()
    
    frais_scolarite_base = tarif.montant if tarif else 0
    
    # Tenue scolaire
    groupe_nom = sous_groupe.groupe_parent.nom
    if groupe_nom == 'Maternelle':
        frais_tenue = float(Parametre.get('montant_tenue_maternelle', 15000))
    elif groupe_nom == 'Primaire':
        frais_tenue = float(Parametre.get('montant_tenue_primaire', 15000))
    else:
        frais_tenue = float(Parametre.get('montant_tenue_secondaire', 20000))
    
    # Droit d'examen
    frais_droit_examen = 0
    if sous_groupe.nom in ['CM2', '3ème', 'Terminale']:
        if sous_groupe.nom == 'CM2':
            frais_droit_examen = float(Parametre.get('droit_examen_cm2_ministere', 5000)) + float(Parametre.get('droit_examen_cm2_ecole', 3000))
        elif sous_groupe.nom == '3ème':
            frais_droit_examen = float(Parametre.get('droit_examen_3eme_ministere', 8000)) + float(Parametre.get('droit_examen_3eme_ecole', 5000))
        elif sous_groupe.nom == 'Terminale':
            frais_droit_examen = float(Parametre.get('droit_examen_tle_ministere', 10000)) + float(Parametre.get('droit_examen_tle_ecole', 7000))
    
    # Renforcement
    frais_renforcement = 0
    if renforcement_actif:
        tarif_renf = TarifFrais.query.filter_by(
            type_frais_id=4,
            sous_groupe_id=sous_groupe_id,
            actif=True
        ).first()
        frais_renforcement = tarif_renf.montant if tarif_renf else 0
    
    total = frais_scolarite_base + transport_montant + cantine_montant + frais_renforcement + frais_tenue + frais_droit_examen
    
    return jsonify({
        'success': True,
        'frais_scolarite_base': frais_scolarite_base,
        'frais_transport': transport_montant,
        'frais_cantine': cantine_montant,
        'frais_renforcement': frais_renforcement,
        'frais_tenue': frais_tenue,
        'frais_droit_examen': frais_droit_examen,
        'total': total
    })


@app.route('/eleve/modifier/<int:id>', methods=['GET', 'POST'])
@login_required
def modifier_eleve(id):
    eleve = Eleve.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            # Informations personnelles
            eleve.nom = request.form.get('nom')
            eleve.prenom = request.form.get('prenom')
            eleve.genre = request.form.get('genre', '').upper()
            eleve.classe = request.form.get('classe')
            eleve.matricule = request.form.get('matricule')
            
            # Date et lieu de naissance
            date_naissance_str = request.form.get('date_naissance')
            eleve.date_naissance = datetime.strptime(date_naissance_str, '%Y-%m-%d').date() if date_naissance_str else None
            eleve.lieu_naissance = request.form.get('lieu_naissance', '').strip()
            
            # Niveau
            eleve.sous_groupe_id = request.form.get('sous_groupe_id')
            
            # Affectation
            est_affecte_etat = 'est_affecte_etat' in request.form
            eleve.est_affecte_etat = est_affecte_etat
            if est_affecte_etat:
                eleve.reference_affectation = request.form.get('reference_affectation', '').strip()
                eleve.organisme_affectation = request.form.get('organisme_affectation', '').strip()
            else:
                eleve.reference_affectation = None
                eleve.organisme_affectation = None
            
            # Options
            transport_check = request.form.get('transport_actif') == 'on'
            eleve.transport_option_id = request.form.get('transport_option_id') if transport_check else None
            
            cantine_check = request.form.get('cantine_actif') == 'on'
            eleve.cantine_option_id = request.form.get('cantine_option_id') if cantine_check else None
            
            eleve.renforcement_inscrit = 'renforcement_inscrit' in request.form
            
            # Parent 1
            eleve.nom_parent = request.form.get('nom_parent', '').strip() or None
            eleve.telephone_parent = request.form.get('telephone_parent', '').strip() or None
            eleve.profession_parent = request.form.get('profession_parent', '').strip() or None
            eleve.employeur = request.form.get('employeur', '').strip() or None
            eleve.adresse = request.form.get('adresse', '').strip() or None
            eleve.affiliation = request.form.get('affiliation', '').strip() or None
            eleve.observation = request.form.get('observation', '').strip() or None
            
            # Parent 2
            eleve.nom_parent1 = request.form.get('nom_parent1', '').strip() or None
            eleve.telephone_parent2 = request.form.get('telephone_parent2', '').strip() or None
            eleve.profession_parent1 = request.form.get('profession_parent1', '').strip() or None
            eleve.employeur2 = request.form.get('employeur2', '').strip() or None
            eleve.adresse1 = request.form.get('adresse1', '').strip() or None
            eleve.affiliation1 = request.form.get('affiliation1', '').strip() or None
            eleve.observation1 = request.form.get('observation1', '').strip() or None
            
            # Recalculer les frais
            eleve.frais_scolarite = calculer_frais_total(
                sous_groupe_id=eleve.sous_groupe_id,
                est_affecte=eleve.est_affecte_etat,
                transport_option_id=eleve.transport_option_id,
                cantine_option_id=eleve.cantine_option_id,
                renforcement_inscrit=eleve.renforcement_inscrit,
                classe=eleve.classe
            )
            
            eleve.date_modification = datetime.utcnow()
            
            db.session.commit()
            flash('Élève modifié avec succès', 'success')
            return redirect(url_for('liste_eleves'))
            
        except Exception as e:
            db.session.rollback()
            import traceback
            traceback.print_exc()
            flash(f'Erreur : {str(e)}', 'danger')
    
    groupes = GroupeScolaire.query.order_by(GroupeScolaire.ordre).all()
    sous_groupes = SousGroupe.query.order_by(SousGroupe.ordre).all()
    transports = OptionTransport.query.filter_by(actif=True).all()
    cantines = OptionCantine.query.filter_by(actif=True).all()
    
    return render_template('modifier_eleve.html',
                         eleve=eleve,
                         groupes=groupes,
                         sous_groupes=sous_groupes,
                         transports=transports,
                         cantines=cantines)


@app.route('/eleve/<int:id>/supprimer')
@login_required
def supprimer_eleve(id):
    """Supprime un élève"""
    eleve = Eleve.query.get_or_404(id)
    
    try:
        if Paiement.query.filter_by(eleve_id=id).count() > 0:
            flash(f'Impossible de supprimer {eleve.nom_complet} car il a des paiements associés', 'danger')
            return redirect(url_for('liste_eleves'))
        
        db.session.delete(eleve)
        db.session.commit()
        flash(f'Élève {eleve.nom_complet} supprimé avec succès !', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur : {str(e)}', 'danger')
    
    return redirect(url_for('liste_eleves'))


# ============ API ROUTES ============

@app.route('/api/groupes/<int:groupe_id>/sous-groupes')
@login_required
def api_get_sous_groupes(groupe_id):
    sous_groupes = SousGroupe.query.filter_by(groupe_id=groupe_id, actif=True).order_by(SousGroupe.ordre).all()
    return jsonify([{'id': sg.id, 'nom': sg.nom, 'code': sg.code} for sg in sous_groupes])


@app.route('/api/options-transport')
@login_required
def api_get_options_transport():
    options = OptionTransport.query.filter_by(actif=True).all()
    return jsonify([{'id': o.id, 'nom': o.nom, 'montant': o.montant_supplement} for o in options])


@app.route('/api/options-cantine')
@login_required
def api_get_options_cantine():
    options = OptionCantine.query.filter_by(actif=True).all()
    return jsonify([{'id': o.id, 'nom': o.nom, 'montant': o.montant} for o in options])


# ============ CONTEXT PROCESSOR ============

@app.context_processor
def injecter_parametres_ecole():
    try:
        params = Parametre.query.all()
        parametres = {p.cle: p.valeur for p in params}
    except:
        parametres = {}
    
    defaults = {
        'nom_ecole': 'GS LAUREADES',
        'devise': 'FCFA',
        'annee_scolaire': '2024-2025',
        'adresse_ecole': '',
        'telephone_ecole': '',
        'email_notification': ''
    }
    
    for key, value in defaults.items():
        if key not in parametres or not parametres[key]:
            parametres[key] = value
    
    return dict(parametres_ecole=parametres)


# ============ GESTION DES PAIEMENTS (conservé pour compatibilité) ============
# ... (gardez toutes vos routes de paiement existantes)