# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

# Créer db ici au lieu de l'importer
db = SQLAlchemy()


# ========== FONCTIONS UTILITAIRES ==========

def calculer_frais_total(sous_groupe_id, est_affecte, transport_option_id=None, 
                         cantine_option_id=None, renforcement_inscrit=False, classe=None):
    """Calcule le montant total des frais pour un élève"""
    total = 0
    
    # 1. Frais de scolarité selon niveau et statut d'affectation
    if sous_groupe_id:
        sous_groupe = SousGroupe.query.get(int(sous_groupe_id))
        
        if sous_groupe:
            # Chercher d'abord par GROUPE (nouveau système)
            tarif = TarifFraisAffecte.query.filter_by(
                groupe_id=sous_groupe.groupe_id,
                est_affecte=est_affecte,
                actif=True
            ).first()
            
            # Si pas de tarif par groupe, chercher par sous-groupe (ancien système)
            if not tarif:
                tarif = TarifFraisAffecte.query.filter_by(
                    sous_groupe_id=sous_groupe_id,
                    est_affecte=est_affecte,
                    actif=True
                ).first()
            
            if tarif:
                total += tarif.montant
            else:
                # Essayer le tarif normal par groupe
                tarif_normal = TarifFraisAffecte.query.filter_by(
                    groupe_id=sous_groupe.groupe_id,
                    est_affecte=False,
                    actif=True
                ).first()
                
                if not tarif_normal:
                    # Fallback : tarif normal par sous-groupe
                    tarif_normal = TarifFraisAffecte.query.filter_by(
                        sous_groupe_id=sous_groupe_id,
                        est_affecte=False,
                        actif=True
                    ).first()
                
                if tarif_normal:
                    total += tarif_normal.montant
    
    # 2. Frais de transport
    if transport_option_id:
        transport = OptionTransport.query.get(int(transport_option_id))
        if transport:
            total += transport.montant_supplement
    
    # 3. Frais de cantine
    if cantine_option_id:
        cantine = OptionCantine.query.get(int(cantine_option_id))
        if cantine:
            total += cantine.montant
    
    # 4. Frais de renforcement
    if renforcement_inscrit:
        classes_renforcement = ['CM1', 'CM2', '3ème', 'Terminale']
        if classe in classes_renforcement or renforcement_inscrit:
            tarif_renf = TarifFrais.query.filter_by(
                type_frais_id=4,  # ID pour le renforcement
                sous_groupe_id=sous_groupe_id,
                actif=True
            ).first()
            if tarif_renf:
                total += tarif_renf.montant
    
    # 5. Tenue obligatoire
    if sous_groupe_id:
        sous_groupe = SousGroupe.query.get(int(sous_groupe_id))
        if sous_groupe and sous_groupe.groupe_parent:
            groupe_nom = sous_groupe.groupe_parent.nom
            if groupe_nom == 'Maternelle':
                total += float(Parametre.get('montant_tenue_maternelle', 15000))
            elif groupe_nom == 'Primaire':
                total += float(Parametre.get('montant_tenue_primaire', 15000))
            elif groupe_nom == 'Secondaire':
                total += float(Parametre.get('montant_tenue_secondaire', 20000))
    
    # 6. Droit d'examen
    if classe:
        if classe == 'CM2':
            total += float(Parametre.get('droit_examen_cm2_ministere', 5000))
            total += float(Parametre.get('droit_examen_cm2_ecole', 3000))
        elif classe == '3ème':
            total += float(Parametre.get('droit_examen_3eme_ministere', 8000))
            total += float(Parametre.get('droit_examen_3eme_ecole', 5000))
        elif classe == 'Terminale':
            total += float(Parametre.get('droit_examen_tle_ministere', 10000))
            total += float(Parametre.get('droit_examen_tle_ecole', 7000))
    
    return total


# ========== MODÈLES ==========

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(200), nullable=False)
    
    # Informations personnelles
    nom = db.Column(db.String(100), nullable=False)
    prenom = db.Column(db.String(100), nullable=False)
    
    # Rôle
    role = db.Column(db.String(20), default='user')  # admin, comptable, user
    
    # Statut du compte
    actif = db.Column(db.Boolean, default=True)
    
    # Dates
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)
    
    def set_password(self, password):
        """Hache et définit le mot de passe"""
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
    
    def check_password(self, password):
        """Vérifie le mot de passe"""
        return check_password_hash(self.password_hash, password)
    
    @property
    def is_admin(self):
        return self.role == 'admin'
    
    @property
    def is_comptable(self):
        return self.role in ['admin', 'comptable']
    
    @property
    def nom_complet(self):
        """Retourne le nom complet de l'utilisateur"""
        return f"{self.prenom} {self.nom}"
    
    def update_last_login(self):
        self.last_login = datetime.utcnow()
        db.session.commit()
    
    def __repr__(self):
        return f'<User {self.username} ({self.nom_complet}) - {self.role}>'


