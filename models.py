# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

# Créer db ici au lieu de l'importer
db = SQLAlchemy()



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
    """Groupes principaux: Materlle, Primaire, Secondaire"""
    __tablename__ = 'groupes_scolaires'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)  # Enfants, Collège, Lycée
    code = db.Column(db.String(50), unique=True)  # ENFANTS, COLLEGE, LYCEE
    ordre = db.Column(db.Integer, default=0)
    description = db.Column(db.String(200))
    actif = db.Column(db.Boolean, default=True)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relations
    sous_groupes = db.relationship('SousGroupe', backref='groupe_parent', lazy=True, cascade='all, delete-orphan')
    
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
    tarifs_affectation = db.relationship('TarifFraisAffecte', backref='sous_groupe', lazy=True, cascade='all, delete-orphan')
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
    
    def __repr__(self):
        return f'<Parametre {self.cle}={self.valeur}>'


class TypeFrais(db.Model):
    """Types de frais disponibles"""
    __tablename__ = 'types_frais'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)  # Scolarité, Transport, Cantine, Renforcement
    code = db.Column(db.String(50), unique=True, nullable=False)  # scolarite, transport, cantine, renforcement
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
    nom = db.Column(db.String(100), nullable=False)  # Circuit 1, Circuit 2, Circuit 3
    code = db.Column(db.String(50), unique=True, nullable=False)  # circuit_1, circuit_2, circuit_3
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
    nom = db.Column(db.String(100), nullable=False)  # Maternelle, Primaire, Secondaire
    code = db.Column(db.String(50), unique=True, nullable=False)  # maternelle, primaire, secondaire
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
    sous_groupe_id = db.Column(db.Integer, db.ForeignKey('sous_groupes.id'), nullable=False)
    est_affecte = db.Column(db.Boolean, default=False)  # True = affecté, False = non affecté
    montant = db.Column(db.Float, default=0, nullable=False)
    actif = db.Column(db.Boolean, default=True)
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    date_modification = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Contrainte d'unicité (un seul tarif par statut et par niveau)
    __table_args__ = (db.UniqueConstraint('sous_groupe_id', 'est_affecte', name='unique_tarif_par_statut'),)
    
    @property
    def type_affectation(self):
        return "Affecté par l'État" if self.est_affecte else "Non affecté"
    
    def __repr__(self):
        statut = "Affecté" if self.est_affecte else "Non affecté"
        return f'<TarifFraisAffecte {statut} - {self.sous_groupe.nom}: {self.montant} FCFA>'


