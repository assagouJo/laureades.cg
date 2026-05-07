from models import Eleve


def calculer_subventions_etat():
    """
    Calcule les subventions totales que l'État doit
    Retourne un dictionnaire avec les statistiques
    """
    stats = {
        'total_subvention': 0,
        'eleves_affectes': 0,
        'subvention_par_niveau': {},
        'subvention_detail': []
    }
    
    eleves_affectes = Eleve.query.filter_by(est_affecte_etat=True).all()
    
    for eleve in eleves_affectes:
        subvention = eleve.subvention_etat
        if subvention > 0:
            stats['total_subvention'] += subvention
            stats['eleves_affectes'] += 1
            
            niveau = eleve.sous_groupe.nom if eleve.sous_groupe else 'Inconnu'
            
            # Par niveau
            if niveau not in stats['subvention_par_niveau']:
                stats['subvention_par_niveau'][niveau] = {
                    'nombre': 0,
                    'montant': 0
                }
            stats['subvention_par_niveau'][niveau]['nombre'] += 1
            stats['subvention_par_niveau'][niveau]['montant'] += subvention
            
            # Détail par élève
            stats['subvention_detail'].append({
                'eleve_id': eleve.id,
                'nom': f"{eleve.prenom} {eleve.nom}",
                'matricule': eleve.matricule,
                'niveau': niveau,
                'tarif_normal': eleve.frais_scolarite_total if not eleve.est_affecte_etat else (
                    eleve.frais_scolarite_total + subvention
                ),
                'tarif_affecte': eleve.frais_scolarite_total,
                'subvention': subvention
            })
    
    return stats