class GroupeScolaire(db.Model):
    """Groupes principaux: Maternelle, Primaire, Premier Cycle, Second Cycle"""
    __tablename__ = 'groupes_scolaires'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(50), unique=True)
    ordre = db.Column(db.Integer, default=0)
    description = db.Column(db.String(200))
    actif = db.Column(db.Boolean, default=True)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relations
    sous_groupes = db.relationship('SousGroupe', backref='groupe_parent', lazy=True, cascade='all, delete-orphan')
    tarifs_affectation = db.relationship('TarifFraisAffecte', backref='groupe', lazy=True, 
                                         foreign_keys='TarifFraisAffecte.groupe_id',
                                         cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<GroupeScolaire {self.nom}>'


class SousGroupe(db.Model):
    """Sous-groupes: Maternelle(Garderie, TPS, PS, MS, GS), Primaire(cp1, cp2, ce1, ce2, cm1, cm2), Secondaire(6eme, 5eme, 4eme, jusqu'a terminale)"""
    __tablename__ = 'sous_groupes'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(50), unique=True)
    groupe_id = db.Column(db.Integer, db.ForeignKey('groupes_scolaires.id'), nullable=False)
    ordre = db.Column(db.Integer, default=0)
    description = db.Column(db.String(200))
    actif = db.Column(db.Boolean, default=True)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relations
    eleves = db.relationship('Eleve', backref='sous_groupe', lazy=True)
    tarifs_affectation = db.relationship('TarifFraisAffecte', backref='sous_groupe', lazy=True, 
                                         foreign_keys='TarifFraisAffecte.sous_groupe_id',
                                         cascade='all, delete-orphan')
    tarifs_frais = db.relationship('TarifFrais', backref='sous_groupe', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<SousGroupe {self.nom}>'
    

class Parametre(db.Model):
    """Paramètres généraux de l'application"""
    __tablename__ = 'parametres'
    id = db.Column(db.Integer, primary_key=True)
    cle = db.Column(db.String(100), unique=True, nullable=False)
    valeur = db.Column(db.String(500))
    description = db.Column(db.String(200))
    date_modification = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get(cls, cle, default=None):
        """Récupère la valeur d'un paramètre"""
        param = cls.query.filter_by(cle=cle).first()
        return param.valeur if param else default
    
    @classmethod
    def set(cls, cle, valeur, description=None):
        """Définit la valeur d'un paramètre"""
        param = cls.query.filter_by(cle=cle).first()
        if param:
            param.valeur = valeur
            if description:
                param.description = description
        else:
            param = cls(cle=cle, valeur=valeur, description=description or '')
            db.session.add(param)
        db.session.commit()
        return param
    
    def __repr__(self):
        return f'<Parametre {self.cle}={self.valeur}>'


class TypeFrais(db.Model):
    """Types de frais disponibles"""
    __tablename__ = 'types_frais'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200))
    ordre = db.Column(db.Integer, default=0)
    actif = db.Column(db.Boolean, default=True)
    
    # Relations
    tarifs = db.relationship('TarifFrais', backref='type_frais', lazy=True)
    
    def __repr__(self):
        return f'<TypeFrais {self.nom}>'


class OptionTransport(db.Model):
    """Options de transport scolaire avec différents circuits"""
    __tablename__ = 'options_transport'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200))
    montant_supplement = db.Column(db.Float, default=0, nullable=False)
    actif = db.Column(db.Boolean, default=True)
    ordre = db.Column(db.Integer, default=0)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relations
    eleves = db.relationship('Eleve', backref='transport_option', lazy=True, foreign_keys='Eleve.transport_option_id')
    
    def __repr__(self):
        return f'<OptionTransport {self.nom}: {self.montant_supplement} FCFA>'


class OptionCantine(db.Model):
    """Options de cantine scolaire par niveau"""
    __tablename__ = 'options_cantine'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(50), unique=True, nullable=False)
    description = db.Column(db.String(200))
    montant = db.Column(db.Float, default=0, nullable=False)
    actif = db.Column(db.Boolean, default=True)
    ordre = db.Column(db.Integer, default=0)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relations
    eleves = db.relationship('Eleve', backref='cantine_option', lazy=True, foreign_keys='Eleve.cantine_option_id')
    
    def __repr__(self):
        return f'<OptionCantine {self.nom}: {self.montant} FCFA>'


