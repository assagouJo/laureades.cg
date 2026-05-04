# init_db.py
# À exécuter une seule fois pour initialiser toutes les données de référence
# python init_db.py

from app import app, db
from models import (
    User, GroupeScolaire, SousGroupe, TypeFrais, OptionTransport, 
    OptionCantine, TarifFrais, TarifFraisAffecte, Parametre
)
from datetime import datetime

def init_database():
    with app.app_context():
        print("=" * 70)
        print("🚀 INITIALISATION DE LA BASE DE DONNÉES - GestScolaire")
        print("=" * 70)
        
        # Créer toutes les tables
        db.create_all()
        print("✅ Tables créées avec succès")
        
        # ============================================================
        # 1. CRÉATION DES GROUPES
        # ============================================================
        print("\n" + "─" * 70)
        print("📚 1. CRÉATION DES GROUPES SCOLAIRES")
        print("─" * 70)
        
        groupes = [
            {
                'nom': 'Maternelle',
                'code': 'MATERNELLE',
                'ordre': 1,
                'description': 'Garderie, TPS, PS, MS, GS'
            },
            {
                'nom': 'Primaire',
                'code': 'PRIMAIRE',
                'ordre': 2,
                'description': 'CP1, CP2, CE1, CE2, CM1, CM2'
            },
            {
                'nom': 'Secondaire',
                'code': 'SECONDAIRE',
                'ordre': 3,
                'description': 'Premier Cycle (6ème→3ème) + Second Cycle (Seconde→Terminale)'
            }
        ]
        
        for g in groupes:
            existant = GroupeScolaire.query.filter_by(code=g['code']).first()
            if not existant:
                groupe = GroupeScolaire(**g)
                db.session.add(groupe)
                print(f"  ✅ Groupe créé : {g['nom']} ({g['description']})")
            else:
                print(f"  ⏭️  Groupe existant : {g['nom']}")
        
        db.session.commit()
        
        # ============================================================
        # 2. CRÉATION DES SOUS-GROUPES
        # ============================================================
        print("\n" + "─" * 70)
        print("📖 2. CRÉATION DES SOUS-GROUPES (NIVEAUX + CYCLES)")
        print("─" * 70)
        
        sous_groupes_config = {
            'Maternelle': [
                {'nom': 'Garderie',  'code': 'GARDERIE',  'ordre': 1, 'cycle': None},
                {'nom': 'TPS',       'code': 'TPS',       'ordre': 2, 'cycle': None},
                {'nom': 'PS',        'code': 'PS',        'ordre': 3, 'cycle': None},
                {'nom': 'MS',        'code': 'MS',        'ordre': 4, 'cycle': None},
                {'nom': 'GS',        'code': 'GS',        'ordre': 5, 'cycle': None},
            ],
            'Primaire': [
                {'nom': 'CP1',       'code': 'CP1',       'ordre': 1, 'cycle': None},
                {'nom': 'CP2',       'code': 'CP2',       'ordre': 2, 'cycle': None},
                {'nom': 'CE1',       'code': 'CE1',       'ordre': 3, 'cycle': None},
                {'nom': 'CE2',       'code': 'CE2',       'ordre': 4, 'cycle': None},
                {'nom': 'CM1',       'code': 'CM1',       'ordre': 5, 'cycle': None},
                {'nom': 'CM2',       'code': 'CM2',       'ordre': 6, 'cycle': None},
            ],
            'Secondaire': [
                # Sous-groupes CYCLES (pour le dropdown)
                {'nom': 'Premier Cycle', 'code': 'CYCLE_PREMIER', 'ordre': 0, 
                 'cycle': None, 'description': '6ème, 5ème, 4ème, 3ème - Collège'},
                {'nom': 'Second Cycle',  'code': 'CYCLE_SECOND',  'ordre': 1, 
                 'cycle': None, 'description': 'Seconde, Première, Terminale - Lycée'},
                
                # PREMIER CYCLE : 6ème → 3ème
                {'nom': '6ème',     'code': '6EME',     'ordre': 2, 'cycle': 'Premier Cycle'},
                {'nom': '5ème',     'code': '5EME',     'ordre': 3, 'cycle': 'Premier Cycle'},
                {'nom': '4ème',     'code': '4EME',     'ordre': 4, 'cycle': 'Premier Cycle'},
                {'nom': '3ème',     'code': '3EME',     'ordre': 5, 'cycle': 'Premier Cycle'},
                
                # SECOND CYCLE : Seconde → Terminale
                {'nom': 'Seconde',  'code': 'SECONDE',  'ordre': 6, 'cycle': 'Second Cycle'},
                {'nom': 'Première', 'code': 'PREMIERE', 'ordre': 7, 'cycle': 'Second Cycle'},
                {'nom': 'Terminale','code': 'TERMINALE','ordre': 8, 'cycle': 'Second Cycle'},
            ]
        }
        
        for groupe_nom, sous_groupes in sous_groupes_config.items():
            groupe = GroupeScolaire.query.filter_by(nom=groupe_nom).first()
            if groupe:
                for sg in sous_groupes:
                    existant = SousGroupe.query.filter_by(code=sg['code']).first()
                    if not existant:
                        cycle_info = f" → {sg['cycle']}" if sg.get('cycle') else ""
                        desc = sg.get('description', '')
                        
                        sous_groupe = SousGroupe(
                            nom=sg['nom'],
                            code=sg['code'],
                            groupe_id=groupe.id,
                            ordre=sg['ordre'],
                            description=desc if desc else (
                                f"{sg['cycle']} - {groupe_nom}" if sg.get('cycle') else groupe_nom
                            )
                        )
                        db.session.add(sous_groupe)
                        print(f"  ✅ Créé : {groupe_nom} → {sg['nom']}{cycle_info}")
                    else:
                        print(f"  ⏭️  Existant : {sg['nom']}")
        
        db.session.commit()
        
        # ============================================================
        # 3. CRÉATION DES TYPES DE FRAIS
        # ============================================================
        print("\n" + "─" * 70)
        print("💰 3. CRÉATION DES TYPES DE FRAIS")
        print("─" * 70)
        
        types_frais = [
            {'nom': 'Scolarité',    'code': 'scolarite',    'ordre': 1},
            {'nom': 'Transport',    'code': 'transport',    'ordre': 2},
            {'nom': 'Cantine',      'code': 'cantine',      'ordre': 3},
            {'nom': 'Renforcement', 'code': 'renforcement', 'ordre': 4}
        ]
        
        for tf in types_frais:
            existant = TypeFrais.query.filter_by(code=tf['code']).first()
            if not existant:
                type_frais = TypeFrais(**tf)
                db.session.add(type_frais)
                print(f"  ✅ Type créé : {tf['nom']}")
            else:
                print(f"  ⏭️  Type existant : {tf['nom']}")
        
        db.session.commit()
        
        # ============================================================
        # 4. CRÉATION DES OPTIONS DE TRANSPORT
        # ============================================================
        print("\n" + "─" * 70)
        print("🚌 4. CRÉATION DES OPTIONS DE TRANSPORT")
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
            existant = OptionTransport.query.filter_by(code=t['code']).first()
            if not existant:
                transport = OptionTransport(**t)
                db.session.add(transport)
                if t['montant_supplement'] > 0:
                    print(f"  ✅ Créé : {t['nom']} — {t['montant_supplement']:,} FCFA")
                else:
                    print(f"  ✅ Créé : {t['nom']}")
            else:
                print(f"  ⏭️  Existant : {t['nom']}")
        
        db.session.commit()
        
        # ============================================================
        # 5. CRÉATION DES OPTIONS DE CANTINE
        # ============================================================
        print("\n" + "─" * 70)
        print("🍽️  5. CRÉATION DES OPTIONS DE CANTINE")
        print("─" * 70)
        
        cantines = [
            {'nom': 'Cantine Maternelle', 'code': 'cantine_maternelle', 'montant': 45000, 'ordre': 1},
            {'nom': 'Cantine Primaire',   'code': 'cantine_primaire',   'montant': 50000, 'ordre': 2},
            {'nom': 'Cantine Secondaire', 'code': 'cantine_secondaire', 'montant': 55000, 'ordre': 3},
            {'nom': 'Pas de cantine',     'code': 'aucune_cantine',     'montant': 0,     'ordre': 99},
        ]
        
        for c in cantines:
            existant = OptionCantine.query.filter_by(code=c['code']).first()
            if not existant:
                cantine = OptionCantine(**c)
                db.session.add(cantine)
                if c['montant'] > 0:
                    print(f"  ✅ Créée : {c['nom']} — {c['montant']:,} FCFA")
                else:
                    print(f"  ✅ Créée : {c['nom']}")
            else:
                print(f"  ⏭️  Existante : {c['nom']}")
        
        db.session.commit()
        
        # ============================================================
        # 6. CRÉATION DES TARIFS DIFFÉRENCIÉS
        # ============================================================
        print("\n" + "─" * 70)
        print("📊 6. CRÉATION DES TARIFS (Normal / Affecté État)")
        print("─" * 70)
        
        # Configuration des tarifs
        # Maternelle et Primaire : PAS d'affectés État
        # Secondaire : tarifs UNIFORMES par cycle
        
        tarifs_config = {
            # ========== MATERNELLE (pas d'affectés) ==========
            'Garderie': {'normal': 200000, 'affecte': None},
            'TPS':      {'normal': 200000, 'affecte': None},
            'PS':       {'normal': 200000, 'affecte': None},
            'MS':       {'normal': 200000, 'affecte': None},
            'GS':       {'normal': 200000, 'affecte': None},
            
            # ========== PRIMAIRE (pas d'affectés) ==========
            'CP1': {'normal': 250000, 'affecte': None},
            'CP2': {'normal': 250000, 'affecte': None},
            'CE1': {'normal': 250000, 'affecte': None},
            'CE2': {'normal': 250000, 'affecte': None},
            'CM1': {'normal': 250000, 'affecte': None},
            'CM2': {'normal': 250000, 'affecte': None},
            
            # ========== PREMIER CYCLE (même tarif pour tout le cycle) ==========
            '6ème':   {'normal': 710000, 'affecte': 590000},
            '5ème':   {'normal': 710000, 'affecte': 590000},
            '4ème':   {'normal': 710000, 'affecte': 590000},
            '3ème':   {'normal': 710000, 'affecte': 590000},
            
            # ========== SECOND CYCLE (même tarif pour tout le cycle) ==========
            'Seconde':   {'normal': 750000, 'affecte': 610000},
            'Première':  {'normal': 750000, 'affecte': 610000},
            'Terminale': {'normal': 750000, 'affecte': 610000},
            
            # ========== CYCLES (pour le dropdown) ==========
            'Premier Cycle': {'normal': 710000, 'affecte': 590000},
            'Second Cycle':  {'normal': 750000, 'affecte': 610000},
        }
        
        for sg_nom, tarifs in tarifs_config.items():
            sous_groupe = SousGroupe.query.filter_by(nom=sg_nom).first()
            if not sous_groupe:
                print(f"  ⚠️  Sous-groupe '{sg_nom}' non trouvé, ignoré")
                continue
            
            # Tarif NORMAL (toujours créé)
            existant_normal = TarifFraisAffecte.query.filter_by(
                sous_groupe_id=sous_groupe.id,
                est_affecte=False
            ).first()
            
            if not existant_normal:
                tarif_normal = TarifFraisAffecte(
                    sous_groupe_id=sous_groupe.id,
                    est_affecte=False,
                    montant=tarifs['normal'],
                    actif=True
                )
                db.session.add(tarif_normal)
                print(f"  ✅ Tarif NORMAL  : {sg_nom:15s} → {tarifs['normal']:>10,} FCFA")
            
            # Tarif AFFECTÉ (seulement si non None)
            if tarifs['affecte'] is not None:
                existant_affecte = TarifFraisAffecte.query.filter_by(
                    sous_groupe_id=sous_groupe.id,
                    est_affecte=True
                ).first()
                
                if not existant_affecte:
                    tarif_affecte = TarifFraisAffecte(
                        sous_groupe_id=sous_groupe.id,
                        est_affecte=True,
                        montant=tarifs['affecte'],
                        actif=True
                    )
                    db.session.add(tarif_affecte)
                    print(f"  ✅ Tarif AFFECTÉ : {sg_nom:15s} → {tarifs['affecte']:>10,} FCFA")
            else:
                print(f"  ⚠️  Pas d'affecté : {sg_nom} (Maternelle/Primaire)")
        
        db.session.commit()
        
        # ============================================================
        # 7. CRÉATION DES TARIFS DE RENFORCEMENT
        # ============================================================
        print("\n" + "─" * 70)
        print("📚 7. CRÉATION DES TARIFS DE RENFORCEMENT")
        print("─" * 70)
        
        renforcement_config = {
            'CM2':       50000,
            '3ème':      60000,
            'Terminale': 75000
        }
        
        type_renforcement = TypeFrais.query.filter_by(code='renforcement').first()
        
        if type_renforcement:
            for classe, montant in renforcement_config.items():
                sous_groupe = SousGroupe.query.filter_by(nom=classe).first()
                if sous_groupe:
                    existant = TarifFrais.query.filter_by(
                        type_frais_id=type_renforcement.id,
                        sous_groupe_id=sous_groupe.id
                    ).first()
                    if not existant:
                        tarif = TarifFrais(
                            type_frais_id=type_renforcement.id,
                            sous_groupe_id=sous_groupe.id,
                            montant=montant,
                            est_obligatoire=True,
                            actif=True
                        )
                        db.session.add(tarif)
                        print(f"  ✅ Renforcement {classe:10s} → {montant:>8,} FCFA (obligatoire)")
        else:
            print("  ❌ Type de frais 'Renforcement' non trouvé !")
        
        db.session.commit()
        
        # ============================================================
        # 8. CRÉATION DU COMPTE ADMINISTRATEUR
        # ============================================================
        print("\n" + "─" * 70)
        print("👤 8. CRÉATION DU COMPTE ADMINISTRATEUR")
        print("─" * 70)
        
        if not User.query.filter_by(username='admin').first():
            admin = User(
                username='admin',
                nom='ASSAGOU',
                prenom='joel',
                role='admin',
                actif=True,
                created_at=datetime.utcnow()
            )
            admin.set_password('admin123')
            db.session.add(admin)
            print("  ✅ Compte administrateur créé")
        else:
            print("  ⏭️  Compte administrateur existant")
        
        # Créer aussi un compte comptable
        if not User.query.filter_by(username='compta').first():
            comptable = User(
                username='compta',
                nom='KRA',
                prenom='Eli',
                role='comptable',
                actif=True,
                created_at=datetime.utcnow()
            )
            comptable.set_password('compta123')
            db.session.add(comptable)
            print("  ✅ Compte comptable créé")
        
        db.session.commit()
                
        
        print("\n⚠️  IMPORTANT : Changez ces mots de passe après la première connexion !")
        print("=" * 70)


                # ============================================================
        # 9. INITIALISATION DES PARAMÈTRES DE PÉRIODES SCOLAIRES
        # ============================================================
        print("\n" + "─" * 70)
        print("📅 9. INITIALISATION DES PARAMÈTRES DE PÉRIODES")
        print("─" * 70)


        parametres_periodes = [
            Parametre(cle='annee_scolaire_active', valeur='2025-2026', description='Année scolaire en cours pour les réinscriptions'),
            Parametre(cle='annees_scolaires', valeur='2025-2026,2026-2027,2027-2028', description='Liste des années disponibles (séparées par des virgules)'),
            Parametre(cle='periode_debut', valeur='2025-09-01', description='Date de début de la période active'),
            Parametre(cle='periode_fin', valeur='2026-07-31', description='Date de fin de la période active'),
            Parametre(cle='frais_reinscription', valeur='0', description='Frais de réinscription (FCFA)'),
        ]

        for p in parametres_periodes:
            existant = Parametre.query.filter_by(cle=p.cle).first()
            if not existant:
                db.session.add(p)
                print(f"  ✅ Paramètre créé : {p.cle} = {p.valeur}")
            else:
                print(f"  ⏭️  Paramètre existant : {p.cle} = {existant.valeur}")

        db.session.commit()


if __name__ == '__main__':
    init_database()