class Eleve(db.Model):
    """Modèle principal des élèves"""
    __tablename__ = 'eleves'
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=False)
    prenom = db.Column(db.String(100), nullable=False)
    classe = db.Column(db.String(50), nullable=False)
    sous_groupe_id = db.Column(db.Integer, db.ForeignKey('sous_groupes.id'), nullable=True)
    genre = db.Column(db.String(1))
    matricule = db.Column(db.String(50), unique=True, nullable=False)
    
    # Informations personnelles
    date_naissance = db.Column(db.Date, nullable=True)
    lieu_naissance = db.Column(db.String(100))
    adresse = db.Column(db.String(200))
    nom_parent = db.Column(db.String(200))
    telephone_parent = db.Column(db.String(20))
    
    # Affectation par l'État
    est_affecte_etat = db.Column(db.Boolean, default=False)
    reference_affectation = db.Column(db.String(100))
    organisme_affectation = db.Column(db.String(100), default='État')
    
    # Options souscrites
    transport_option_id = db.Column(db.Integer, db.ForeignKey('options_transport.id'), nullable=True)
    cantine_option_id = db.Column(db.Integer, db.ForeignKey('options_cantine.id'), nullable=True)
    renforcement_inscrit = db.Column(db.Boolean, default=False)
    
    # Frais et paiements
    frais_scolarite = db.Column(db.Float, default=0)  # Total des frais (scolarité + options)
    montant_paye = db.Column(db.Float, default=0)
    
    # Dates
    date_inscription = db.Column(db.DateTime, default=datetime.utcnow)
    date_modification = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Statut
    actif = db.Column(db.Boolean, default=True)
    commentaire = db.Column(db.Text)

    annee_scolaire = db.Column(db.String(20), default='2025-2026')  # Année scolaire de l'élève
    reinscrit = db.Column(db.Boolean, default=False)  # Statut de réinscription
    date_reinscription = db.Column(db.DateTime, nullable=True)  # Date de réinscription
    reinscrit_par = db.Column(db.String(100), nullable=True)  # Qui a réinscrit
    
    # Relations
    paiements = db.relationship('Paiement', backref='eleve', lazy=True, cascade='all, delete-orphan')

    @property
    def statut_reinscription(self):
        """Retourne le statut de réinscription"""
        if self.reinscrit:
            return f'Réinscrit - {self.annee_scolaire}'
        return 'Non réinscrit'
    
    @property
    def badge_reinscription(self):
        """Retourne la classe CSS pour le badge"""
        if self.reinscrit:
            return 'badge bg-success'
        return 'badge bg-warning text-dark'
    
    @property
    def frais_scolarite_base(self):
        """Frais de scolarité selon le niveau ET le statut d'affectation"""
        if not self.sous_groupe_id:
            return 0
        
        # Chercher le tarif pour ce niveau selon le statut d'affectation
        tarif = TarifFraisAffecte.query.filter_by(
            sous_groupe_id=self.sous_groupe_id,
            est_affecte=self.est_affecte_etat,
            actif=True
        ).first()
        
        if tarif:
            return tarif.montant
        
        # Fallback: chercher le tarif non affecté
        tarif_normal = TarifFraisAffecte.query.filter_by(
            sous_groupe_id=self.sous_groupe_id,
            est_affecte=False,
            actif=True
        ).first()
        return tarif_normal.montant if tarif_normal else 0
    
    @property
    def frais_transport(self):
        """Frais de transport si option choisie"""
        if self.transport_option:
            return self.transport_option.montant_supplement
        return 0
    
    @property
    def frais_cantine(self):
        """Frais de cantine si option choisie"""
        if self.cantine_option:
            return self.cantine_option.montant
        return 0
    
    @property
    def frais_renforcement(self):
        """Frais de renforcement si obligatoire ou inscrit"""
        if self.renforcement_inscrit or self.est_renforcement_obligatoire:
            tarif = TarifFrais.query.filter_by(
                type_frais_id=4,  # Renforcement
                sous_groupe_id=self.sous_groupe_id,
                actif=True
            ).first()
            return tarif.montant if tarif else 0
        return 0
    
    @property
    def frais_scolarite_total(self):
        """Total des frais (scolarité + options + renforcement)"""
        return (self.frais_scolarite_base + 
                self.frais_transport + 
                self.frais_cantine + 
                self.frais_renforcement)
    
    @property
    def solde(self):
        return self.frais_scolarite_total - self.montant_paye
    
    @property
    def statut_paiement(self):
        if self.solde <= 0:
            return 'Payé'
        elif self.montant_paye > 0:
            return 'Partiel'
        else:
            return 'Impayé'
    
    @property
    def taux_paiement(self):
        if self.frais_scolarite_total > 0:
            return round((self.montant_paye / self.frais_scolarite_total) * 100, 2)
        return 0
    
    @property
    def est_renforcement_obligatoire(self):
        """Vérifie si le renforcement est obligatoire pour ce niveau"""
        classes_renforcement_obligatoire = ['CM1', 'CM2', '3ème', 'Terminale']
        return self.classe in classes_renforcement_obligatoire
    
    @property
    def nom_complet(self):
        return f"{self.prenom} {self.nom}"
    
    @property
    def montant_paye_reel(self):
        """
        Montant réellement payé (ignore les avoirs)
        = somme des paiements positifs uniquement
        """
        return sum(p.montant for p in self.paiements if p.montant > 0)
    
    @property
    def type_affectation(self):
        return "Affecté État" if self.est_affecte_etat else "Non affecté"
    
    def __repr__(self):
        return f'<Eleve {self.nom_complet} - {self.matricule}>'