class TarifFrais(db.Model):
    """Tarifs des frais par niveau (Renforcement, etc.)"""
    __tablename__ = 'tarifs_frais'
    id = db.Column(db.Integer, primary_key=True)
    type_frais_id = db.Column(db.Integer, db.ForeignKey('types_frais.id'), nullable=False)
    sous_groupe_id = db.Column(db.Integer, db.ForeignKey('sous_groupes.id'), nullable=False)
    montant = db.Column(db.Float, default=0, nullable=False)
    est_obligatoire = db.Column(db.Boolean, default=False)
    actif = db.Column(db.Boolean, default=True)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    date_modification = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Contrainte d'unicité
    __table_args__ = (db.UniqueConstraint('type_frais_id', 'sous_groupe_id', name='unique_tarif_niveau'),)
    
    def __repr__(self):
        return f'<TarifFrais {self.type_frais.nom} - {self.sous_groupe.nom}: {self.montant} FCFA>'

class TarifFraisAffecte(db.Model):
    """Tarifs différenciés pour élèves affectés et non affectés par l'État"""
    __tablename__ = 'tarifs_frais_affecte'
    id = db.Column(db.Integer, primary_key=True)
    
    # Permettre soit un sous_groupe_id soit un groupe_id
    sous_groupe_id = db.Column(db.Integer, db.ForeignKey('sous_groupes.id'), nullable=True)
    groupe_id = db.Column(db.Integer, db.ForeignKey('groupes_scolaires.id'), nullable=True)
    
    est_affecte = db.Column(db.Boolean, default=False)
    type_tarif = db.Column(db.String(20), default='scolarite')
    montant = db.Column(db.Float, default=0, nullable=False)
    actif = db.Column(db.Boolean, default=True)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    date_modification = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 🔥 CORRECTION : Inclure type_tarif dans les contraintes d'unicité
    __table_args__ = (
        # Pour les sous-groupes : un seul tarif par (sous_groupe, type_tarif, est_affecte)
        db.UniqueConstraint('sous_groupe_id', 'type_tarif', 'est_affecte', 
                            name='unique_tarif_sous_groupe'),
        # Pour les groupes : un seul tarif par (groupe, type_tarif, est_affecte)
        db.UniqueConstraint('groupe_id', 'type_tarif', 'est_affecte', 
                            name='unique_tarif_groupe'),
    )
    
    @property
    def type_affectation(self):
        return "Affecté par l'État" if self.est_affecte else "Non affecté"
    
    @property
    def niveau_nom(self):
        """Retourne le nom du niveau (groupe ou sous-groupe)"""
        if self.sous_groupe:
            return self.sous_groupe.nom
        elif self.groupe:
            return self.groupe.nom
        return "Non défini"
    
    def __repr__(self):
        niveau = self.niveau_nom
        statut = "Affecté" if self.est_affecte else "Non affecté"
        return f'<TarifFraisAffecte {statut} - {niveau}: {self.montant} FCFA>'

