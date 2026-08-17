# Rapports générés

`sync_fighters.py` génère ici `fighter_identity_quarantine.json`. Le fichier est
ignoré par Git et téléversé comme artefact du workflow combattants. Il ne
contient aucune clé ou secret. Pour chaque candidat, il conserve les combats
comparés ainsi que la relation entre dates de naissance, lieux de naissance et
mensurations afin que l'administrateur comprenne pourquoi une association a été
acceptée, refusée ou laissée en attente.
