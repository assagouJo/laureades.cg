# init_tables.py
# Script d'initialisation SÉCURISÉ - Ne supprime jamais les données existantes
# python init_tables.py

from app import app, db
from sqlalchemy import inspect, text
from datetime import datetime
from models import (
    User, GroupeScolaire, SousGroupe, TypeFrais, OptionTransport, 
    OptionCantine, TarifFrais, TarifFraisAffecte, Parametre
)

def safe_add_missing_columns():
    """Ajoute uniquement les colonnes manquantes sans toucher aux données"""
    inspector = inspect(db.engine)
    
    columns_to_check = {
        # 'eleves': {
        #     'email_parent': 'VARCHAR(120)',
        #     'photo': 'VARCHAR(200)',
        # },
    }
    
    for table, columns in columns_to_check.items():
        existing_cols = [c['name'] for c in inspector.get_columns(table)]
        for col_name, col_type in columns.items():
            if col_name not in existing_cols:
                try:
                    with db.engine.begin() as conn:
                        sql = f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}"
                        conn.execute(text(sql))
                    print(f"  ➕ Colonne ajoutée: {table}.{col_name}")
                except Exception as e:
                    print(f"  ⚠️ Erreur: {e}")

def get_or_create(session, model, defaults=None, **kwargs):
    """
    ✅ Récupère un enregistrement existant ou le crée s'il n'existe pas
    🔒 Ne modifie JAMAIS un enregistrement existant
    """
    instance = session.query(model).filter_by(**kwargs).first()
    if instance:
        return instance, False  # False = existait déjà
    else:
        params = dict(kwargs)
        if defaults:
            params.update(defaults)
        instance = model(**params)
        session.add(instance)
        return instance, True  # True = nouvellement créé