class Eleve(db.Model):
    """Modèle principal des élèves"""
    __tablename__ = 'eleves'
    
    # Informations eleves
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    prenom = db.Column(db.String(100), nullable=False)
    classe = db.Column(db.String(50), nullable=False)
    genre = db.Column(db.String(1))
    date_naissance = db.Column(db.Date, nullable=True)
    lieu_naissance = db.Column(db.String(100))
    matricule = db.Column(db.String(50), unique=True, nullable=False)
    
    # Informations parents1
    nom_parent = db.Column(db.String(200))
    profession_parent = db.Column(db.String(200))
    employeur = db.Column(db.String(200))
    telephone_parent = db.Column(db.String(20))
    adresse = db.Column(db.String(200))
    affiliation = db.Column(db.String(100))
    observation = db.Column(db.String(200))
    
    # Informations parents2
    nom_parent1 = db.Column(db.String(200))
    profession_parent1 = db.Column(db.String(200))
    employeur2 = db.Column(db.String(200))
    telephone_parent2 = db.Column(db.String(20))
    adresse1 = db.Column(db.String(200))
    affiliation1 = db.Column(db.String(100))
    observation1 = db.Column(db.String(200))

    # Affectation par l'État
    est_affecte_etat = db.Column(db.Boolean, default=False)
    reference_affectation = db.Column(db.String(100))
    organisme_affectation = db.Column(db.String(100), default='État')
    
    # Options souscrites
    sous_groupe_id = db.Column(db.Integer, db.ForeignKey('sous_groupes.id'), nullable=True)
    transport_option_id = db.Column(db.Integer, db.ForeignKey('options_transport.id'), nullable=True)
    cantine_option_id = db.Column(db.Integer, db.ForeignKey('options_cantine.id'), nullable=True)
    renforcement_inscrit = db.Column(db.Boolean, default=False)
    frais_tenue = db.Column(db.Float, default=0)
    frais_droit_examen = db.Column(db.Float, default=0)
    
    # Frais et paiements
    frais_scolarite = db.Column(db.Float, default=0)
    montant_paye = db.Column(db.Float, default=0)
    
    # Dates
    date_inscription = db.Column(db.DateTime, default=datetime.utcnow)
    date_modification = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Statut
    actif = db.Column(db.Boolean, default=True)
    commentaire = db.Column(db.Text)
    annee_scolaire = db.Column(db.String(20), default='2026-2027')
    reinscrit = db.Column(db.Boolean, default=False)
    date_reinscription = db.Column(db.DateTime, nullable=True)
    reinscrit_par = db.Column(db.String(100), nullable=True)
    
    # Relations
    paiements = db.relationship('Paiement', backref='eleve', lazy=True, cascade='all, delete-orphan')

    # ========== PROPRIÉTÉS ==========
    
    @property
    def statut_reinscription(self):
        if self.reinscrit:
            return f'Réinscrit - {self.annee_scolaire}'
        return 'Non réinscrit'
    
    @property
    def badge_reinscription(self):
        return 'badge bg-success' if self.reinscrit else 'badge bg-warning text-dark'
    
    @property
    def frais_scolarite_base(self):
        """Calcule le montant de base de la scolarité (hors options)"""
        if not self.sous_groupe_id:
            return 0
        
        sous_groupe = SousGroupe.query.get(self.sous_groupe_id)
        if not sous_groupe:
            return 0
        
        # 1. Chercher d'abord par GROUPE (nouveau système) avec type_tarif='scolarite'
        tarif = TarifFraisAffecte.query.filter_by(
            groupe_id=sous_groupe.groupe_id,
            est_affecte=self.est_affecte_etat,
            type_tarif='scolarite',  # ← AJOUTER CETTE LIGNE
            actif=True
        ).first()
        
        if tarif:
            return tarif.montant
        
        # 2. Sinon chercher par sous-groupe (ancien système) avec type_tarif='scolarite'
        tarif = TarifFraisAffecte.query.filter_by(
            sous_groupe_id=self.sous_groupe_id,
            est_affecte=self.est_affecte_etat,
            type_tarif='scolarite',  # ← AJOUTER CETTE LIGNE
            actif=True
        ).first()
        
        if tarif:
            return tarif.montant
        
        # 3. Fallback au tarif normal (non affecté) par GROUPE
        tarif_normal = TarifFraisAffecte.query.filter_by(
            groupe_id=sous_groupe.groupe_id,
            est_affecte=False,
            type_tarif='scolarite',  # ← AJOUTER CETTE LIGNE
            actif=True
        ).first()
        
        if tarif_normal:
            return tarif_normal.montant
        
        # 4. Dernier fallback par sous-groupe
        tarif_normal = TarifFraisAffecte.query.filter_by(
            sous_groupe_id=self.sous_groupe_id,
            est_affecte=False,
            type_tarif='scolarite',  # ← AJOUTER CETTE LIGNE
            actif=True
        ).first()
        
        return tarif_normal.montant if tarif_normal else 0
        
        def mettre_a_jour_frais_scolarite(self):
            """Recalcule et met à jour les frais de scolarité de l'élève"""
            self.frais_scolarite = calculer_frais_total(
                sous_groupe_id=self.sous_groupe_id,
                est_affecte=self.est_affecte_etat,
                transport_option_id=self.transport_option_id,
                cantine_option_id=self.cantine_option_id,
                renforcement_inscrit=self.renforcement_inscrit,
                classe=self.classe
            )
            self.date_modification = datetime.utcnow()
    
    @property
    def frais_transport(self):
        if self.transport_option:
            return self.transport_option.montant_supplement
        return 0
    
    @property
    def frais_cantine(self):
        if self.cantine_option:
            return self.cantine_option.montant
        return 0
    
    @property
    def frais_renforcement(self):
        """Frais de renforcement (obligatoire pour les classes examen)"""
        # Vérifier si l'élève est en classe d'examen ou s'il est inscrit au renforcement
        if self.est_classe_examen or self.renforcement_inscrit:
            tarif = TarifFrais.query.filter_by(
                type_frais_id=4,  # ID pour le renforcement
                sous_groupe_id=self.sous_groupe_id,
                actif=True
            ).first()
            return tarif.montant if tarif else 0
        return 0

    @property
    def est_renforcement_obligatoire(self):
        """Vérifie si le renforcement est obligatoire (classes examen)"""
        classes_renforcement_obligatoire = ['CM2', '3ème', 'Terminale']
        # Vérifier par le niveau (sous_groupe.nom) ou par la classe
        if self.sous_groupe:
            return self.sous_groupe.nom in classes_renforcement_obligatoire
        return self.classe in classes_renforcement_obligatoire    
    
    @property
    def frais_droit_examen_montant(self):
        """Calcule dynamiquement le montant du droit d'examen depuis les paramètres"""
        # Vérifier par l'ID du sous-groupe (plus fiable)
        if self.sous_groupe_id:
            # IDs des sous-groupes examen (à vérifier dans votre base)
            # Exécutez d'abord le diagnostic pour connaître les IDs exacts
            ids_examen = [11, 15, 18]  # À remplacer par les vrais IDs
            
            if self.sous_groupe_id == 11:  # CM2
                ministere = float(Parametre.get('droit_examen_cm2_ministere', 5000))
                ecole = float(Parametre.get('droit_examen_cm2_ecole', 3000))
                return ministere + ecole
            elif self.sous_groupe_id == 15:  # 3ème
                ministere = float(Parametre.get('droit_examen_3eme_ministere', 8000))
                ecole = float(Parametre.get('droit_examen_3eme_ecole', 5000))
                return ministere + ecole
            elif self.sous_groupe_id == 18:  # Terminale
                ministere = float(Parametre.get('droit_examen_tle_ministere', 10000))
                ecole = float(Parametre.get('droit_examen_tle_ecole', 7000))
                return ministere + ecole
        
        return 0
    
    @property
    def est_classe_examen(self):
        if self.sous_groupe:
            return self.sous_groupe.nom in ['CM2', '3ème', 'Terminale']
        return False
    
    @property
    def detail_droit_examen(self):
        if self.classe == 'CM2':
            return {
                'ministere': float(Parametre.get('droit_examen_cm2_ministere', 5000)),
                'ecole': float(Parametre.get('droit_examen_cm2_ecole', 3000))
            }
        elif self.classe == '3ème':
            return {
                'ministere': float(Parametre.get('droit_examen_3eme_ministere', 8000)),
                'ecole': float(Parametre.get('droit_examen_3eme_ecole', 5000))
            }
        elif self.classe == 'Terminale':
            return {
                'ministere': float(Parametre.get('droit_examen_tle_ministere', 10000)),
                'ecole': float(Parametre.get('droit_examen_tle_ecole', 7000))
            }
        return {'ministere': 0, 'ecole': 0}
    
    @property
    def subvention_etat(self):
        """Calcule ce que l'État doit pour cet élève affecté"""
        if not self.est_affecte_etat or not self.sous_groupe:
            return 0
        
        # Chercher d'abord par groupe
        tarif_normal = TarifFraisAffecte.query.filter_by(
            groupe_id=self.sous_groupe.groupe_id,
            est_affecte=False,
            actif=True
        ).first()
        
        tarif_affecte = TarifFraisAffecte.query.filter_by(
            groupe_id=self.sous_groupe.groupe_id,
            est_affecte=True,
            actif=True
        ).first()
        
        # Fallback par sous-groupe
        if not tarif_normal:
            tarif_normal = TarifFraisAffecte.query.filter_by(
                sous_groupe_id=self.sous_groupe_id,
                est_affecte=False,
                actif=True
            ).first()
        
        if not tarif_affecte:
            tarif_affecte = TarifFraisAffecte.query.filter_by(
                sous_groupe_id=self.sous_groupe_id,
                est_affecte=True,
                actif=True
            ).first()
        
        if tarif_normal and tarif_affecte:
            return tarif_normal.montant - tarif_affecte.montant
        return 0
    
    @property
    def statut_subvention(self):
        """Statut de la subvention État"""
        subvention = self.subvention_etat
        if subvention <= 0:
            return "Non concerné"
        
        montant_du = self.frais_scolarite_total - subvention
        
        if self.montant_paye >= self.frais_scolarite_total:
            return "Complet"
        elif self.montant_paye >= montant_du:
            return "Partiel (État)"
        else:
            return "En attente État"
    
    @property
    def frais_scolarite_total(self):
        """Calcule le total de tous les frais (sans double comptage)"""
        total = 0
        
        # 1. Scolarité de base (calculée depuis les tarifs)
        total += self.frais_scolarite_base or 0
        
        # 2. Frais d'inscription
        total += self.frais_inscription_montant or 0
        
        # 3. Tenue scolaire
        total += self.frais_tenue_montant or 0
        
        # 4. Transport (optionnel)
        if self.transport_option:
            total += self.frais_transport or 0
        
        # 5. Cantine (optionnelle)
        if self.cantine_option:
            total += self.frais_cantine or 0
        
        # 6. Renforcement (obligatoire pour classes examen)
        total += self.frais_renforcement or 0
        
        # 7. Droit d'examen (obligatoire pour classes examen)
        if self.est_classe_examen:
            total += self.frais_droit_examen_montant or 0
        
        return total
        
    @property
    def solde(self):
        return self.frais_scolarite_total - self.montant_paye_reel
    
    @property
    def statut_paiement(self):
        if self.solde <= 0:
            return 'Payé'
        elif self.montant_paye > 0:
            return 'Partiel'
        return 'Impayé'
    
    @property
    def taux_paiement(self):
        if self.frais_scolarite_total > 0:
            return round((self.montant_paye / self.frais_scolarite_total) * 100, 2)
        return 0
    
    @property
    def est_renforcement_obligatoire(self):
        classes_renforcement_obligatoire = ['CM1', 'CM2', '3ème', 'Terminale']
        return self.classe in classes_renforcement_obligatoire
    
    @property
    def frais_inscription_montant(self):
        """Récupère le montant d'inscription depuis les tarifs du groupe"""
        if not self.sous_groupe:
            return 0
        
        # Chercher le tarif d'inscription pour ce groupe
        tarif = TarifFraisAffecte.query.filter_by(
            groupe_id=self.sous_groupe.groupe_id,
            type_tarif='inscription',
            actif=True
        ).first()
        
        if tarif:
            return tarif.montant
        return 0

    @property
    def frais_tenue_montant(self):
        """Récupère le montant de la tenue depuis les paramètres selon le groupe"""
        if not self.sous_groupe or not self.sous_groupe.groupe_parent:
            return 0
        
        groupe_id = self.sous_groupe.groupe_id
        groupe_nom = self.sous_groupe.groupe_parent.nom
        
        # Maternelle (id=1)
        if groupe_id == 1 or groupe_nom == 'Maternelle':
            return float(Parametre.get('montant_tenue_maternelle', 15000))
        # Primaire (id=2)
        elif groupe_id == 2 or groupe_nom == 'Primaire':
            return float(Parametre.get('montant_tenue_primaire', 15000))
        # Premier Cycle (id=3) ou Second Cycle (id=4)
        elif groupe_id in [3, 4] or groupe_nom in ['Premier Cycle', 'Second Cycle', 'Secondaire']:
            return float(Parametre.get('montant_tenue_secondaire', 20000))
        
        return 0
    
    
    @property
    def nom_complet(self):
        return f"{self.prenom} {self.nom}"
    
    @property
    def montant_paye_reel(self):
        return sum(p.montant for p in self.paiements if p.montant > 0 and p.statut == 'actif')
    
    @property
    def type_affectation(self):
        return "Affecté État" if self.est_affecte_etat else "Non affecté"
    
    @property
    def genre_icon(self):
        if self.genre == 'M':
            return '♂️'
        elif self.genre == 'F':
            return '♀️'
        return '👤'
    
    # Dans la classe Eleve, ajoutez ces propriétés

    @property
    def montant_paye_inscription(self):
        """Montant déjà payé pour l'inscription"""
        total = 0
        for p in self.paiements:
            if p.statut == 'actif' and p.details:
                total += p.details.get('inscription', 0)
        return total

    @property
    def montant_paye_tenue(self):
        """Montant déjà payé pour la tenue"""
        total = 0
        for p in self.paiements:
            if p.statut == 'actif' and p.details:
                total += p.details.get('tenue', 0)
        return total

    @property
    def montant_paye_examen(self):
        """Montant déjà payé pour le droit d'examen"""
        total = 0
        for p in self.paiements:
            if p.statut == 'actif' and p.details:
                total += p.details.get('examen', 0)
        return total

    @property
    def montant_paye_scolarite(self):
        """Montant déjà payé pour la scolarité"""
        total = 0
        for p in self.paiements:
            if p.statut == 'actif' and p.details:
                total += p.details.get('scolarite', 0)
        return total

    @property
    def montant_paye_transport(self):
        """Montant déjà payé pour le transport"""
        total = 0
        for p in self.paiements:
            if p.statut == 'actif' and p.details:
                total += p.details.get('transport', 0)
        return total

    @property
    def montant_paye_cantine(self):
        """Montant déjà payé pour la cantine"""
        total = 0
        for p in self.paiements:
            if p.statut == 'actif' and p.details:
                total += p.details.get('cantine', 0)
        return total

    @property
    def montant_paye_renforcement(self):
        """Montant déjà payé pour le renforcement"""
        total = 0
        for p in self.paiements:
            if p.statut == 'actif' and p.details:
                total += p.details.get('renforcement', 0)
        return total
    
    def __repr__(self):
        return f'<Eleve {self.nom_complet} - {self.matricule}>'