class Paiement(db.Model):
    """Modèle des paiements effectués par les élèves"""
    __tablename__ = 'paiements'
    
    # Champs existants
    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(db.Integer, db.ForeignKey('eleves.id'), nullable=False)
    montant = db.Column(db.Float, nullable=False)
    type_paiement = db.Column(db.String(50), nullable=False)  # especes, cheque, virement, mobile_money, avoir
    reference = db.Column(db.String(100))  # N° chèque, référence transaction
    description = db.Column(db.String(200))
    recu = db.Column(db.String(50), unique=True)  # N° de reçu ou AV-... pour les avoirs
    date_paiement = db.Column(db.DateTime, default=datetime.utcnow)
    encaisse_par = db.Column(db.String(100))  # Nom de l'utilisateur qui a encaissé
    
    # Nouveaux champs pour la gestion des annulations et avoirs
    statut = db.Column(db.String(20), default='actif')  # actif, annule, avoir
    annule_le = db.Column(db.DateTime, nullable=True)
    annule_par = db.Column(db.String(100), nullable=True)  # Nom de l'utilisateur qui a annulé
    motif_annulation = db.Column(db.Text, nullable=True)

    annee_scolaire = db.Column(db.String(20), default='2025-2026')
    
    
    def __repr__(self):
        type_str = 'AVOIR' if self.est_avoir else 'Paiement'
        return f'<{type_str} {self.recu} - {self.montant} FCFA>'
    
    def peut_etre_annule(self):
        """Vérifie si le paiement peut être annulé"""
        # Vérifier si le paiement est lié à un dépôt validé
        for liaison in self.depots_lies:
            if liaison.depot.statut == 'valide':
                return False
        return True
    
    def get_depot_actif(self):
        """Retourne le dépôt valide auquel ce paiement est lié, ou None"""
        for liaison in self.depots_lies:
            if liaison.depot.statut == 'valide':
                return liaison.depot
        return None
    
    @property
    def montant_paye_periode(self):
        """Montant payé pour l'année scolaire de l'élève"""
        return sum(p.montant for p in self.paiements 
                   if p.montant > 0 and p.statut == 'actif' 
                   and p.annee_scolaire == self.annee_scolaire)
    
    @property
    def solde_periode(self):
        """Solde basé sur les paiements de l'année de l'élève"""
        return self.frais_scolarite_total - self.montant_paye_periode
    
    @property
    def est_avoir(self):
        """Retourne True si c'est un avoir (montant négatif ou reçu commence par AV-)"""
        return self.montant < 0 or (self.recu and self.recu.startswith('AV-'))
    
    @property
    def est_annule(self):
        """Retourne True si le paiement est annulé"""
        return self.statut == 'annule'
    
    @property
    def montant_absolu(self):
        """Retourne la valeur absolue du montant"""
        return abs(self.montant)


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
    
    # Relations
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
    
    paiements = db.relationship('PaiementDepot', backref='depot', lazy=True, cascade='all, delete-orphan')
    
    def peut_etre_modifie(self):
        """Un dépôt ne peut plus être modifié une fois validé"""
        return self.statut != 'valide' and self.statut != 'annule'
    
    def peut_etre_annule(self):
        """Un dépôt ne peut être annulé que s'il n'est pas encore validé"""
        return self.statut == 'en_attente'
    
    def annuler(self):
        """Annuler un dépôt et libérer les paiements associés"""
        if not self.peut_etre_annule():
            raise ValueError(f"Impossible d'annuler un dépôt avec le statut '{self.statut}'")
        
        self.statut = 'annule'
        # Les paiements ne sont pas supprimés, juste libérés du dépôt
        for paiement_depot in self.paiements:
            paiement_depot.paiement.statut = 'en_attente'  # ou un autre statut approprié
    
    def retirer_paiement(self, paiement_id):
        """Retirer un paiement spécifique du dépôt"""
        if not self.peut_etre_modifie():
            raise ValueError("Impossible de modifier un dépôt déjà validé ou annulé")
        
        paiement_depot = PaiementDepot.query.filter_by(
            depot_id=self.id, 
            paiement_id=paiement_id
        ).first()
        
        if paiement_depot:
            db.session.delete(paiement_depot)
            # Mettre à jour le montant total
            self.montant_total -= paiement_depot.paiement.montant
    
    def valider(self, reference_banque=None):
        """Valider définitivement le dépôt"""
        if self.statut == 'annule':
            raise ValueError("Impossible de valider un dépôt annulé")
        
        self.statut = 'valide'
        self.date_validation = datetime.utcnow()
        if reference_banque:
            self.reference_banque = reference_banque
        
        # Marquer tous les paiements comme déposés
        for paiement_depot in self.paiements:
            paiement_depot.paiement.statut = 'depose'  # ou autre statut final


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
    
    # Logique d'annulation ici
    paiement.statut = 'annule'
    return True


from sqlalchemy import event

@event.listens_for(PaiementDepot, 'after_delete')
def empecher_suppression_si_depot_valide(mapper, connection, target):
    """Déclencheur qui empêche la suppression d'un lien si le dépôt est validé"""
    depot = DepotBancaire.query.get(target.depot_id)
    if depot and depot.statut == 'valide':
        raise Exception(
            f"Impossible de dissocier un paiement d'un dépôt bancaire validé "
            f"(Dépôt n°{depot.numero_depot})"
        )

@event.listens_for(PaiementDepot, 'before_update')
def empecher_modification_si_depot_valide(mapper, connection, target):
    """Déclencheur qui empêche la modification d'un lien si le dépôt est validé"""
    depot = DepotBancaire.query.get(target.depot_id)
    if depot and depot.statut == 'valide':
        raise Exception(
            f"Impossible de modifier un lien avec un dépôt bancaire validé "
            f"(Dépôt n°{depot.numero_depot})"
        )