def init_database():
    with app.app_context():
        print("=" * 70)
        print("🚀 INITIALISATION SÉCURISÉE DE LA BASE DE DONNÉES - GestScolaire")
        print("=" * 70)
        print("⚠️  MODE SÉCURISÉ : Aucune donnée existante ne sera modifiée")
        
        # 🔒 Étape 1 : Créer UNIQUEMENT les tables manquantes
        print("\n📊 Vérification des tables...")
        try:
            db.create_all()
            print("✅ Tables vérifiées (les tables existantes sont conservées)")
        except Exception as e:
            print(f"⚠️  Note: {e}")
            print("✅ Les tables existantes sont intactes")
        
        safe_add_missing_columns()
        
        # 🔒 Étape 2 : Groupes scolaires (création uniquement si absent)
        print("\n" + "─" * 70)
        print("📚 2. VÉRIFICATION DES GROUPES SCOLAIRES")
        print("─" * 70)
        
        groupes = [
            {'nom': 'Maternelle', 'code': 'MATERNELLE', 'ordre': 1,
             'description': 'Garderie, TPS, PS, MS, GS'},
            {'nom': 'Primaire', 'code': 'PRIMAIRE', 'ordre': 2,
             'description': 'CP1, CP2, CE1, CE2, CM1, CM2'},
            {'nom': 'Secondaire', 'code': 'SECONDAIRE', 'ordre': 3,
             'description': 'Premier Cycle + Second Cycle'}
        ]
        
        for g in groupes:
            instance, created = get_or_create(db.session, GroupeScolaire, **g)
            if created:
                print(f"  ✅ Groupe créé : {g['nom']}")
            else:
                print(f"  ⏭️  Groupe existant : {g['nom']} (conservé)")
        
        db.session.commit()
        
        # 🔒 Étape 3 : Sous-groupes
        print("\n" + "─" * 70)
        print("📖 3. VÉRIFICATION DES SOUS-GROUPES")
        print("─" * 70)
        
        sous_groupes_config = {
            'Maternelle': [
                {'nom': 'Garderie', 'code': 'GARDERIE', 'ordre': 1},
                {'nom': 'TPS', 'code': 'TPS', 'ordre': 2},
                {'nom': 'PS', 'code': 'PS', 'ordre': 3},
                {'nom': 'MS', 'code': 'MS', 'ordre': 4},
                {'nom': 'GS', 'code': 'GS', 'ordre': 5},
            ],
            'Primaire': [
                {'nom': 'CP1', 'code': 'CP1', 'ordre': 1},
                {'nom': 'CP2', 'code': 'CP2', 'ordre': 2},
                {'nom': 'CE1', 'code': 'CE1', 'ordre': 3},
                {'nom': 'CE2', 'code': 'CE2', 'ordre': 4},
                {'nom': 'CM1', 'code': 'CM1', 'ordre': 5},
                {'nom': 'CM2', 'code': 'CM2', 'ordre': 6},
            ],
            'Secondaire': [
                {'nom': 'Premier Cycle', 'code': 'CYCLE_PREMIER', 'ordre': 0},
                {'nom': 'Second Cycle', 'code': 'CYCLE_SECOND', 'ordre': 1},
                {'nom': '6ème', 'code': '6EME', 'ordre': 2},
                {'nom': '5ème', 'code': '5EME', 'ordre': 3},
                {'nom': '4ème', 'code': '4EME', 'ordre': 4},
                {'nom': '3ème', 'code': '3EME', 'ordre': 5},
                {'nom': 'Seconde', 'code': 'SECONDE', 'ordre': 6},
                {'nom': 'Première', 'code': 'PREMIERE', 'ordre': 7},
                {'nom': 'Terminale', 'code': 'TERMINALE', 'ordre': 8},
            ]
        }
        
        created_count = 0
        for groupe_nom, sous_groupes in sous_groupes_config.items():
            groupe = GroupeScolaire.query.filter_by(nom=groupe_nom).first()
            if groupe:
                for sg in sous_groupes:
                    instance, created = get_or_create(
                        db.session, SousGroupe,
                        code=sg['code'],
                        defaults={
                            'nom': sg['nom'],
                            'groupe_id': groupe.id,
                            'ordre': sg['ordre'],
                            'description': f"{groupe_nom} - {sg['nom']}"
                        }
                    )
                    if created:
                        created_count += 1
                        print(f"  ✅ Créé : {groupe_nom} → {sg['nom']}")
                    else:
                        print(f"  ⏭️  Existant : {sg['nom']} (conservé)")
        
        db.session.commit()
        print(f"  📊 {created_count} nouveaux sous-groupes créés")
        
        # 🔒 Étape 4 : Types de frais
        print("\n" + "─" * 70)
        print("💰 4. VÉRIFICATION DES TYPES DE FRAIS")
        print("─" * 70)
        
        types_frais = [
            {'nom': 'Scolarité', 'code': 'scolarite', 'ordre': 1},
            {'nom': 'Transport', 'code': 'transport', 'ordre': 2},
            {'nom': 'Cantine', 'code': 'cantine', 'ordre': 3},
            {'nom': 'Renforcement', 'code': 'renforcement', 'ordre': 4}
        ]
        
        for tf in types_frais:
            instance, created = get_or_create(db.session, TypeFrais, 
                code=tf['code'],
                defaults={'nom': tf['nom'], 'ordre': tf['ordre']}
            )
            if created:
                print(f"  ✅ Type créé : {tf['nom']}")
            else:
                print(f"  ⏭️  Type existant : {tf['nom']} (conservé)")
        
        db.session.commit()
        
        # 🔒 Étape 5 : Options transport
        print("\n" + "─" * 70)
        print("🚌 5. VÉRIFICATION DES OPTIONS DE TRANSPORT")
        print("─" * 70)
        
        transports = [
            {'nom': 'Circuit 1', 'code': 'circuit_1', 'montant_supplement': 50000, 'ordre': 1,
             'description': 'Zone Nord'},
            {'nom': 'Circuit 2', 'code': 'circuit_2', 'montant_supplement': 55000, 'ordre': 2,
             'description': 'Zone Sud'},
            {'nom': 'Circuit 3', 'code': 'circuit_3', 'montant_supplement': 60000, 'ordre': 3,
             'description': 'Zone Centre'},
            {'nom': 'Pas de transport', 'code': 'aucun_transport', 'montant_supplement': 0, 'ordre': 99,
             'description': 'Sans transport'},
        ]
        
        for t in transports:
            instance, created = get_or_create(db.session, OptionTransport,
                code=t['code'],
                defaults={
                    'nom': t['nom'],
                    'montant_supplement': t['montant_supplement'],
                    'ordre': t['ordre'],
                    'description': t['description']
                }
            )
            if created:
                print(f"  ✅ Créé : {t['nom']}" + 
                      (f" - {t['montant_supplement']:,} FCFA" if t['montant_supplement'] > 0 else ""))
            else:
                print(f"  ⏭️  Existant : {t['nom']} (conservé)")
        
        db.session.commit()
        
        # 🔒 Étape 6 : Options cantine
        print("\n" + "─" * 70)
        print("🍽️  6. VÉRIFICATION DES OPTIONS DE CANTINE")
        print("─" * 70)
        
        cantines = [
            {'nom': 'Cantine Maternelle', 'code': 'cantine_maternelle', 'montant': 45000, 'ordre': 1},
            {'nom': 'Cantine Primaire', 'code': 'cantine_primaire', 'montant': 50000, 'ordre': 2},
            {'nom': 'Cantine Secondaire', 'code': 'cantine_secondaire', 'montant': 55000, 'ordre': 3},
            {'nom': 'Pas de cantine', 'code': 'aucune_cantine', 'montant': 0, 'ordre': 99},
        ]
        
        for c in cantines:
            instance, created = get_or_create(db.session, OptionCantine,
                code=c['code'],
                defaults={
                    'nom': c['nom'],
                    'montant': c['montant'],
                    'ordre': c['ordre']
                }
            )
            if created:
                print(f"  ✅ Créée : {c['nom']}" + 
                      (f" - {c['montant']:,} FCFA" if c['montant'] > 0 else ""))
            else:
                print(f"  ⏭️  Existante : {c['nom']} (conservée)")
        
        db.session.commit()
        
        # 🔒 Étape 7 : Tarifs (création uniquement)
        print("\n" + "─" * 70)
        print("📊 7. VÉRIFICATION DES TARIFS")
        print("─" * 70)
        
        tarifs_config = {
            'Garderie': {'normal': 200000, 'affecte': None},
            'TPS': {'normal': 200000, 'affecte': None},
            'PS': {'normal': 200000, 'affecte': None},
            'MS': {'normal': 200000, 'affecte': None},
            'GS': {'normal': 200000, 'affecte': None},
            'CP1': {'normal': 250000, 'affecte': None},
            'CP2': {'normal': 250000, 'affecte': None},
            'CE1': {'normal': 250000, 'affecte': None},
            'CE2': {'normal': 250000, 'affecte': None},
            'CM1': {'normal': 250000, 'affecte': None},
            'CM2': {'normal': 250000, 'affecte': None},
            '6ème': {'normal': 710000, 'affecte': 590000},
            '5ème': {'normal': 710000, 'affecte': 590000},
            '4ème': {'normal': 710000, 'affecte': 590000},
            '3ème': {'normal': 710000, 'affecte': 590000},
            'Seconde': {'normal': 750000, 'affecte': 610000},
            'Première': {'normal': 750000, 'affecte': 610000},
            'Terminale': {'normal': 750000, 'affecte': 610000},
            'Premier Cycle': {'normal': 710000, 'affecte': 590000},
            'Second Cycle': {'normal': 750000, 'affecte': 610000},
        }
        
        tarifs_crees = 0
        for sg_nom, tarifs in tarifs_config.items():
            sous_groupe = SousGroupe.query.filter_by(nom=sg_nom).first()
            if not sous_groupe:
                continue
            
            # Tarif NORMAL
            existant = TarifFraisAffecte.query.filter_by(
                sous_groupe_id=sous_groupe.id,
                est_affecte=False
            ).first()
            
            if not existant:
                tarif_normal = TarifFraisAffecte(
                    sous_groupe_id=sous_groupe.id,
                    est_affecte=False,
                    montant=tarifs['normal'],
                    actif=True
                )
                db.session.add(tarif_normal)
                tarifs_crees += 1
                print(f"  ✅ Tarif NORMAL : {sg_nom:15s} → {tarifs['normal']:>10,} FCFA")
            
            # Tarif AFFECTÉ (si applicable)
            if tarifs['affecte'] is not None:
                existant = TarifFraisAffecte.query.filter_by(
                    sous_groupe_id=sous_groupe.id,
                    est_affecte=True
                ).first()
                
                if not existant:
                    tarif_affecte = TarifFraisAffecte(
                        sous_groupe_id=sous_groupe.id,
                        est_affecte=True,
                        montant=tarifs['affecte'],
                        actif=True
                    )
                    db.session.add(tarif_affecte)
                    tarifs_crees += 1
                    print(f"  ✅ Tarif AFFECTÉ : {sg_nom:15s} → {tarifs['affecte']:>10,} FCFA")
        
        db.session.commit()
        print(f"  📊 {tarifs_crees} nouveaux tarifs créés")
        
        # 🔒 Étape 8 : Comptes utilisateurs (uniquement si absents)
        print("\n" + "─" * 70)
        print("👤 8. VÉRIFICATION DES COMPTES UTILISATEURS")
        print("─" * 70)
        
        admin, created = get_or_create(db.session, User,
            username='admin',
            defaults={
                'nom': 'ASSAGOU',
                'prenom': 'joel',
                'role': 'admin',
                'actif': True,
                'created_at': datetime.utcnow()
            }
        )
        if created:
            admin.set_password('admin123')
            print("  ✅ Compte admin créé")
        else:
            print("  ⏭️  Compte admin existant (conservé)")
        
        compta, created = get_or_create(db.session, User,
            username='compta',
            defaults={
                'nom': 'KRA',
                'prenom': 'Eli',
                'role': 'comptable',
                'actif': True,
                'created_at': datetime.utcnow()
            }
        )
        if created:
            compta.set_password('compta123')
            print("  ✅ Compte comptable créé")
        else:
            print("  ⏭️  Compte comptable existant (conservé)")
        
        db.session.commit()
        
        # 🔒 Étape 9 : Paramètres (création uniquement si absents)
        print("\n" + "─" * 70)
        print("📅 9. VÉRIFICATION DES PARAMÈTRES")
        print("─" * 70)
        
        parametres = [
            {'cle': 'annee_scolaire_active', 'valeur': '2026-2027', 
             'description': 'Année scolaire en cours pour les réinscriptions'},
            {'cle': 'annees_scolaires', 'valeur': '2026-2027,2027-2028,2028-2029', 
             'description': 'Liste des années disponibles'},
            {'cle': 'periode_debut', 'valeur': '2026-09-01', 
             'description': 'Date de début de la période active'},
            {'cle': 'periode_fin', 'valeur': '2027-07-31', 
             'description': 'Date de fin de la période active'},
            {'cle': 'frais_reinscription', 'valeur': '0', 
             'description': 'Frais de réinscription (FCFA)'},
            {'cle': 'montant_tenue_maternelle', 'valeur': '15000', 
             'description': 'Montant tenue maternelle (FCFA)'},
            {'cle': 'montant_tenue_primaire', 'valeur': '15000', 
             'description': 'Montant tenue primaire (FCFA)'},
            {'cle': 'montant_tenue_secondaire', 'valeur': '20000', 
             'description': 'Montant tenue secondaire (FCFA)'},
            {'cle': 'droit_examen_cm2_ministere', 'valeur': '5000', 
             'description': 'Droit examen ministère - CM2 (FCFA)'},
            {'cle': 'droit_examen_cm2_ecole', 'valeur': '3000', 
             'description': 'Droit examen école - CM2 (FCFA)'},
            {'cle': 'droit_examen_3eme_ministere', 'valeur': '8000', 
             'description': 'Droit examen ministère - 3ème/BEPC (FCFA)'},
            {'cle': 'droit_examen_3eme_ecole', 'valeur': '5000', 
             'description': 'Droit examen école - 3ème (FCFA)'},
            {'cle': 'droit_examen_tle_ministere', 'valeur': '10000', 
             'description': 'Droit examen ministère - Terminale/BAC (FCFA)'},
            {'cle': 'droit_examen_tle_ecole', 'valeur': '7000', 
             'description': 'Droit examen école - Terminale (FCFA)'},
        ]
        
        params_crees = 0
        for p in parametres:
            instance, created = get_or_create(db.session, Parametre,
                cle=p['cle'],
                defaults={'valeur': p['valeur'], 'description': p['description']}
            )
            if created:
                params_crees += 1
                print(f"  ✅ Paramètre créé : {p['cle']} = {p['valeur']}")
            else:
                print(f"  ⏭️  Paramètre existant : {p['cle']} = {instance.valeur} (conservé)")
        
        db.session.commit()
        print(f"  📊 {params_crees} nouveaux paramètres créés")
        
        # ============================================================
        # RÉSUMÉ FINAL
        # ============================================================
        print("\n" + "=" * 70)
        print("✅ INITIALISATION TERMINÉE AVEC SUCCÈS")
        print("=" * 70)
        print(f"👥 Utilisateurs : {User.query.count()}")
        print(f"📚 Groupes : {GroupeScolaire.query.count()}")
        print(f"📖 Sous-groupes : {SousGroupe.query.count()}")
        print(f"💰 Types frais : {TypeFrais.query.count()}")
        print(f"🚌 Transports : {OptionTransport.query.count()}")
        print(f"🍽️  Cantines : {OptionCantine.query.count()}")
        print(f"📊 Tarifs : {TarifFraisAffecte.query.count()}")
        print(f"⚙️  Paramètres : {Parametre.query.count()}")
        print("\n🔒 TOUTES LES DONNÉES EXISTANTES ONT ÉTÉ PRÉSERVÉES")
        print("=" * 70)

if __name__ == '__main__':
    init_database()