class Paiement(db.Model):
    """Modèle des paiements effectués par les élèves"""
    __tablename__ = 'paiements'
    
    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(db.Integer, db.ForeignKey('eleves.id'), nullable=False)
    montant = db.Column(db.Float, nullable=False)
    type_paiement = db.Column(db.String(50), nullable=False)
    reference = db.Column(db.String(100))
    description = db.Column(db.String(200))
    recu = db.Column(db.String(50), unique=True)
    date_paiement = db.Column(db.DateTime, default=datetime.utcnow)
    encaisse_par = db.Column(db.String(100))
    
    # Gestion des annulations et avoirs
    statut = db.Column(db.String(20), default='actif')
    annule_le = db.Column(db.DateTime, nullable=True)
    annule_par = db.Column(db.String(100), nullable=True)
    motif_annulation = db.Column(db.Text, nullable=True)
    annee_scolaire = db.Column(db.String(20), default='2025-2026')

    categorie_frais = db.Column(db.String(50), default='scolarite')  # Pour la catégorie principale
    details = db.Column(db.JSON, default={})
    
    def __repr__(self):
        type_str = 'AVOIR' if self.est_avoir else 'Paiement'
        return f'<{type_str} {self.recu} - {self.montant} FCFA>'
    
    def peut_etre_annule(self):
        for liaison in self.depots_lies:
            if liaison.depot.statut == 'valide':
                return False
        return True
    
    def get_depot_actif(self):
        for liaison in self.depots_lies:
            if liaison.depot.statut == 'valide':
                return liaison.depot
        return None
    
    @property
    def est_avoir(self):
        return self.montant < 0 or (self.recu and self.recu.startswith('AV-'))
    
    @property
    def est_annule(self):
        return self.statut == 'annule'
    
    @property
    def montant_absolu(self):
        return abs(self.montant)
    
    def est_verrouille_par_depot(self):
        for liaison in self.depots_lies:
            if liaison.depot and liaison.depot.statut == 'valide':
                return True
        return False
    
    def peut_etre_modifie(self):
        return not self.est_verrouille_par_depot()


class HistoriquePriseEnCharge(db.Model):
    """Historique des changements de statut d'affectation"""
    __tablename__ = 'historique_prises_en_charge'
    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(db.Integer, db.ForeignKey('eleves.id'), nullable=False)
    date_changement = db.Column(db.DateTime, default=datetime.utcnow)
    ancien_statut = db.Column(db.Boolean, default=False)
    nouveau_statut = db.Column(db.Boolean, default=False)
    reference = db.Column(db.String(100))
    organisme = db.Column(db.String(100))
    motif = db.Column(db.String(200))
    utilisateur_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    
    eleve = db.relationship('Eleve', backref='historique_affectations')
    utilisateur = db.relationship('User', backref='modifications_affectations')
    
    def __repr__(self):
        return f'<HistoriquePriseEnCharge {self.eleve.nom_complet} - {self.date_changement}>'
    

class DepotBancaire(db.Model):
    """Dépôts bancaires groupés"""
    __tablename__ = 'depots_bancaires'
    id = db.Column(db.Integer, primary_key=True)
    numero_depot = db.Column(db.String(50), unique=True, nullable=False)
    montant_total = db.Column(db.Numeric(10, 0), nullable=False)
    date_depot = db.Column(db.DateTime, default=datetime.utcnow)
    date_validation = db.Column(db.DateTime)
    statut = db.Column(db.String(20), default='en_attente')
    effectue_par = db.Column(db.String(50))
    banque = db.Column(db.String(100), default='Banque')
    reference_banque = db.Column(db.String(100))
    observations = db.Column(db.String(200))
    annee_scolaire = db.Column(db.String(20), default='2025-2026')
    
    paiements = db.relationship('PaiementDepot', backref='depot', lazy=True, cascade='all, delete-orphan')
    
    def peut_etre_modifie(self):
        return self.statut != 'valide' and self.statut != 'annule'
    
    def peut_etre_annule(self):
        return self.statut == 'en_attente'
    
    def annuler(self):
        if not self.peut_etre_annule():
            raise ValueError(f"Impossible d'annuler un dépôt avec le statut '{self.statut}'")
        self.statut = 'annule'
    
    def retirer_paiement(self, paiement_id):
        if not self.peut_etre_modifie():
            raise ValueError("Impossible de modifier un dépôt déjà validé ou annulé")
        paiement_depot = PaiementDepot.query.filter_by(depot_id=self.id, paiement_id=paiement_id).first()
        if paiement_depot:
            db.session.delete(paiement_depot)
            self.montant_total -= paiement_depot.paiement.montant
    
    def valider(self, reference_banque=None):
        if self.statut == 'annule':
            raise ValueError("Impossible de valider un dépôt annulé")
        self.statut = 'valide'
        self.date_validation = datetime.utcnow()
        if reference_banque:
            self.reference_banque = reference_banque


class PaiementDepot(db.Model):
    """Liaison entre les paiements et les dépôts bancaires"""
    __tablename__ = 'paiements_depots'
    id = db.Column(db.Integer, primary_key=True)
    paiement_id = db.Column(db.Integer, db.ForeignKey('paiements.id'), nullable=False)
    depot_id = db.Column(db.Integer, db.ForeignKey('depots_bancaires.id'), nullable=False)
    date_liaison = db.Column(db.DateTime, default=datetime.utcnow)
    
    paiement = db.relationship('Paiement', backref='depots_lies')
    
    
class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    action = db.Column(db.String(100))
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('audit_logs', lazy=True))


# ========== FONCTIONS DE VÉRIFICATION ==========

def verifier_depot_valide(paiement_id):
    """Vérifie si un paiement fait partie d'un dépôt validé"""
    depot_actif = db.session.query(DepotBancaire)\
        .join(PaiementDepot)\
        .filter(
            PaiementDepot.paiement_id == paiement_id,
            DepotBancaire.statut == 'valide'
        ).first()
    return depot_actif is not None


def paiements_verrouilles_par_depot(paiement_ids):
    """Retourne la liste des IDs de paiements qui sont dans un dépôt validé"""
    paiements_verrouilles = db.session.query(PaiementDepot.paiement_id)\
        .join(DepotBancaire)\
        .filter(
            PaiementDepot.paiement_id.in_(paiement_ids),
            DepotBancaire.statut == 'valide'
        ).all()
    return [p[0] for p in paiements_verrouilles]


def annuler_paiement_avec_verification(paiement_id):
    """Tente d'annuler un paiement en vérifiant le verrouillage par dépôt"""
    paiement = Paiement.query.get(paiement_id)
    if not paiement:
        raise ValueError("Paiement introuvable")
    if verifier_depot_valide(paiement_id):
        depot = paiement.get_depot_actif()
        raise ValueError(
            f"Impossible d'annuler ou modifier ce paiement. "
            f"Il fait partie du dépôt bancaire n°{depot.numero_depot} "
            f"déjà validé le {depot.date_validation.strftime('%d/%m/%Y')}."
        )
    paiement.statut = 'annule'
    return True


# ========== TRIGGERS SQLAlchemy ==========

from sqlalchemy import event

@event.listens_for(PaiementDepot, 'after_delete')
def empecher_suppression_si_depot_valide(mapper, connection, target):
    """Empêche la suppression d'un lien si le dépôt est validé"""
    depot = DepotBancaire.query.get(target.depot_id)
    if depot and depot.statut == 'valide':
        raise Exception(
            f"Impossible de dissocier un paiement d'un dépôt bancaire validé "
            f"(Dépôt n°{depot.numero_depot})"
        )


@event.listens_for(PaiementDepot, 'before_update')
def empecher_modification_si_depot_valide(mapper, connection, target):
    """Empêche la modification d'un lien si le dépôt est validé"""
    depot = DepotBancaire.query.get(target.depot_id)
    if depot and depot.statut == 'valide':
        raise Exception(
            f"Impossible de modifier un lien avec un dépôt bancaire validé "
            f"(Dépôt n°{depot.numero_depot})"
        )