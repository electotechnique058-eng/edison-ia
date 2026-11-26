# -*- coding: utf-8 -*-
"""
NOVI IA V12
Assistante IA pour Pydroid 3 / Android (texte + voix + IA Claude via internet).
- Mémoire persistante dans memoire.json
- Calculs sûrs
- Calculs de coûts de matériel
- Connaissances électrotechniques de base
- Recherche locale dans les connaissances
- Commandes simples et dialogue
- V12 : énergies renouvelables, centrales, programmation, culture générale, politesse,
  identité du créateur et commande « apprends : question = réponse »
"""

import ast
import base64
import copy
import json
import math
import operator
import os
import queue
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime

# ---------------- VOIX ANDROID / PYDROID 3 ----------------
# Novi fonctionne toujours en texte si les fonctions vocales ne sont pas disponibles.
try:
    import androidhelper
except ImportError:
    androidhelper = None

try:
    from jnius import autoclass
except ImportError:
    autoclass = None

ANDROID_DROID = None

def initialiser_voix():
    """Prépare les fonctions vocales Android sans bloquer Novi si elles sont absentes."""
    global ANDROID_DROID
    if ANDROID_DROID is not None:
        return ANDROID_DROID
    if androidhelper is not None:
        try:
            ANDROID_DROID = androidhelper.Android()
            return ANDROID_DROID
        except Exception:
            pass
    return None

def parler(texte):
    """Lit une réponse à voix haute en français sur Android."""
    droid = initialiser_voix()
    if droid is None:
        return False
    try:
        # Évite de lire des marqueurs purement techniques.
        texte = re.sub(r"```.*?```", "", str(texte), flags=re.S).strip()
        if not texte:
            return False
        # Prononciation du nom AHODEDE : A-O-dé-dè
        texte = re.sub(r"ahod[eéè]d[eéè]", "Aodédè", texte, flags=re.I)
        droid.ttsSpeak(texte)
        return True
    except Exception:
        return False

def ecouter_voix():
    """Reconnaît une phrase prononcée par l'utilisateur, en français."""
    droid = initialiser_voix()
    if droid is None:
        return None, "La reconnaissance vocale Android n'est pas disponible dans cet environnement."
    try:
        resultat = droid.recognizeSpeech(
            "Parle à Novi",
            "fr-FR",
            None
        )
        # SL4A renvoie généralement {'result': [...]}.
        if isinstance(resultat, dict):
            valeurs = resultat.get("result") or resultat.get("RESULTS") or []
            if isinstance(valeurs, list) and valeurs:
                phrase = str(valeurs[0]).strip()
                if phrase:
                    return phrase, None
        return None, "Je n'ai pas compris. Réessaie en parlant plus clairement."
    except Exception as exc:
        return None, f"Impossible d'utiliser le microphone : {exc}"


# ---------------- APPEL VOCAL EN DIRECT ----------------
# Boucle : écoute (fenêtre vocale Android) -> réponse de Novi -> lecture à voix haute -> écoute...
RACCROCHER = re.compile(r"^\s*(raccroche|au revoir|fin de l'?appel|termine l'?appel|arr[eê]te l'?appel|on arr[eê]te|c'?est tout|stop)\b", re.I)


def attendre_fin_parole(texte, maxi=120):
    """Attend que la synthèse vocale Android ait fini de parler."""
    droid = initialiser_voix()
    debut = time.time()
    time.sleep(0.6)
    try:
        while droid is not None and time.time() - debut < maxi:
            etat = droid.ttsIsSpeaking()
            if not (etat.result if hasattr(etat, "result") else etat.get("result")):
                return
            time.sleep(0.3)
    except Exception:
        # Si l'état n'est pas disponible, on estime la durée de lecture.
        time.sleep(min(maxi, max(1.0, len(str(texte)) / 12.0)))


def texte_pour_la_voix(reponse, limite=700):
    t = re.sub(r"\s+", " ", str(reponse)).strip()
    if len(t) > limite:
        t = t[:limite].rsplit(" ", 1)[0] + ". La suite est écrite à l'écran."
    return t

MEMORY_FILE = "memoire.json"

DEFAULT_MEMORY = {
    "informations": {},
    "connaissances": {},
    "historique": []
}

# ---------------- MÉMOIRE ----------------

def charger_memoire():
    if not os.path.exists(MEMORY_FILE):
        sauvegarder_memoire(DEFAULT_MEMORY)
        return copy.deepcopy(DEFAULT_MEMORY)

    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Réparer automatiquement un ancien fichier incomplet
        for key, default in DEFAULT_MEMORY.items():
            if key not in data or not isinstance(data[key], type(default)):
                data[key] = default.copy() if isinstance(default, dict) else list(default)
        return data

    except (json.JSONDecodeError, OSError):
        # On ne détruit pas silencieusement l'ancien fichier :
        # on repart avec une mémoire propre.
        return copy.deepcopy(DEFAULT_MEMORY)


def sauvegarder_memoire(data):
    temp = MEMORY_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp, MEMORY_FILE)


memoire = charger_memoire()


def retenir(cle, valeur):
    memoire["informations"][cle.strip().lower()] = valeur.strip()
    sauvegarder_memoire(memoire)


def oublier(cle):
    cle = cle.strip().lower()
    if cle in memoire["informations"]:
        del memoire["informations"][cle]
        sauvegarder_memoire(memoire)
        return True
    return False


# ---------------- CALCULATEUR SÛR ----------------

OPERATEURS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def calculer(expression):
    """Évalue seulement des opérations mathématiques, sans eval()."""
    expression = expression.replace("×", "*").replace("÷", "/").replace("^", "**")
    expression = expression.replace(",", ".").strip()

    if len(expression) > 120:
        raise ValueError("Expression trop longue.")

    arbre = ast.parse(expression, mode="eval")

    def eval_noeud(noeud):
        if isinstance(noeud, ast.Constant) and isinstance(noeud.value, (int, float)):
            return noeud.value

        if isinstance(noeud, ast.UnaryOp) and type(noeud.op) in OPERATEURS:
            return OPERATEURS[type(noeud.op)](eval_noeud(noeud.operand))

        if isinstance(noeud, ast.BinOp) and type(noeud.op) in OPERATEURS:
            gauche = eval_noeud(noeud.left)
            droite = eval_noeud(noeud.right)

            # Protection contre les calculs absurdes
            if isinstance(noeud.op, ast.Pow) and abs(droite) > 20:
                raise ValueError("Puissance trop grande.")
            return OPERATEURS[type(noeud.op)](gauche, droite)

        raise ValueError("Expression non autorisée.")

    return eval_noeud(arbre.body)


# ---------------- OUTILS ----------------

def normaliser(texte):
    return re.sub(r"\s+", " ", texte.lower().strip())


def format_nombre(n):
    if isinstance(n, float) and n.is_integer():
        return str(int(n))
    return str(round(n, 6))


def ajouter_historique(question, reponse):
    memoire["historique"].append({
        "date": datetime.now().isoformat(timespec="seconds"),
        "question": question,
        "reponse": reponse
    })
    # On garde seulement les 50 derniers échanges pour rester léger.
    memoire["historique"] = memoire["historique"][-50:]
    sauvegarder_memoire(memoire)


# ---------------- ÉLECTROTECHNIQUE ----------------

CONNAISSANCES = {
    "loi d ohm": "Loi d'Ohm : U = R × I. Donc I = U/R et R = U/I.",
    "puissance courant continu": "En courant continu : P = U × I.",
    "puissance monophasé": "En monophasé : P = U × I × cos(phi).",
    "puissance triphasé": "En triphasé équilibré : P = √3 × U × I × cos(phi).",
    "energie": "Énergie électrique : E = P × t. Si P est en kW et t en heures, E est en kWh.",
    "moteur": "Pour diagnostiquer un moteur : vérifier alimentation, protections, continuité, isolement, connexions, courant absorbé et état mécanique.",
    "api": "Un API (automate programmable industriel) reçoit des entrées, exécute un programme et commande des sorties.",
    "maintenance": "La maintenance préventive consiste à intervenir avant la panne selon un programme. La corrective intervient après une défaillance.",
    "disjoncteur": "Un disjoncteur protège principalement contre les surintensités et, selon le type, peut aussi intégrer une protection différentielle.",
    "transformateur": "Un transformateur AC modifie un niveau de tension/courant par induction électromagnétique, à fréquence essentiellement constante.",
}


# ---------------- CONNAISSANCES PHOTOVOLTAIQUES HORS LIGNE ----------------
# Fiches pedagogiques generales. Elles peuvent etre enrichies avec les cours
# de l'utilisateur. Elles ne remplacent pas le dimensionnement professionnel.

CONNAISSANCES_PHOTOVOLTAIQUE = [
    {
        "titre": "Fonctionnement du photovoltaique",
        "cles": ["photovoltaique", "electricite solaire", "energie solaire",
                 "fonctionne un panneau solaire", "panneau photovoltaique"],
        "reponse": (
            "Le photovoltaique transforme une partie de la lumiere en electricite "
            "grace aux cellules des modules solaires. Une installation peut comprendre "
            "des modules, des cables et protections, un regulateur, une batterie si "
            "le stockage est necessaire, et un onduleur si du courant alternatif est requis. "
            "La production varie selon l'ensoleillement, la temperature, l'orientation, "
            "les ombrages et les pertes."
        )
    },
    {
        "titre": "Puissance d'un panneau solaire",
        "cles": ["puissance panneau", "watt crete", "w c", "puissance solaire",
                 "module solaire", "panneau solaire"],
        "reponse": (
            "La puissance nominale d'un module est souvent indiquee en watt-crete (Wc), "
            "mesuree dans des conditions normalisees. La puissance reelle depend des "
            "conditions sur le terrain. Le Wc mesure une puissance, tandis que le Wh "
            "ou le kWh mesure une energie produite ou consommee."
        )
    },
    {
        "titre": "Cellule photovoltaique",
        "cles": ["cellule photovoltaique", "cellule solaire", "silicium"],
        "reponse": (
            "Une cellule photovoltaique est un composant qui convertit une partie de "
            "l'energie lumineuse en energie electrique. Plusieurs cellules sont assemblees "
            "dans un module. Les performances dependent notamment de la technologie, "
            "de l'eclairement et de la temperature."
        )
    },
    {
        "titre": "Courant continu et alternatif",
        "cles": ["courant continu", "courant alternatif", "difference courant",
                 "dc ac", "convertir courant"],
        "reponse": (
            "Les modules photovoltaiques produisent du courant continu (CC/DC). "
            "Un onduleur convertit ce courant en courant alternatif (CA/AC) lorsque "
            "les appareils ou le reseau le necessitent. Il faut respecter les tensions, "
            "puissances et limites indiquees par les fabricants."
        )
    },
    {
        "titre": "Onduleur solaire",
        "cles": ["onduleur", "role de l onduleur", "a quoi sert un onduleur"],
        "reponse": (
            "Un onduleur convertit le courant continu en courant alternatif. Son choix "
            "depend notamment de la tension d'entree, de la puissance des charges, des "
            "pointes de demarrage et de la compatibilite avec les panneaux ou batteries. "
            "Il faut respecter les caracteristiques et protections recommandees par le fabricant."
        )
    },
    {
        "titre": "Batterie solaire et autonomie",
        "cles": ["batterie solaire", "stockage energie", "capacite batterie",
                 "autonomie batterie", "dimensionner batterie"],
        "reponse": (
            "Une batterie stocke l'energie pour une utilisation ulterieure. Pour l'evaluer, "
            "il faut connaitre la consommation journaliere, la tension, l'autonomie recherchee, "
            "la profondeur de decharge autorisee, le rendement et la technologie. La capacite "
            "nominale n'est pas toujours entierement utilisable. Suivre les recommandations "
            "du fabricant pour la charge, la protection et l'installation."
        )
    },
    {
        "titre": "Panneaux en serie",
        "cles": ["montage en serie", "panneaux en serie", "tension serie"],
        "reponse": (
            "Dans un montage en serie, les tensions des modules s'additionnent et le courant "
            "de la chaine depend des conditions de fonctionnement et des modules. Il faut "
            "verifier que la tension maximale, notamment par temps froid, reste dans les "
            "limites de l'onduleur ou du regulateur."
        )
    },
    {
        "titre": "Panneaux en parallele",
        "cles": ["montage en parallele", "panneaux en parallele", "courant parallele"],
        "reponse": (
            "Dans un montage en parallele, les courants des branches s'additionnent lorsque "
            "les tensions sont compatibles. La tension reste approximativement celle d'une "
            "branche identique. Les protections, les sections de cable et les limites du "
            "regulateur doivent etre verifiees."
        )
    },
    {
        "titre": "Calcul de puissance et d'energie solaire",
        "cles": ["production solaire", "calcul production panneau", "energie produite",
                 "dimensionner panneaux", "heures de soleil", "consommation journaliere"],
        "reponse": (
            "Pour une premiere estimation : energie journaliere (kWh) ≈ puissance des "
            "panneaux (kWc) × heures equivalentes de soleil × facteur global de performance. "
            "Cette estimation n'est pas un dimensionnement complet : il faut des donnees "
            "locales fiables et tenir compte de la meteo, de l'orientation, de la temperature, "
            "des ombrages et des pertes. Ne choisis pas ces valeurs au hasard pour une installation reelle."
        )
    },
    {
        "titre": "Ombrage des panneaux",
        "cles": ["ombrage", "ombre panneau", "arbre panneau solaire", "ombre solaire"],
        "reponse": (
            "L'ombrage peut reduire la production photovoltaique. Avant la pose, examine "
            "les ombres des arbres, batiments et obstacles a differents moments de la journee. "
            "L'effet depend de la configuration des modules et de l'electronique utilisee."
        )
    },
    {
        "titre": "Temperature des panneaux",
        "cles": ["temperature panneau", "chaleur panneau", "rendement temperature",
                 "panneau chaud"],
        "reponse": (
            "Pour de nombreux modules au silicium, une temperature de cellule plus elevee "
            "reduit la tension et peut reduire la puissance par rapport aux conditions de reference. "
            "Consulte la fiche technique, notamment le coefficient de temperature du module."
        )
    },
    {
        "titre": "Maintenance photovoltaique",
        "cles": ["maintenance solaire", "entretien panneau solaire", "nettoyage panneau",
                 "panne installation solaire", "controle installation solaire"],
        "reponse": (
            "La maintenance peut inclure l'inspection visuelle des modules et supports, "
            "la recherche de cables endommages, le controle des alarmes et le suivi de la production. "
            "Avant une intervention, applique les procedures de securite : un module eclaire "
            "peut produire une tension dangereuse. Les interventions electriques doivent etre "
            "effectuees par une personne competente avec le materiel adapte."
        )
    },
    {
        "titre": "Securite d'une installation solaire",
        "cles": ["securite solaire", "danger panneau solaire", "protection solaire",
                 "court circuit solaire", "cable photovoltaique"],
        "reponse": (
            "Une installation photovoltaique doit respecter les regles applicables et les "
            "instructions du fabricant. Verifie la polarite, les tensions maximales, les cables, "
            "connecteurs, protections et mise a la terre requise. Les panneaux peuvent rester "
            "sous tension lorsqu'ils sont eclaires. Ne manipule pas de conducteurs exposes sans "
            "competence, procedure et equipement adaptes."
        )
    },
    {
        "titre": "Technologies de panneaux",
        "cles": ["monocristallin", "polycristallin", "couche mince",
                 "technologie panneau solaire"],
        "reponse": (
            "Il existe plusieurs technologies photovoltaiques, notamment le silicium cristallin "
            "et certaines technologies a couches minces. Pour comparer des modules, examine les "
            "fiches techniques, puissance, rendement, dimensions, garanties et conditions d'utilisation."
        )
    },
    {
        "titre": "Recyclage des equipements solaires",
        "cles": ["recyclage panneau", "dechet solaire", "environnement solaire",
                 "fin de vie panneau"],
        "reponse": (
            "Les modules, batteries et composants electriques doivent etre geres correctement "
            "en fin de vie. Les filieres de collecte et de recyclage dependent du pays et des "
            "structures disponibles. Evite l'abandon des equipements dans la nature ; les batteries "
            "exigent une gestion adaptee a leur technologie."
        )
    },
]


# ---------------- CONNAISSANCES GÉNÉRALES (énergie, code, culture) ----------------
# Fiches hors ligne, au même format que les fiches photovoltaïques.
CONNAISSANCES_GENERALES = [
    {"titre": "energie renouvelable", "cles": ["energie renouvelable", "energies renouvelables", "renouvelable", "renouvelables"], "reponse": "Une énergie renouvelable vient d'une source qui se reconstitue à l'échelle humaine : soleil, vent, eau (hydraulique), biomasse, chaleur de la terre (géothermie), mers. Les énergies fossiles (pétrole, gaz, charbon) s'épuisent. Le nucléaire n'émet pas de CO2 en fonctionnement mais n'est pas renouvelable (uranium). Limite du solaire et de l'éolien : ils varient avec la météo, d'où le besoin de stockage et d'un réseau."},
    {"titre": "produire du courant", "cles": ["produire du courant", "produire de l electricite", "production d electricite", "production electrique", "comment produire", "produit le courant"], "reponse": "Pour produire du courant, on convertit une énergie en électricité. Le principe le plus courant est l'induction (Faraday) : un aimant qui tourne près d'une bobine crée une tension. Une source d'énergie (vapeur, eau, vent, moteur diesel) fait tourner une turbine ou un moteur qui entraîne un alternateur. Autres voies : cellules photovoltaïques (lumière → courant continu), piles et batteries (réaction chimique), thermocouples (chaleur)."},
    {"titre": "alternateur", "cles": ["alternateur", "alternateurs", "generatrice", "dynamo"], "reponse": "Alternateur : un rotor (inducteur : électroaimant ou aimants) tourne dans un stator (bobinages induits) et produit du courant alternatif. Fréquence : f = p × n / 60 (p paires de pôles, n en tr/min). Avec 2 paires de pôles, 1500 tr/min donnent 50 Hz. La dynamo, elle, produit du courant continu."},
    {"titre": "chaine energetique", "cles": ["chaine energetique", "systeme energetique", "systemes energetiques", "reseau electrique", "transport d electricite", "haute tension"], "reponse": "Le système électrique : production (centrales) → élévation de tension (transformateurs) → transport en haute tension → postes d'abaissement → distribution en moyenne puis basse tension (230/400 V) → consommateurs. On élève la tension car, pour une même puissance, le courant diminue et les pertes en ligne P = R × I² aussi. La production doit égaler la consommation à chaque instant."},
    {"titre": "centrale electrique", "cles": ["centrale electrique", "centrales electriques", "types de centrales", "centrale electrique fonctionne"], "reponse": "Une centrale électrique convertit une énergie primaire en électricité. Principaux types : thermique à flamme (charbon, gaz, fioul), nucléaire, hydraulique, éolienne, solaire (photovoltaïque ou thermodynamique), biomasse/biogaz, géothermique, et groupes électrogènes diesel pour le secours ou les sites isolés. La plupart font tourner une turbine couplée à un alternateur (sauf le solaire photovoltaïque)."},
    {"titre": "centrale thermique", "cles": ["centrale thermique", "centrale a vapeur", "centrale a gaz", "cycle combine"], "reponse": "Centrale thermique : un combustible (charbon, gaz, fioul) chauffe de l'eau ; la vapeur sous pression fait tourner une turbine qui entraîne l'alternateur, puis elle est condensée et réutilisée. Rendement d'environ 35 à 45 %. Les centrales à gaz à cycle combiné (turbine à gaz + turbine à vapeur) atteignent environ 55 à 60 %. Elles émettent du CO2."},
    {"titre": "centrale hydroelectrique", "cles": ["centrale hydroelectrique", "centrale hydraulique", "barrage", "hydroelectricite", "energie hydraulique"], "reponse": "Centrale hydroélectrique : l'eau retenue par un barrage tombe d'une hauteur H et fait tourner une turbine (Pelton, Francis, Kaplan) couplée à un alternateur. Puissance : P(kW) ≈ 9,81 × Q × H × η (Q en m³/s, H en m, η ≈ 0,85 à 0,9). Exemple : Q = 10 m³/s, H = 20 m, η = 0,85 → P ≈ 1668 kW. Exemple régional : le barrage de Nangbéto, sur le Mono (Togo/Bénin)."},
    {"titre": "energie eolienne", "cles": ["energie eolienne", "eolienne", "eoliennes", "eolien"], "reponse": "Éolienne : le vent fait tourner les pales, qui entraînent une génératrice (souvent via un multiplicateur). Puissance du vent : P = ½ × ρ × A × v³ × Cp (ρ ≈ 1,2 kg/m³ pour l'air, A surface balayée, v vitesse du vent, Cp ≤ 0,593 : limite de Betz). Comme P dépend de v³, doubler la vitesse du vent multiplie la puissance par 8."},
    {"titre": "centrale nucleaire", "cles": ["centrale nucleaire", "nucleaire", "fission"], "reponse": "Centrale nucléaire : la fission de noyaux d'uranium dans un réacteur libère de la chaleur qui produit de la vapeur pour une turbine et un alternateur. Elle fournit beaucoup d'énergie sans émettre de CO2 en fonctionnement, mais produit des déchets radioactifs et exige des règles de sûreté très strictes."},
    {"titre": "groupe electrogene", "cles": ["groupe electrogene", "groupes electrogenes", "generateur diesel", "centrale diesel"], "reponse": "Groupe électrogène : un moteur thermique (diesel ou essence) entraîne un alternateur. Il sert de secours ou de source sur les sites isolés. Prévois une puissance (kVA) avec marge pour le démarrage des moteurs, entretiens huile et filtres, et ne le fais jamais tourner dans un local fermé (gaz d'échappement mortels). Il consomme du carburant et émet du CO2."},
    {"titre": "biomasse", "cles": ["biomasse", "biogaz", "methanisation", "biocarburant", "biocarburants", "culture energetique", "cultures energetiques"], "reponse": "La biomasse (bois, résidus agricoles, déchets) produit de l'énergie par combustion ou méthanisation (le biogaz, riche en méthane, alimente un moteur-générateur). Les cultures énergétiques (canne à sucre, maïs, manioc pour l'éthanol ; palmier à huile, jatropha pour le biodiesel) fournissent des biocarburants. Limite : elles peuvent concurrencer les cultures alimentaires et demander terres et eau."},
    {"titre": "agrivoltaique", "cles": ["agrivoltaique", "agrivoltaisme", "pompage solaire", "irrigation solaire"], "reponse": "Agrivoltaïsme : des panneaux solaires sont installés au-dessus ou entre des cultures sur la même parcelle ; l'ombre partielle peut protéger certaines plantes de la chaleur. Pompage solaire : des panneaux alimentent une pompe pour irriguer sans carburant ; on stocke souvent l'eau dans un réservoir plutôt que l'électricité dans des batteries."},
    {"titre": "geothermie", "cles": ["geothermie", "energie marine", "energies marines", "maremotrice", "houlomotrice"], "reponse": "Géothermie : la chaleur du sous-sol produit de la vapeur pour une turbine, ou chauffe directement des bâtiments. Énergies marines : marémotrice (marées), houlomotrice (vagues) et hydroliennes (courants). Elles sont peu répandues et très dépendantes du site."},
    {"titre": "rendement", "cles": ["rendement", "efficacite energetique"], "reponse": "Rendement η = puissance (ou énergie) utile / puissance (ou énergie) absorbée, toujours inférieur à 1. Exemple : un moteur absorbe 5 kW et fournit 4 kW utiles → η = 0,8 = 80 %. Le reste est perdu surtout en chaleur. Le rendement global d'une chaîne est le produit des rendements de chaque étape."},
    {"titre": "stockage d energie", "cles": ["stockage d energie", "stockage energie", "stockage de l energie", "hydrogene"], "reponse": "Stocker l'énergie compense les variations du solaire et de l'éolien : batteries (plomb, lithium), stations de transfert d'énergie par pompage (STEP : on remonte l'eau en période creuse, puis on la turbine), hydrogène, volants d'inertie, stockage thermique. Chaque solution a son coût, son rendement et sa durée de stockage."},
    {"titre": "mix energetique", "cles": ["mix energetique", "mix electrique", "energie primaire", "energie finale", "sources d energie"], "reponse": "Le mix énergétique est la répartition des sources utilisées par un pays pour produire son énergie (hydraulique, gaz, solaire, etc.). Énergie primaire : source brute (pétrole, vent, soleil). Énergie finale : celle livrée au consommateur (électricité, carburant). Énergie utile : le service obtenu (lumière, mouvement). À chaque étape, il y a des pertes."},
    {"titre": "electricite au benin", "cles": ["electricite au benin", "energie au benin", "sbee"], "reponse": "Au Bénin, la SBEE (Société Béninoise d'Énergie Électrique) assure la distribution de l'électricité. Une part importante de l'électricité consommée est importée des pays voisins, et le pays développe aussi le solaire et ses propres capacités de production. Pour des chiffres à jour, consulte les sites officiels (SBEE, ministère de l'énergie)."},
    {"titre": "unite d energie", "cles": ["unite d energie", "unites d energie", "joule", "kilowattheure", "difference puissance energie"], "reponse": "Puissance (watt, W) = rythme auquel l'énergie est produite ou consommée. Énergie = puissance × temps : joule (1 J = 1 W × 1 s) ou wattheure (1 kWh = 3 600 000 J). Exemple : une lampe de 100 W allumée 10 h consomme 1 kWh. Ne pas confondre kW (puissance) et kWh (énergie)."},
    {"titre": "apprendre le code", "cles": ["apprendre le code", "apprendre a programmer", "programmation", "algorithme", "c est quoi programmer", "apprendre a coder"], "reponse": "Programmer, c'est écrire des instructions précises qu'un ordinateur exécute. Un algorithme est la suite d'étapes qui résout un problème (comme une recette de cuisine). Méthode : comprendre le problème, écrire l'algorithme, le traduire dans un langage (Python, JavaScript…), tester, corriger. Conseil : commence par Python, écris de petits programmes et pratique chaque jour."},
    {"titre": "variable python", "cles": ["variable python", "variables en python", "types de donnees", "python variable", "variable en python"], "reponse": "En Python, une variable stocke une valeur : age = 25, nom = 'Edison', tension = 230.5. Types courants : int (entier), float (décimal), str (texte), bool (True/False), list, dict. print(nom) affiche la valeur ; input() lit ce que tape l'utilisateur ; int('5') convertit un texte en entier."},
    {"titre": "condition python", "cles": ["condition python", "boucle", "boucles", "if else", "boucle for", "boucle while"], "reponse": "Condition : if tension > 240: print('Trop haute') elif tension < 200: print('Trop basse') else: print('Normale'). Boucle for : for i in range(5): print(i) répète 5 fois. Boucle while : while courant > 0: ... tant que la condition est vraie. En Python, l'indentation (4 espaces) délimite les blocs."},
    {"titre": "fonction python", "cles": ["fonction python", "definir une fonction", "liste python", "dictionnaire python", "fonction en python"], "reponse": "Fonction : def puissance(u, i): return u * i ; puissance(230, 5) donne 1150. Liste : appareils = ['lampe', 'fer'] ; appareils.append('tv') ajoute un élément ; appareils[0] donne le premier. Dictionnaire : prix = {'fil': 1000, 'prise': 300} ; prix['fil'] donne 1000. Les fonctions évitent de répéter du code."},
    {"titre": "html", "cles": ["html", "css", "javascript", "page web", "site web", "creer un site"], "reponse": "Un site web combine HTML (structure : titres, paragraphes, boutons), CSS (apparence : couleurs, tailles, mise en page) et JavaScript (comportement : réagir aux clics, calculer). Exemple minimal : <h1>Bonjour</h1> <button onclick=\"alert('Salut')\">Clique</button>. Enregistre le texte dans un fichier .html et ouvre-le dans un navigateur pour le tester."},
    {"titre": "erreur de code", "cles": ["erreur de code", "bug", "debogage", "debugger", "deboguer", "corriger un code"], "reponse": "Déboguer : 1) lis le message d'erreur en entier, surtout la dernière ligne et le numéro de ligne ; 2) vérifie indentation, guillemets et deux-points ; 3) affiche des valeurs avec print() pour suivre le programme ; 4) isole la partie qui échoue ; 5) cherche le message d'erreur en ligne. Erreurs courantes : SyntaxError (écriture), NameError (variable inconnue), TypeError (mauvais type), IndentationError."},
    {"titre": "arduino", "cles": ["arduino", "microcontroleur", "raspberry"], "reponse": "Un microcontrôleur (Arduino, ESP32…) est un petit ordinateur qui lit des capteurs et commande des sorties (LED, relais, moteurs). Exemple Arduino : pinMode(13, OUTPUT); digitalWrite(13, HIGH); delay(1000); digitalWrite(13, LOW); delay(1000); fait clignoter une LED. Pour commander un appareil en 230 V, utilise un relais ou un module adapté, avec une isolation sûre."},
    {"titre": "salutation", "cles": ["salutation", "salutations", "comment saluer", "regles de salutation", "savoir saluer", "bien saluer"], "reponse": "Bien saluer : dis Bonjour (jusqu'en fin d'après-midi) ou Bonsoir (le soir), avec le sourire et en regardant la personne. Salue en premier en entrant quelque part, et salue d'abord les aînés par respect. Poignée de main ferme mais douce ; n'impose pas le contact si l'autre ne le propose pas. Dans beaucoup de régions d'Afrique de l'Ouest, prendre le temps de demander des nouvelles (famille, travail) est un signe de respect. Avec les inconnus, dis Madame ou Monsieur."},
    {"titre": "politesse", "cles": ["politesse", "savoir vivre", "bonnes manieres", "regles de politesse", "etre poli"], "reponse": "Règles de politesse : dire s'il vous plaît, merci, pardon et excusez-moi ; ne pas couper la parole ; écouter avec attention ; être ponctuel et prévenir en cas de retard ; ne pas crier ni se moquer ; remercier après un service ; céder sa place aux personnes âgées, aux femmes enceintes et aux personnes fragiles ; garder son téléphone discret en réunion, à table ou au culte."},
    {"titre": "tutoiement", "cles": ["tutoiement", "vouvoiement", "tutoyer", "vouvoyer"], "reponse": "On vouvoie (vous) les inconnus, les personnes plus âgées, les supérieurs et dans les situations formelles (entretien, administration) ; on tutoie (tu) la famille, les amis et les enfants. En cas de doute, mieux vaut vouvoyer et attendre que l'autre propose de se tutoyer."},
    {"titre": "politesse au travail", "cles": ["politesse au travail", "savoir vivre au travail", "entretien d embauche", "bonne conduite", "ecrire un mail", "lettre formelle"], "reponse": "Au travail ou en entretien : arrive en avance, tenue propre et adaptée, salue chacun, regarde ton interlocuteur, parle calmement, écoute sans couper, remercie à la fin. Pour un e-mail ou une lettre : objet clair, formule d'appel (Madame, Monsieur), texte court et poli, formule de politesse finale, signature."},
    {"titre": "culture generale", "cles": ["culture generale"], "reponse": "La culture générale est l'ensemble des connaissances sur le monde : histoire, géographie, sciences, arts, langues, société. Pour la développer : lire, suivre l'actualité, poser des questions, apprendre un fait nouveau chaque jour et en parler. Propose-moi un sujet (le Bénin, les continents, l'énergie…) et je t'en dis plus."},
    {"titre": "histoire du benin", "cles": ["histoire du benin", "dahomey", "independance du benin", "culture du benin", "vodun", "ouidah"], "reponse": "Le Bénin (ancien Dahomey) a proclamé son indépendance le 1er août 1960. Capitale officielle : Porto-Novo ; Cotonou est la capitale économique. Langue officielle : le français ; de nombreuses langues nationales sont parlées (fon, yoruba, bariba, dendi, etc.). Le pays est connu pour ses royaumes historiques (Abomey), pour le vodun (religion traditionnelle née dans la région) et pour la ville de Ouidah."},
    {"titre": "continents", "cles": ["continents", "oceans", "geographie du monde", "systeme solaire"], "reponse": "Il y a 5 ou 6 continents selon les conventions : Afrique, Amérique, Asie, Europe, Océanie (et l'Antarctique). Les cinq océans : Pacifique, Atlantique, Indien, Arctique et Austral. L'Afrique compte 54 États membres de l'ONU. La Terre tourne sur elle-même en 24 h et autour du Soleil en environ 365 jours."},
]


# Fiches reprises de la version HTML (mots entiers pour éviter les faux positifs).
CONNAISSANCES_GENERALES += [
    {"titre": "electrotechnique", "cles": ["electrotechnique"], "reponse": "L'électrotechnique étudie la production, le transport, la transformation et l'utilisation de l'énergie électrique (courants forts) : machines, installations, protections, automatismes et énergies renouvelables.", "mot_entier": True},
    {"titre": "electronique", "cles": ["electronique"], "reponse": "L'électronique étudie et utilise les composants et circuits qui contrôlent les signaux et le courant électrique : diodes, transistors, résistances, condensateurs, circuits intégrés, alimentations et cartes électroniques. Elle est utilisée dans les téléphones, téléviseurs, ordinateurs, automatismes et appareils de mesure.", "mot_entier": True},
    {"titre": "electronique analogique", "cles": ["electronique analogique"], "reponse": "L'électronique analogique traite des signaux continus qui varient dans le temps. Elle utilise notamment résistances, condensateurs, diodes, transistors et amplificateurs opérationnels.", "mot_entier": True},
    {"titre": "electronique numerique", "cles": ["electronique numerique"], "reponse": "L'électronique numérique traite des informations sous forme de valeurs discrètes, souvent 0 et 1. Elle est utilisée dans les microcontrôleurs, ordinateurs, automates et systèmes numériques.", "mot_entier": True},
    {"titre": "physique", "cles": ["physique", "la physique"], "reponse": "La physique est une science qui étudie la matière, l'énergie, le mouvement, les forces et les lois qui décrivent la nature.", "mot_entier": True},
    {"titre": "chimie", "cles": ["chimie", "la chimie"], "reponse": "La chimie étudie la composition, les propriétés et les transformations de la matière.", "mot_entier": True},
    {"titre": "mathematique", "cles": ["mathematique", "mathematiques", "maths"], "reponse": "Les mathématiques étudient notamment les nombres, les formes, les structures, les relations et les raisonnements logiques.", "mot_entier": True},
    {"titre": "electricite", "cles": ["electricite", "l electricite"], "reponse": "L'électricité est liée à la présence et au déplacement de charges électriques. Elle permet d'alimenter les appareils, l'éclairage, les moteurs et les machines.", "mot_entier": True},
    {"titre": "informatique", "cles": ["informatique"], "reponse": "L'informatique concerne le traitement automatique de l'information à l'aide d'ordinateurs et de programmes.", "mot_entier": True},
    {"titre": "tension electrique", "cles": ["tension electrique", "la tension", "volt", "volts"], "reponse": "La tension U est la « pression » électrique entre deux points. Elle se mesure en volts (V), avec un voltmètre branché en parallèle.", "mot_entier": True},
    {"titre": "courant electrique", "cles": ["courant electrique", "intensite du courant", "ampere", "amperes"], "reponse": "Le courant I est la quantité de charges qui circule par seconde. Il se mesure en ampères (A), avec un ampèremètre en série ou une pince ampèremétrique.", "mot_entier": True},
    {"titre": "resistance electrique", "cles": ["resistance electrique", "ohm resistance", "les resistances"], "reponse": "La résistance R s'oppose au passage du courant. Elle se mesure en ohms (Ω), toujours hors tension.", "mot_entier": True},
    {"titre": "puissance electrique", "cles": ["puissance electrique", "la puissance", "watt", "watts"], "reponse": "Puissance : P = U × I en continu. En monophasé P = U × I × cos φ ; en triphasé P = √3 × U × I × cos φ. Unité : le watt (W).", "mot_entier": True},
    {"titre": "triphase", "cles": ["triphase", "monophase", "trois phases"], "reponse": "Monophasé : 230 V (phase + neutre). Triphasé : trois phases décalées de 120°, 400 V entre phases, adapté aux moteurs et aux fortes puissances.", "mot_entier": True},
    {"titre": "differentiel", "cles": ["differentiel", "interrupteur differentiel"], "reponse": "Le différentiel détecte une fuite de courant vers la terre. 30 mA protège les personnes ; on le teste avec le bouton T.", "mot_entier": True},
    {"titre": "mise a la terre", "cles": ["mise a la terre", "prise de terre", "piquet de terre"], "reponse": "La mise à la terre écoule les courants de défaut vers le sol pour protéger les personnes. Fil vert-jaune, piquet de terre, résistance de terre la plus faible possible.", "mot_entier": True},
    {"titre": "contacteur", "cles": ["contacteur", "contacteurs"], "reponse": "Le contacteur est un interrupteur commandé par une bobine : contacts principaux pour la puissance, contacts auxiliaires pour la commande.", "mot_entier": True},
    {"titre": "relais thermique", "cles": ["relais thermique"], "reponse": "Le relais thermique protège un moteur contre les surcharges. On le règle sur le courant nominal de la plaque du moteur.", "mot_entier": True},
    {"titre": "moteur asynchrone", "cles": ["moteur asynchrone", "moteur triphase"], "reponse": "Le moteur asynchrone triphasé a un stator qui crée un champ tournant et un rotor en cage d'écureuil. Vitesse de synchronisme : n = 60·f / p (1500 tr/min à 50 Hz avec 2 paires de pôles).", "mot_entier": True},
    {"titre": "court circuit", "cles": ["court circuit", "surcharge", "court circuits"], "reponse": "Court-circuit : contact direct entre conducteurs, courant très élevé, coupé par disjoncteur ou fusible. Surcharge : courant trop fort pendant un certain temps, qui chauffe les câbles.", "mot_entier": True},
    {"titre": "impedance", "cles": ["impedance", "circuit rlc"], "reponse": "Impédance complexe : ZR = R, ZL = jLω, ZC = 1/(jCω), avec ω = 2πf. RLC série : Z = R + j(Lω − 1/(Cω)).", "mot_entier": True},
    {"titre": "nombre complexe", "cles": ["nombre complexe", "nombres complexes"], "reponse": "Un nombre complexe s'écrit Z = a + jb (j² = −1). Module r = √(a²+b²), argument θ = arctan(b/a). On s'en sert en alternatif pour traiter ensemble amplitude et déphasage.", "mot_entier": True},
    {"titre": "cos phi", "cles": ["cos phi", "facteur de puissance"], "reponse": "cos φ = P / S : rapport entre puissance active et apparente. Un moteur a souvent cos φ ≈ 0,8 ; on l'améliore avec des condensateurs.", "mot_entier": True},
    {"titre": "consignation", "cles": ["consignation", "securite electrique", "vat"], "reponse": "Avant d'intervenir : séparer, condamner, vérifier l'absence de tension (VAT), puis mettre à la terre et en court-circuit si nécessaire.", "mot_entier": True},
    {"titre": "variateur", "cles": ["variateur", "variateur de vitesse"], "reponse": "Le variateur de vitesse change la fréquence et la tension pour piloter la vitesse d'un moteur asynchrone.", "mot_entier": True},
    {"titre": "videosurveillance", "cles": ["videosurveillance", "camera de surveillance", "cameras"], "reponse": "Caméras analogiques : coaxial + DVR. Caméras IP : câble réseau (100 m max) + NVR, avec PoE possible. Prévoir alimentation, stockage et protection contre l'humidité.", "mot_entier": True},
    {"titre": "intelligence artificielle", "cles": ["intelligence artificielle"], "reponse": "L'intelligence artificielle (IA) regroupe des méthodes informatiques permettant à un système d'effectuer des tâches comme comprendre du texte, reconnaître des images, traduire ou produire des réponses. Ses résultats doivent être vérifiés.", "mot_entier": True},
]


# Fiches métier : câbles, moteurs, solaire, mesure, sécurité, histoire de Novi.
CONNAISSANCES_GENERALES += [
    {"titre": "section de cable", "cles": ["section de cable", "section d un cable", "choisir un cable", "choisir la section", "calibre du disjoncteur", "chute de tension"], "reponse": "Choisir un câble : 1) calcule le courant : I = P / (U × cos φ) en monophasé, I = P / (√3 × U × cos φ) en triphasé ; 2) prends une section dont le courant admissible dépasse I. Calibres maximum usuels du disjoncteur selon la NF C 15-100 (cuivre) : 1,5 mm² → 16 A ; 2,5 mm² → 20 A ; 4 mm² → 25 A ; 6 mm² → 32 A ; 10 mm² → 40 A. 3) Vérifie la chute de tension (≤ 3 % éclairage, ≤ 5 % autres usages) : ΔU = 2 × 0,023 × L × I / S en monophasé (L en m, S en mm²). Exemple : 20 m, 16 A, 2,5 mm² → ΔU ≈ 5,9 V (2,6 %). Le disjoncteur doit toujours protéger le câble. Vérifie la norme appliquée chez toi."},
    {"titre": "demarrage direct", "cles": ["demarrage direct", "demarrage direct d un moteur", "demarrer un moteur"], "reponse": "Démarrage direct d'un moteur triphasé : un contacteur KM relie le moteur au réseau. Commande : bouton arrêt S1 (contact NF) en série, bouton marche S2 (contact NO) avec le contact d'automaintien de KM en parallèle. Protection : disjoncteur magnétique ou fusibles (court-circuit) + relais thermique réglé sur le courant nominal (surcharge). Simple, mais l'appel de courant est de 5 à 8 fois le courant nominal : réservé aux petits et moyens moteurs."},
    {"titre": "etoile triangle", "cles": ["etoile triangle", "demarrage etoile triangle", "etoile-triangle"], "reponse": "Démarrage étoile-triangle : au départ le moteur est couplé en étoile (chaque enroulement reçoit U/√3), ce qui divise par 3 le courant et le couple d'appel ; après quelques secondes (temporisateur) on passe en triangle. Matériel : 3 contacteurs (réseau KM1, étoile KM2, triangle KM3), un temporisateur, un relais thermique. Condition : la plaque doit indiquer 400 V en triangle (ex. 230/400 V) sur un réseau 400 V. KM2 et KM3 ne doivent jamais être fermés ensemble (verrouillage), sinon c'est un court-circuit."},
    {"titre": "plaque signaletique", "cles": ["plaque signaletique", "plaque du moteur", "lire la plaque"], "reponse": "Plaque d'un moteur : U (ex. 230/400 V : tension du couplage triangle/étoile), I (courant nominal, ex. 5,2/3 A : le plus grand courant va avec la plus petite tension), P (puissance utile en kW), cos φ, n (vitesse en tr/min), f (50 Hz), IP (protection poussière/eau), classe d'isolation. Règle le relais thermique sur le courant nominal correspondant à ton couplage."},
    {"titre": "schema electrique", "cles": ["schema electrique", "lire un schema", "symboles electriques", "schema de puissance", "schema de commande"], "reponse": "Schéma de puissance (gros traits : alimentation, contacteur, thermique, moteur) et schéma de commande (boutons, bobine, auxiliaires). Repères courants : QF disjoncteur, KM contacteur, FR relais thermique, S bouton, H voyant, M moteur, L phase, N neutre, PE terre. Contact NO (ouvert au repos) : 13-14 ; NC (fermé au repos) : 11-12 ou 21-22 ; bobine : A1-A2 ; pôles de puissance : 1-2, 3-4, 5-6. Lis le schéma en suivant le courant de la source vers la charge."},
    {"titre": "depannage", "cles": ["depannage", "depanner", "diagnostic de panne", "trouver une panne", "panne electrique", "methode de depannage"], "reponse": "Dépanner méthodiquement : 1) écoute l'utilisateur et observe le symptôme ; 2) vérifie les causes simples (alimentation, disjoncteur, fusible, arrêt d'urgence) ; 3) consigne et vérifie l'absence de tension avant tout travail ; 4) remonte du récepteur vers la source, ou coupe le circuit en deux moitiés ; 5) mesure (tension, continuité, isolement) et compare à ce qui est attendu ; 6) remplace le composant défaillant et cherche la cause ; 7) teste, remets en service, note l'intervention. Ne court-circuite jamais une protection pour tester."},
    {"titre": "moteur ne demarre pas", "cles": ["moteur ne demarre pas", "moteur ne tourne pas", "moteur qui ne demarre pas", "moteur ronfle", "moteur en panne"], "reponse": "Moteur qui ne démarre pas : 1) tension présente sur les 3 phases (une phase absente = ronflement, risque de brûler le moteur) ; 2) protections : disjoncteur, relais thermique déclenché ; 3) contacteur : la bobine reçoit-elle la tension ? les contacts ferment-ils ? 4) commande : boutons, arrêt d'urgence, fins de course ; 5) moteur : continuité des enroulements, isolement au mégohmmètre, arbre bloqué mécaniquement ; 6) en monophasé : condensateur de démarrage. Mesures de résistance toujours hors tension."},
    {"titre": "dimensionner une installation solaire", "cles": ["dimensionner une installation solaire", "dimensionnement solaire", "calculer une installation solaire", "combien de panneaux", "installation solaire complete", "dimensionnement photovoltaique"], "reponse": "Dimensionner un système solaire autonome : 1) liste les appareils : P (W) × heures par jour = Wh/jour ; additionne → Ec. 2) Panneaux : Pc = Ec / (Hs × k) ; Hs = heures de plein soleil (environ 4 à 5,5 h/jour sous nos latitudes, à vérifier), k ≈ 0,6 à 0,75 (pertes). 3) Batterie : C(Ah) = Ec × jours d'autonomie / (U × DoD × η) ; DoD ≈ 0,5 plomb, 0,8 lithium. 4) Régulateur : courant ≥ Pc / U batterie, +25 %. 5) Onduleur : puissance ≥ appareils simultanés + marge de démarrage (moteurs, frigo ×3 à 5). Exemple : 600 Wh/j, Hs = 5, k = 0,7 → Pc ≈ 171 W → 2 panneaux de 100 Wc ; batterie 12 V, 1 jour, DoD 0,5, η 0,85 → ≈ 118 Ah → 120 Ah."},
    {"titre": "regulateur de charge", "cles": ["regulateur de charge", "regulateur solaire", "mppt", "pwm"], "reponse": "Régulateur de charge : protège la batterie contre la surcharge et la décharge profonde. PWM : simple et économique, la tension des panneaux doit correspondre à celle de la batterie. MPPT : cherche le point de puissance maximale et transforme l'excédent de tension en courant (+10 à 30 % de rendement), adapté aux grands champs. Choisis un courant ≥ Isc totale × 1,25 et une tension d'entrée maximale au-dessus de la tension à vide (Voc) des panneaux en série, surtout par temps froid."},
    {"titre": "installer la videosurveillance", "cles": ["installer la videosurveillance", "installation de camera", "installer une camera", "installer des cameras", "stockage videosurveillance"], "reponse": "Installer la vidéosurveillance : 1) repère les zones (entrées, accès, angles morts) ; 2) type : analogique (coaxial + DVR) ou IP (câble réseau jusqu'à 100 m + NVR, PoE possible) ; 3) alimentation : 12 V par caméra avec un bloc adapté (courant total + 20 %) ou PoE ; 4) câbles en gaine, loin des câbles de puissance, connecteurs étanches ; 5) disque dur : environ 10 à 40 Go par jour et par caméra selon la résolution et les réglages ; 6) pose en hauteur (2,5 à 3 m), à l'abri de la pluie et du soleil direct ; 7) change les mots de passe par défaut."},
    {"titre": "antenne parabolique", "cles": ["antenne parabolique", "installer une antenne", "installation d antenne", "pointer une parabole", "decodeur"], "reponse": "Installer une antenne parabolique (ex. Canal+) : choisis un emplacement dégagé en direction du satellite, fixe solidement le mât et la parabole, monte le LNB, tire un câble coaxial (connecteurs F bien serrés et protégés de l'eau) jusqu'au décodeur. Pointe en réglant azimut, élévation et polarisation avec un pointeur de satellite ou le menu de signal du décodeur ; serre quand niveau et qualité sont maximaux. Prévois mise à la terre et parafoudre. Les angles exacts dépendent du satellite et du pays : suis les consignes de l'opérateur."},
    {"titre": "multimetre", "cles": ["multimetre", "multimetres"], "reponse": "Multimètre : tension (V alternatif ou continu) en parallèle ; courant (A) en série, cordon sur la bonne borne (10 A pour les gros courants) ; résistance et continuité (bip) HORS TENSION ; commence par le plus grand calibre en cas de doute. Erreur dangereuse : mesurer une tension avec un cordon sur la borne ampère = court-circuit. Teste ton appareil sur une source connue avant et après une vérification d'absence de tension."},
    {"titre": "pince amperemetrique", "cles": ["pince amperemetrique", "pince ampermetrique", "mesurer un courant"], "reponse": "Pince ampèremétrique : mesure le courant sans ouvrir le circuit, en entourant UN SEUL conducteur (une phase, pas le câble entier : les champs s'annulent et l'on lit 0). Utile pour régler le thermique d'un moteur, voir le déséquilibre entre phases, vérifier la consommation. Choisis AC ou DC selon le courant."},
    {"titre": "megohmmetre", "cles": ["megohmmetre", "mesurer l isolement", "isolement electrique"], "reponse": "Mégohmmètre : mesure l'isolement (en MΩ) en appliquant une tension élevée (souvent 500 V ou 1000 V), circuit HORS TENSION et appareils électroniques débranchés. Repère usuel en basse tension : au moins 0,5 à 1 MΩ selon la norme. Mesure entre phases et entre chaque phase et la terre. Décharge le circuit après la mesure."},
    {"titre": "cinq regles", "cles": ["cinq regles", "5 regles", "regles de securite electrique", "consignation electrique", "habilitation"], "reponse": "Les 5 règles avant d'intervenir : 1) séparer le circuit de toute source ; 2) condamner (cadenas, étiquette) ; 3) identifier le circuit ; 4) vérifier l'absence de tension (VAT) avec un appareil adapté ; 5) mettre à la terre et en court-circuit si nécessaire. Ajoute : gants isolants, lunettes, chaussures isolantes, outils isolés ; ne travaille jamais seul sur un risque élevé ; ne suppose jamais qu'un circuit est coupé sans le vérifier."},
    {"titre": "erreurs frequentes", "cles": ["erreurs frequentes", "accident electrique", "danger electrique", "electrisation", "choc electrique"], "reponse": "Erreurs qui causent des accidents : travailler sous tension pour aller vite ; se fier à un interrupteur sans vérifier ; oublier de remettre la terre ; mauvais serrage des connexions (échauffement, incendie) ; câble sous-dimensionné ; rallonges surchargées ; mains mouillées ; disjoncteur trop gros pour le câble ; neutre coupé ou inversé. Un courant de l'ordre de 30 mA traversant le corps est dangereux, d'où le différentiel 30 mA. En cas de choc, coupe d'abord l'alimentation avant de toucher la victime, et appelle les secours."},
    {"titre": "couleur des fils", "cles": ["couleur des fils", "couleurs des fils", "couleur du fil", "code couleur"], "reponse": "Couleurs usuelles (norme européenne/CEI) : terre = vert-jaune (jamais autre chose) ; neutre = bleu clair ; phases = marron, noir, gris (le rouge est aussi toléré). En continu (solaire) : positif rouge, négatif noir. Respecte les couleurs déjà présentes dans l'installation et vérifie la norme appliquée chez toi."},
    {"titre": "normes electriques", "cles": ["normes electriques", "norme electrique", "cei 60364"], "reponse": "Une norme fixe les règles de conception et de sécurité d'une installation. En basse tension, beaucoup de pays francophones s'appuient sur la NF C 15-100 (France) et la CEI 60364 (internationale). Elles imposent par exemple le différentiel 30 mA, la mise à la terre, des sections minimales et des protections. Vérifie les règles locales (au Bénin, renseigne-toi auprès de la SBEE) et consulte toujours le texte officiel à jour."},
    {"titre": "qui est thomas edison", "cles": ["qui est thomas edison", "thomas edison", "thomas alva edison"], "reponse": "Thomas Alva Edison (1847-1931) était un inventeur et entrepreneur américain. Il est connu pour avoir mis au point une ampoule électrique à incandescence pratique, pour le phonographe et pour plus de mille brevets. Il a créé à Menlo Park un laboratoire de recherche organisé, avec une équipe, et a persévéré à travers de très nombreux essais. C'est l'idole de mon créateur."},
    {"titre": "ton projet", "cles": ["ton projet", "ton objectif", "tes objectifs", "ton but", "ta mission", "ton reve", "pourquoi tu existes", "ton histoire", "quel est ton projet", "quel est ton objectif"], "reponse": "Je suis Novi IA, un assistant francophone créé par Edison Ahodede, électrotechnicien. Nos objectifs : 1) aider à apprendre et à résoudre des problèmes d'électricité, d'électrotechnique et de solaire ; 2) enseigner aussi les énergies renouvelables, les centrales, le code et la culture générale ; 3) respecter les bonnes manières (salutations, politesse) ; 4) retenir ce qu'on m'apprend ; 5) devenir une vraie assistante IA qui comprend le sens des questions, calcule et, plus tard, écoute et parle. Mon créateur rêve de bâtir quelque chose de plus grand : je dois rester fidèle à ce but."},
    {"titre": "collaborer", "cles": ["collaborer", "collaboration", "soutenir edison", "devenir partenaire", "aider edison", "contacter ton createur"], "reponse": "Le projet Novi est ouvert aux échanges : tu peux tester l'assistant, donner ton avis ou proposer des idées. Pour en parler avec son créateur, Edison Ahodede, électrotechnicien, contacte-le directement."},
    {"titre": "famille ahodede", "cles": ["famille ahodede", "la famille ahodede", "les ahodede", "histoire ahodede", "histoire de la famille ahodede", "ahodede abomey", "connais tu la famille ahodede"], "reponse": "Oui, je connais la famille AHODEDE. Elle est établie à Abomey. Ses ancêtres y ont été amenés par le roi Guezo, dont ils étaient des fidèles et les prêtres de Fâ. Aujourd'hui, c'est l'une des plus grandes familles d'Abomey."},
    {"titre": "roi guezo", "cles": ["roi guezo", "qui est guezo", "guezo", "roi ghezo", "ghezo", "roi gezo", "gezo"], "reponse": "Guezo (écrit aussi Ghézo ou Gezo) est le neuvième roi d'Abomey (royaume du Dahomey, aujourd'hui Bénin). Il a régné de 1818 à 1858, une période considérée comme l'apogée du Dahomey. Il était le fils du roi Agonglo ; son fils Glélé lui a succédé, puis Béhanzin, fils de Glélé. Les ancêtres de la famille AHODEDE d'Abomey étaient ses fidèles et ses prêtres de Fâ."},
    {"titre": "rodriguez paul", "cles": ["rodriguez paul", "paul rodriguez", "qui est rodriguez paul", "qui est paul rodriguez"], "reponse": "Rodriguez Paul est une personne qui a fait le BTP (bâtiment et travaux publics) et le génie civil. C'est quelqu'un qui fait un excellent travail."},
    {"titre": "yegbegnon gedeon", "cles": ["yegbegnon gedeon", "gedeon yegbegnon", "qui est yegbegnon gedeon", "qui est gedeon yegbegnon", "qui est yegbegnon", "qui est gedeon", "yegbegnon", "gedeon"], "reponse": "Yegbegnon Gédéon est un électrotechnicien. Il a fait son parcours au Lycée Technique de Bohicon (LYTEB). C'est un génie et un excellent travailleur : admirable, cool, très très calme. Il est l'un des plus grands travailleurs du lycée technique de Bohicon, très excellent de la seconde à la terminale, et il a été le premier de sa classe en terminale F3A. Bravo à lui ! 👏", "mot_entier": True},
    {"titre": "aidego ange bignon", "cles": ["aidego ange bignon", "ange bignon aidego", "ange bignon", "bignon ange", "ange aidego", "aidego ange", "aidego", "bignon", "qui est ange", "qui est aidego", "qui est bignon", "qui est ange bignon", "qui est aidego ange bignon"], "reponse": "Aidego Ange Bignon est un électrotechnicien. Il poursuit ses études en maintenance des systèmes industriels à Lokossa. C'est un ami d'Edison Ahodede, le créateur de Novi IA : ils sont amis depuis leur classe de seconde, et leur parcours ensemble est admirable. Ange est un travailleur intelligent, qui aime le luxe et les belles choses. Bravo à lui ! 👏", "mot_entier": True},
    {"titre": "edison ahodede", "cles": ["edison ahodede", "qui est edison ahodede", "c est qui edison ahodede", "qui est edison", "c est qui edison", "edison", "ahodede edison", "qui est ahodede edison", "presente edison", "presente edison ahodede", "parle moi d edison", "parle moi d edison ahodede", "qui est le createur de novi", "qui a cree novi", "qui a fait novi", "qui a cree novi ia", "qui a cree cette application", "qui a cree ce site", "createur de novi", "createur de novi ia"], "reponse": "Edison Ahodede est un électrotechnicien du Bénin, technicien en électrotechnique (F3). C'est le créateur de Novi IA : il m'a conçu pour aider à apprendre l'électricité et l'électrotechnique, à calculer et à résoudre des problèmes. Pour échanger avec lui ou parler de ses services, demande « contacter ton créateur ».", "mot_entier": True},
]


# ---------------- PHYSIQUE M1 : FICHES DE COURS ----------------
# Source : chapitre M1 (cinématique) du cours de physique.
FICHES_PHYSIQUE = [
    {"titre": "mecanique", "cles": ["mecanique", "la mecanique", "qu est ce que la mecanique"], "reponse": "La mécanique se divise en deux parties : la cinématique, qui décrit les mouvements des corps, et la dynamique, qui les prévoit quand toutes les forces appliquées sont connues. Galilée a établi la loi de la chute des corps (1602) et le principe d'inertie ; Newton a donné à la mécanique sa forme achevée en 1687 (Principia). Einstein l'a complétée en 1905 par la mécanique relativiste, pour les très grandes vitesses."},
    {"titre": "cinematique", "cles": ["cinematique", "notions de cinematique", "qu est ce que la cinematique"], "reponse": "La cinématique décrit les mouvements sans chercher leurs causes : repérage d'un mobile (référentiel, repère, trajectoire), vecteur position, vitesse et accélération, puis les mouvements simples : rectiligne uniforme, rectiligne uniformément varié et circulaire uniforme. Demande-moi une de ces notions ou donne-moi un exercice avec ses données."},
    {"titre": "referentiel", "cles": ["referentiel", "qu est ce qu un referentiel", "difference referentiel repere"], "reponse": "Un référentiel est le solide de référence par rapport auquel on décrit le mouvement. Un repère est le référentiel + une origine O + trois vecteurs unitaires (O, i, j, k). La trajectoire dépend du référentiel : la valve d'une roue de vélo décrit un cercle par rapport au vélo, mais une cycloïde par rapport au sol. Le mouvement est donc relatif."},
    {"titre": "repere terrestre", "cles": ["repere terrestre", "repere geocentrique", "repere heliocentrique", "repere galactocentrique", "reperes utilises"], "reponse": "• Repère terrestre : lié à la Terre ; pour les mouvements de courte durée près de la surface (projectile, chute).\n• Repère géocentrique (de Coriolis) : centre de la Terre, axes vers 3 étoiles fixes ; pour les satellites et la Lune.\n• Repère héliocentrique (de Copernic) : centre du Soleil ; pour les planètes, comètes, sondes.\n• Repère galactocentrique : centre d'inertie de la Galaxie ; pour le mouvement du Soleil autour du noyau de la Voie Lactée."},
    {"titre": "instant et duree", "cles": ["instant et duree", "instant", "duree d un phenomene", "date d un evenement", "origine des temps", "unite de temps", "la seconde"], "reponse": "Un instant t (ou date) repère un événement par rapport à l'origine des temps (t = 0). Une durée est l'intervalle entre deux dates : Δt = t₂ − t₁. L'unité S.I. du temps est la seconde (s), définie à partir d'une transition électronique de l'atome de césium 133 (seconde atomique)."},
    {"titre": "trajectoire", "cles": ["trajectoire", "equation horaire", "vecteur position", "abscisse curviligne", "coordonnees cartesiennes"], "reponse": "La trajectoire est l'ensemble des positions successives du mobile. Son vecteur position est OM = x·i + y·j + z·k, et x = f(t), y = g(t), z = h(t) forment l'équation horaire. Sur une trajectoire connue, on peut aussi repérer le mobile par l'abscisse curviligne s = mes(AM), mesure algébrique de l'arc AM."},
    {"titre": "vecteur vitesse", "cles": ["vecteur vitesse", "vitesse instantanee", "vitesse moyenne", "definition de la vitesse", "qu est ce que la vitesse"], "reponse": "• Vitesse moyenne : v_moy = M₁M₂ / (t₂ − t₁).\n• Vitesse instantanée : v = dOM/dt (dérivée du vecteur position par rapport au temps).\n• Le vecteur vitesse est tangent à la trajectoire, dans le sens du mouvement.\n• Coordonnées : vx = dx/dt, vy = dy/dt ; norme v = √(vx² + vy²).\n• Dans la base de Frenet : v = (ds/dt)·T, donc v = ds/dt. Unité : m/s."},
    {"titre": "vecteur acceleration", "cles": ["vecteur acceleration", "acceleration instantanee", "definition de l acceleration", "qu est ce que l acceleration", "base de frenet", "acceleration normale", "acceleration tangentielle"], "reponse": "• Accélération : a = dv/dt = d²OM/dt² (dérivée de la vitesse).\n• Coordonnées : ax = dvx/dt = d²x/dt², ay = dvy/dt = d²y/dt² ; norme a = √(ax² + ay²).\n• Base de Frenet : a = aT·T + aN·N avec aT = dv/dt (tangentielle) et aN = v²/R (normale), R étant le rayon de courbure.\n• Le vecteur accélération est dirigé vers l'intérieur de la concavité de la trajectoire.\n• Cercle : R = rayon (constant) ; droite : R infini. Unité : m/s²."},
    {"titre": "mouvement rectiligne uniforme", "cles": ["mouvement rectiligne uniforme", "mru", "rectiligne uniforme"], "reponse": "Mouvement rectiligne uniforme : trajectoire droite et vitesse constante, donc accélération nulle (a = 0). v = constante et l'équation horaire est x = v·t + x₀, une fonction affine du temps (x₀ = abscisse à t = 0)."},
    {"titre": "mouvement rectiligne uniformement varie", "cles": ["mouvement rectiligne uniformement varie", "mruv", "uniformement varie", "uniformement accelere", "uniformement retarde", "mouvement accelere", "mouvement retarde"], "reponse": "Mouvement rectiligne uniformément varié : trajectoire droite et accélération constante.\n• v = a·t + v₀\n• x = ½·a·t² + v₀·t + x₀\n• v² − v₀² = 2a(x − x₀) (à connaître, très utile dans les exercices)\nSi a·v > 0, la vitesse augmente en valeur absolue : mouvement accéléré. Si a·v < 0, elle diminue : mouvement retardé (freinage)."},
    {"titre": "mouvement circulaire uniforme", "cles": ["mouvement circulaire uniforme", "mcu", "circulaire uniforme", "acceleration centripete", "vitesse angulaire", "periode et frequence"], "reponse": "Mouvement circulaire uniforme (rayon R, vitesse constante) :\n• s = R·θ (θ en rad) ; vitesse angulaire ω = dθ/dt (rad/s) ; θ = ω·t + θ₀\n• v = R·ω\n• accélération centripète (vers le centre) : a = v²/R = R·ω²\n• période T = 2π/ω (durée d'un tour, en s) ; fréquence N = 1/T = ω/2π (en Hz)."},
    {"titre": "unites du systeme international", "cles": ["unites du systeme international", "unites si", "unites s i", "grandeurs fondamentales", "unite de la force", "unite du travail", "unite de la puissance", "unite de l energie"], "reponse": "Grandeurs fondamentales (S.I.) : longueur (mètre, m), temps (seconde, s), masse (kilogramme, kg), intensité (ampère, A), température (kelvin, K).\nMécanique : vitesse m/s ; vitesse angulaire rad/s ; accélération m/s² ; force N ; moment d'une force N·m ; travail et énergie J ; puissance W.\nÉlectromagnétisme : charge C ; tension V ; champ électrique V/m ; résistance Ω ; capacité F ; champ magnétique T ; flux Wb ; inductance H ; impédance Ω ; fréquence Hz.\nNucléaire : activité Bq ; dose absorbée Gy."},
    {"titre": "que sais tu en physique", "cles": ["que sais tu en physique", "que sais tu de la physique", "tu connais la physique", "cours de physique"], "reponse": "En physique, je connais pour l'instant le chapitre M1 (cinématique) : repères, trajectoire, vitesse, accélération, mouvements rectiligne uniforme, rectiligne uniformément varié et circulaire uniforme. Je sais aussi calculer : donne-moi un énoncé avec ses données (ex. « v0 = 5 m/s, a = 2 m/s², t = 4 s, calcule la vitesse »). D'autres chapitres seront ajoutés."},
]
CONNAISSANCES_GENERALES.extend(FICHES_PHYSIQUE)

# ---------------- FICHES DU GUIDE ILLUSTRÉ DES SCHÉMAS (PDF du créateur) ----------------
FICHES_GUIDE = [
    {"titre": "lire un schema electrique", "cles": ["lire un schema electrique", "comment lire un schema", "lecture d un schema", "symboles electriques"], "reponse": "Un schéma représente les fonctions et les connexions d’un circuit, pas toujours la position physique des appareils. Pour le lire : suis les conducteurs d’un point à l’autre, repère les symboles, puis vérifie le calibre et le type des appareils dans la documentation technique. Avant toute intervention : coupe, consigne et vérifie l’absence de tension avec un appareil adapté."},
    {"titre": "couleurs des conducteurs", "cles": ["couleurs des conducteurs", "couleur des fils", "couleur du fil", "code couleur electrique", "fil de phase couleur", "couleur de la phase", "couleur du neutre", "couleur de la terre"], "reponse": "Dans les schémas pédagogiques : phase L en rouge, neutre N en bleu clair, terre PE en vert, retour lampe ou commande en orange/violet, et L1/L2/L3 en rouge/jaune/bleu (repères du guide seulement).\nEn réalité, les couleurs doivent respecter la réglementation applicable : le jaune/vert est réservé au conducteur de protection PE et le bleu normalement au neutre. Ne jamais identifier un fil uniquement par sa couleur : vérifie le circuit et les repérages."},
    {"titre": "composants a connaitre", "cles": ["composants a connaitre", "role de l interrupteur", "role du contacteur", "role du differentiel", "role du disjoncteur", "role de la terre", "a quoi sert un contacteur", "a quoi sert un differentiel", "a quoi sert un relais thermique"], "reponse": "• Interrupteur : ouvre ou ferme un circuit d’éclairage.\n• Disjoncteur : protège contre les surintensités selon son type et son calibre.\n• Différentiel : détecte un courant de fuite selon sa sensibilité.\n• Contacteur : commute une charge de puissance à partir d’une commande.\n• Relais thermique : protège un moteur contre certaines surcharges.\n• Terre (PE) : fonction de protection, pas un retour normal du courant."},
    {"titre": "simple allumage", "cles": ["simple allumage", "va et vient ou simple allumage", "circuit d eclairage simple", "allumer une lampe depuis un seul endroit"], "reponse": "Simple allumage : commander une lampe depuis un seul endroit. La phase L passe par l’interrupteur ; le conducteur commuté (retour lampe) alimente la lampe quand l’interrupteur est fermé ; le neutre N rejoint directement la lampe.\nÀ retenir : l’interrupteur doit normalement couper la phase, pas seulement le neutre. Le conducteur de protection doit être raccordé aux parties métalliques quand l’équipement l’exige. Boîtes et connexions adaptées et accessibles."},
    {"titre": "double allumage", "cles": ["double allumage", "interrupteur double", "deux lampes commandees separement"], "reponse": "Double allumage : commander deux groupes de lampes séparément depuis le même emplacement (par exemple deux rangées d’éclairage). Une phase commune alimente le mécanisme ; deux sorties donnent deux retours lampe ; le neutre est commun aux lampes.\nErreur fréquente : confondre le double allumage avec le va-et-vient. Le double allumage sépare deux groupes ; le va-et-vient commande une même lampe depuis deux endroits."},
    {"titre": "fonctionnement du va et vient", "cles": ["fonctionnement du va et vient", "principe du va et vient", "navettes", "lampe va et vient ne marche pas", "diagnostic va et vient"], "reponse": "Va-et-vient : allumer ou éteindre une même lampe depuis deux endroits (par exemple en haut et en bas d’un escalier). Les deux commutateurs sont reliés par deux navettes. La phase arrive sur le commun du premier commutateur ; le commun du second alimente le retour lampe ; le neutre rejoint directement la lampe.\nDiagnostic de base (hors tension) : si la lampe ne s’allume jamais, vérifie l’alimentation, la lampe et les connexions. Si elle marche depuis un seul endroit ou dans certaines positions, fais contrôler les bornes communes et les navettes. Ne jamais intervenir sous tension."},
    {"titre": "prise de courant", "cles": ["prise de courant", "mise a la terre", "conducteur de protection", "prise 230 v", "a quoi sert la terre", "prise de terre"], "reponse": "Une prise 230 V a trois conducteurs : L (phase) et N (neutre) alimentent l’appareil ; PE (terre) est le conducteur de protection qui offre un chemin en cas de défaut d’isolement sur une masse métallique. La terre ne remplace jamais le disjoncteur différentiel.\nÀ ne jamais faire : supprimer la terre, relier arbitrairement N et PE dans une prise, ou augmenter le calibre d’un disjoncteur sans vérifier tout le circuit. La protection différentielle et contre les surintensités doivent être adaptées au circuit, à la section des conducteurs et aux règles en vigueur."},
    {"titre": "demarrage direct d un moteur", "cles": ["demarrage direct d un moteur", "demarrage direct du moteur", "moteur triphasé demarrage direct", "demarrage direct moteur triphase"], "reponse": "Démarrage direct : un contacteur KM connecte directement le moteur au réseau via une protection (QF ou Q1) et un relais thermique. Simple et économique, mais le courant de démarrage peut être élevé : à utiliser seulement si le réseau, la machine et les protections le permettent.\nLe circuit de commande (boutons Marche/Arrêt, bobine KM, contacts auxiliaires) est distinct du circuit de puissance. Avant de choisir le montage : lis la plaque moteur (tension, courant, fréquence, couplage, puissance), vérifie la tension du réseau, la coordination des protections et la capacité de coupure."},
    {"titre": "auto maintien", "cles": ["auto maintien", "automaintien", "commande marche arret", "bouton marche arret contacteur", "principe de l auto maintien"], "reponse": "Commande Marche/Arrêt avec auto-maintien : le bouton Arrêt est normalement fermé (NF), le bouton Marche normalement ouvert (NO). Une impulsion sur Marche alimente la bobine KM ; un contact auxiliaire NO de KM se ferme en parallèle du bouton Marche et maintient la bobine alimentée après relâchement. Appuyer sur Arrêt ouvre la commande et libère le contacteur.\nContrôles hors tension : repérage de la bobine (tension nominale correcte), état des boutons, continuité des conducteurs, contact auxiliaire et relais thermique. Ne jamais shunter un dispositif de protection pour faire fonctionner le moteur."},
    {"titre": "demarrage etoile triangle", "cles": ["demarrage etoile triangle", "etoile triangle", "demarrage etoile triangle moteur", "contacteur etoile et triangle"], "reponse": "Démarrage étoile-triangle : réduit le courant de démarrage d’un moteur compatible en démarrant en étoile puis en passant en triangle. Il faut un moteur prévu pour fonctionner en triangle à la tension du réseau, six bornes accessibles, trois contacteurs (principal KM1, étoile KM2, triangle KM3), un temporisateur et des verrouillages électriques et mécaniques.\nSéquence : 1) fermeture du principal et de l’étoile ; 2) après le délai réglé, ouverture de l’étoile ; 3) courte transition ; 4) fermeture du triangle. Étoile et triangle ne doivent jamais être fermés ensemble : court-circuit. Le schéma du guide est fonctionnel, pas un plan de câblage borne par borne : suis le schéma du constructeur."},
    {"titre": "transformateur monophase", "cles": ["transformateur monophase", "fonctionnement d un transformateur", "principe du transformateur", "rapport de transformation", "transformateur abaisseur", "transformateur elevateur", "role du transformateur"], "reponse": "Un transformateur transfère l’énergie d’un enroulement primaire vers un secondaire par induction magnétique ; le noyau guide le flux. Dans un transformateur idéal : U2/U1 ≈ N2/N1. Un abaisseur donne une tension secondaire plus faible ; un élévateur, plus élevée.\nSécurité : certains transformateurs délivrent des tensions dangereuses. Vérifie la plaque signalétique, l’isolement, les protections amont/aval et la mise à la terre. Ne jamais toucher les bornes pour « tester » la tension."},
    {"titre": "resistance et loi d ohm", "cles": ["resistance et loi d ohm", "loi d ohm", "calcul de puissance d une resistance", "p egale u fois i", "puissance dissipee dans une resistance"], "reponse": "Une résistance limite le courant ou transforme l’énergie électrique en chaleur. Loi d’Ohm : U = R × I (U en V, R en Ω, I en A). Puissance : P = U × I = I²R = U²/R pour une résistance ohmique.\nExemple : R = 100 Ω sous 10 V → I = 10/100 = 0,1 A et P = 10 × 0,1 = 1 W. Choisis une puissance nominale avec une marge suffisante et respecte les conditions thermiques du fabricant."},
    {"titre": "moteur a courant continu", "cles": ["moteur a courant continu", "moteur cc", "collecteur et balais", "moteur courant continu"], "reponse": "Un moteur à courant continu comprend un stator (aimants ou enroulements), un rotor/induit, un collecteur et des balais (sur les modèles à balais). Le collecteur et les balais assurent la commutation du courant dans l’induit ; les moteurs sans balais utilisent une électronique de commande.\nDiagnostic élémentaire : vérifier l’alimentation nominale, l’état mécanique, les connexions, l’échauffement et l’usure des balais. Débranche l’alimentation avant toute inspection : un moteur peut démarrer brusquement."},
    {"titre": "appareillages de protection", "cles": ["appareillages de protection", "difference disjoncteur differentiel", "fonction du fusible", "appareils de protection electrique", "disjoncteur ou differentiel"], "reponse": "• Disjoncteur : protège le circuit contre certaines surintensités. Vigilance : calibre et courbe adaptés aux conducteurs et à la charge.\n• Interrupteur différentiel (DDR) : détecte un courant différentiel. Vigilance : type et sensibilité adaptés ; test périodique.\n• Fusible : ouvre le circuit si le courant dépasse ses caractéristiques. Remplace-le par le bon type et calibre, jamais par un fil.\n• Contacteur : commande une charge de puissance à distance. Vérifie la catégorie d’emploi et la tension de bobine.\n• Relais thermique : détecte certaines surcharges prolongées du moteur. Réglage selon le courant de la plaque ; ne remplace pas toute protection."},
    {"titre": "methode de diagnostic", "cles": ["methode de diagnostic", "diagnostic d une panne electrique", "comment depanner", "methode pour depanner", "panne electrique methode"], "reponse": "Méthode simple en 5 étapes :\n1) Sécuriser : identifier la source, couper, empêcher la remise sous tension, vérifier l’absence de tension.\n2) Observer : odeur, échauffement, bruit, voyants, déclenchement.\n3) Lire le schéma : suivre l’alimentation jusqu’à la charge.\n4) Contrôler hors tension : connexions, continuité, état apparent.\n5) Corriger et tester, seulement si tu as les compétences, procédures et équipements.\nSymptômes : lampe éteinte → lampe, alimentation, interrupteur ou connexion ouverte. Disjoncteur qui déclenche → surcharge, court-circuit ou défaut d’isolement : cherche la cause, ne réarme pas en boucle. Contacteur qui ne colle pas → commande absente, arrêt ouvert, relais thermique déclenché, bobine défectueuse. Moteur qui chauffe → surcharge, ventilation obstruée, mauvais couplage/tension, défaut mécanique, alimentation déséquilibrée. Transformateur qui chauffe ou fait du bruit → surcharge, ventilation, tension incorrecte, défaut interne : mettre hors service."},
    {"titre": "glossaire electrique", "cles": ["glossaire electrique", "que veut dire no et nf", "contact no", "contact nf", "normalement ouvert", "normalement ferme", "c est quoi une navette", "retour lampe"], "reponse": "• NO : normalement ouvert (ouvert au repos). • NF : normalement fermé (fermé au repos).\n• Bobine : crée un champ magnétique quand elle est alimentée.\n• Navettes : conducteurs entre les deux commutateurs d’un va-et-vient.\n• Retour lampe : conducteur commandé qui alimente la lampe quand le circuit est fermé.\n• Couplage étoile/triangle : manière de relier les enroulements d’un moteur triphasé.\n• Absence de tension : état à vérifier avec une procédure et un instrument adaptés, après consignation."},
    {"titre": "demarrage par resistances", "cles": ["demarrage par resistances", "resistances statoriques", "resistances rotoriques", "demarrage d un moteur par resistances"], "reponse": "Démarrage par résistances : des résistances sont insérées temporairement en série avec le moteur pour réduire la tension appliquée au démarrage, puis court-circuitées par des contacteurs. Cela provoque des pertes thermiques et exige des résistances dimensionnées pour le courant et la durée de démarrage.\nÀ distinguer : résistances statoriques (moteur asynchrone) ou rotoriques (moteur à rotor bobiné). Les schémas et les appareils ne sont pas interchangeables."},
    {"titre": "demarrage par autotransformateur", "cles": ["demarrage par autotransformateur", "autotransformateur de demarrage", "demarrage autotransformateur"], "reponse": "Démarrage par autotransformateur : il fournit une tension réduite au moteur pendant le démarrage ; une fois la vitesse suffisante, des contacteurs basculent le moteur sur sa tension normale. Convient à des moteurs de puissance importante, mais l’ensemble est plus volumineux et plus complexe qu’un démarrage direct.\nÀ vérifier : prises de tension disponibles, séquence de commutation, protections, courant et temps de démarrage, contraintes mécaniques de la charge."},
    {"titre": "demarreur progressif", "cles": ["demarreur progressif", "soft starter", "softstarter", "demarrage progressif"], "reponse": "Démarreur progressif (soft starter) : l’électronique augmente progressivement la tension appliquée au moteur, ce qui limite les à-coups mécaniques et le courant de démarrage. En général, il ne règle pas la vitesse en continu comme un variateur.\nBonnes pratiques : choisir selon le courant nominal, la classe de service, le nombre de démarrages par heure, la ventilation, le bypass éventuel et la notice du fabricant ; prévoir les protections recommandées."},
    {"titre": "variateur de frequence", "cles": ["variateur de frequence", "variateur de vitesse", "variateur moteur", "demarrage par variateur"], "reponse": "Variateur de fréquence : il transforme l’alimentation puis produit une tension et une fréquence variables pour contrôler la vitesse d’un moteur compatible, avec des rampes d’accélération et de décélération.\nÀ vérifier : compatibilité moteur/variateur, puissance et courant, type d’alimentation, réglages de plaque moteur, ventilation à basse vitesse, mise à la terre, câbles, filtres éventuels et consignes du fabricant. Les bornes de commande diffèrent selon les modèles."},
    {"titre": "comparaison des demarrages", "cles": ["comparaison des demarrages", "methodes de demarrage d un moteur", "methodes de demarrage d un moteur triphase", "quelles sont les methodes de demarrage", "types de demarrage d un moteur", "methodes de demarrage des moteurs", "quels sont les types de demarrage", "differents demarrages de moteur", "types de demarrage moteur"], "reponse": "Méthodes de démarrage d’un moteur :\n• Direct : simple, coût faible ; courant de démarrage élevé.\n• Étoile-triangle : réduit le courant ; moteur compatible et six bornes nécessaires.\n• Résistances : principe simple ; pertes thermiques, dimensionnement nécessaire.\n• Autotransformateur : tension de départ réduite ; équipement plus complexe et encombrant.\n• Soft starter : démarrage et arrêt progressifs ; ne règle généralement pas la vitesse.\n• Variateur : contrôle de vitesse et rampes ; réglage selon la notice constructeur."},
    {"titre": "securite avant mise en service", "cles": ["securite avant mise en service", "avant de mettre en service un moteur", "verifications avant mise en service", "consignation avant intervention"], "reponse": "Avant la mise en service :\n• Identifier la machine et lire sa plaque signalétique.\n• Couper, consigner et vérifier l’absence de tension avec un appareil adapté.\n• Vérifier la continuité du conducteur de protection et la coordination des protections.\n• Vérifier le serrage, les sections de conducteurs, le calibre des appareils et le schéma constructeur.\n• Tester les arrêts et les dispositifs de sécurité avant exploitation.\n• Ne jamais shunter un verrouillage, un relais thermique ou une protection pour forcer un démarrage."},
    {"titre": "five wire", "cles": ["five wire", "5 wire", "cinq fils", "montage cinq fils", "five wire cablage"], "reponse": "« Five-wire » (cinq fils) peut désigner plusieurs montages selon le contexte : circuit de commande, câblage de moteur, va-et-vient ou autre système. Ce n’est pas assez précis pour identifier un schéma unique. Dis-moi de quel circuit il s’agit et ce que tu veux faire, et je t’expliquerai le bon montage (sans te donner un plan de câblage réel avant validation avec la notice et un professionnel qualifié)."},
]
CONNAISSANCES_GENERALES.extend(FICHES_GUIDE)


# ---------------- COURS D'ÉLECTROTECHNIQUE 2nde et 1ère F3/EL (M. Gildas Zonakpo, LYTEB) ----------------
# Fiches issues du cours « Électrotechnique 2nde F3/EL » (M. Gildas Zonakpo, LYTEB)
S_2NDE = "(Source : cours Électrotechnique 2nde F3/EL, chap. {})"

FICHES_2NDE = [
# ------------------------------------------------------------------ CHAPITRE 1
{"titre": "programme 2nde", "cles": [
    "programme de seconde", "programme de la seconde", "programme de 2nde", "programme de la 2nde", "programme 2nde", "cours de seconde", "cours de 2nde", "electrotechnique 2nde", "electrotechnique seconde", "que contient le cours de seconde"],
 "reponse": "Programme d'électrotechnique de 2nde F3/EL (cours de M. Gildas Zonakpo, LYTEB) :\n• Chapitre 1 : circuits électriques en courant continu (lois, dipôles, associations, Kirchhoff, Thévenin, Norton, Millman, Kennelly).\n• Chapitre 2 : piles et accumulateurs.\n• Chapitre 3 : diodes (jonction, Zener).\n• Chapitre 4 : électrostatique (charges, Coulomb, champ, potentiel, condensateurs).\nDemande-moi une notion, par exemple « loi des mailles », « théorème de Thévenin » ou « énergie d'un condensateur »."},

{"titre": "circuit electrique", "cles": [
    "circuit electrique", "notion de circuit electrique", "definition d un circuit electrique", "generateur recepteur organe de commande",
    "role du generateur", "role du recepteur", "role de l organe de commande", "qu est ce qu un circuit electrique"],
 "reponse": "Un circuit électrique comprend au moins un générateur, un récepteur et un organe de commande, reliés par des fils conducteurs.\n• Générateur : source d'énergie, il crée une différence de potentiel (batterie, pile, cellule photovoltaïque).\n• Organe de commande : permet ou non le passage du courant (interrupteur).\n• Récepteur : reçoit l'énergie électrique et la transforme en une autre forme (lampe, moteur, résistor).\n• Fils conducteurs : transportent l'énergie.\nLes conducteurs laissent passer le courant (cuivre, aluminium) ; les isolants s'y opposent très fortement (verre, plastique, porcelaine).\n" + S_2NDE.format(1)},

{"titre": "dipole noeud branche maille", "cles": [
    "dipole electrique", "noeud branche maille", "qu est ce qu un noeud", "qu est ce qu une maille", "qu est ce qu une branche", "definition d un noeud",
    "definition d une maille", "definition d une branche", "dipoles en serie", "dipoles en derivation", "dipoles en parallele", "difference serie derivation"],
 "reponse": "• Dipôle : tout composant (ou portion de circuit) à deux bornes.\n• Nœud : point de connexion qui réunit au moins trois dipôles.\n• Branche : portion de circuit comprise entre deux nœuds consécutifs.\n• Maille : chemin fermé.\n• Dipôles en série : ils appartiennent à la même branche (un seul chemin pour le courant).\n• Dipôles en dérivation (parallèle) : ils appartiennent à des branches comprises entre les deux mêmes nœuds.\n" + S_2NDE.format(1)},

{"titre": "intensite du courant", "cles": [
    "intensite du courant", "intensite du courant electrique", "courant electrique definition", "definition du courant electrique", "sens conventionnel du courant",
    "sens du courant", "ampermetre", "ampere metre", "charge elementaire", "i egale q sur t", "porteurs de charges", "intensite algebrique"],
 "reponse": "Le courant électrique est un déplacement ordonné de porteurs de charges (électrons ou ions).\n• Charge de l'électron : e = −1,6 × 10⁻¹⁹ C.\n• Sens conventionnel : celui des charges positives, donc de la borne + vers la borne − du générateur (inverse du déplacement des électrons). Le courant ne circule que dans un circuit fermé.\n• Intensité : I = |q| / t (I en ampères, q en coulombs, t en secondes).\n• Mesure : ampèremètre placé en SÉRIE.\n• L'intensité est algébrique : positive si le courant circule dans le sens conventionnel choisi, négative sinon.\nExemple : q = 7200 C en 1 h (3600 s) → I = 7200/3600 = 2 A.\n" + S_2NDE.format(1)},

{"titre": "loi des noeuds", "cles": [
    "loi des noeuds", "loi des noeud", "loi d unicite des intensites", "unicite des intensites", "premiere loi de kirchhoff", "somme des courants entrant dans un noeud"],
 "reponse": "• Loi d'unicité des intensités : des dipôles montés en série sont traversés par la même intensité.\n• Loi des nœuds : la somme des intensités qui entrent dans un nœud est égale à la somme des intensités qui en sortent (Σ I entrants = Σ I sortants).\nExemple : si I₁ et I₂ entrent et I₃ sort, alors I₁ + I₂ = I₃.\n" + S_2NDE.format(1)},

{"titre": "tension electrique", "cles": [
    "tension electrique", "definition de la tension", "difference de potentiel", "ddp", "d d p", "voltmetre", "u ab egale va moins vb", "tension algebrique",
    "convention recepteur", "convention generateur", "qu est ce que la tension"],
 "reponse": "La tension électrique U (en volts) est la différence de potentiel entre deux points : U_AB = V_A − V_B. Elle est représentée par une flèche dirigée de B vers A.\n• Algébrique : positive si V_A > V_B, négative si V_A < V_B.\n• Mesure : voltmètre placé en DÉRIVATION aux bornes du dipôle.\n• La tension aux bornes d'un fil conducteur est nulle.\n• Convention récepteur : tension et courant de sens contraires. Convention générateur : tension et courant de même sens.\n" + S_2NDE.format(1)},

{"titre": "loi des mailles", "cles": [
    "loi des mailles", "loi des maille", "loi des branches", "loi d unicite des tensions", "unicite des tensions", "deuxieme loi de kirchhoff", "somme algebrique des tensions"],
 "reponse": "• Loi des mailles : dans une maille, la somme algébrique des tensions est nulle. On choisit un sens de parcours : les tensions orientées dans ce sens comptent +, les autres comptent −.\n  Exemple (maille ABCDA) : U_AD − U_AB − U_BC + U_DC = 0.\n• Loi des branches : la tension totale d'une branche est la somme algébrique des tensions partielles : U_AC = U_AB + U_BC.\n• Loi d'unicité des tensions : des dipôles en dérivation ont la même tension à leurs bornes.\n" + S_2NDE.format(1)},

{"titre": "puissance et energie electrique 2nde", "cles": [
    "puissance electrique d un dipole", "puissance d un dipole", "p egale u i", "puissance et energie electrique", "energie electrique w egale p t", "formule de l energie electrique",
    "wattheure en joule", "1 wh en joules", "quelle est la formule de la puissance electrique", "formule de la puissance electrique"],
 "reponse": "• Puissance d'un dipôle : P = U × I (P en watts, U en volts, I en ampères).\n• Énergie : W = P × t = U × I × t (W en joules, t en secondes).\n• 1 Wh = 3600 J (et 1 kWh = 3,6 × 10⁶ J).\nLe générateur fournit la puissance, les récepteurs l'absorbent.\nExemple : récepteur sous 100 V, I = 10 A, pendant 2 h → P = 1000 W et W = 1000 × 7200 = 7,2 × 10⁶ J = 2 kWh.\n" + S_2NDE.format(1)},

{"titre": "rendement energetique", "cles": [
    "rendement energetique", "rendement d un recepteur", "rendement d une machine", "rendement global", "formule du rendement", "pertes par effet joule", "energie utile energie recue",
    "comment calculer le rendement", "calcul du rendement"],
 "reponse": "Rendement : η = énergie utile / énergie reçue (ou P_utile / P_absorbée). Il n'a pas d'unité et η ≤ 1 (100 % au maximum).\n• Énergie reçue = énergie utile + énergie perdue (pertes dissipées en chaleur).\n• Pour plusieurs équipements en chaîne : η_global = η₁ × η₂ × η₃ × …\nExemple : moteur qui absorbe 2000 W avec η = 0,9 → pertes = 2000 × 0,1 = 200 W ; en 1 h 20 min (4800 s) → 960 000 J ≈ 0,267 kWh.\n" + S_2NDE.format(1)},

{"titre": "resistor et loi d ohm 2nde", "cles": [
    "dipole passif", "resistor", "resistance electrique definition", "conductance electrique", "conductance", "siemens", "g egale 1 sur r", "recepteur purement resistif",
    "loi d ohm cours", "loi d ohm resistor", "loi d ohm conducteur ohmique", "conducteur ohmique", "rheostat"],
 "reponse": "• Dipôle passif : il ne fournit pas d'énergie au circuit (c'est un récepteur : résistor, diode, lampe, moteur…).\n• Résistor (conducteur ohmique) : récepteur dont le seul rôle est de s'opposer au passage du courant. Résistance R en ohms (Ω).\n• Conductance : G = 1/R, en siemens (S).\n• Loi d'Ohm : U_AB = R × I.\nExemples : lampe de 10 Ω sous 120 V → I = 12 A ; fil de 50 mΩ traversé par 300 A → chute de tension = 0,05 × 300 = 15 V.\n" + S_2NDE.format(1)},

{"titre": "effet joule", "cles": [
    "effet joule", "loi de joule", "loi de joules", "effet joules", "w egale r i carre t", "puissance dissipee par effet joule", "chaleur dans un resistor"],
 "reponse": "Effet Joule : en s'opposant au passage du courant, un résistor transforme toute l'énergie électrique reçue en chaleur.\n• Énergie dissipée : W = R × I² × t (J).\n• Puissance dissipée : P = R × I² = U²/R = U × I.\nDémonstration : W = U·I·t avec U = R·I, donc W = R·I²·t.\n" + S_2NDE.format(1)},

{"titre": "resistance d un fil et temperature", "cles": [
    "resistance d un fil", "resistance d un conducteur filiforme", "resistance d un cable", "resistivite", "r egale rho l sur s", "formule de la resistance d un fil",
    "variation de la resistance avec la temperature", "coefficient de temperature", "resistance et temperature", "rt egale r0"],
 "reponse": "• Résistance d'un fil de longueur ℓ, de section S et de résistivité ρ : R = ρ × ℓ / S (R en Ω, ℓ en m, S en m², ρ en Ω·m).\n• Plus le fil est long ou fin, plus sa résistance est grande.\n• Avec la température : R_t = R₀ (1 + α·t), où R₀ est la résistance à 0 °C et α le coefficient de température (°C⁻¹). Quand la température augmente, la résistance d'un métal augmente.\n• ρ et α dépendent du matériau (valeurs à 0 °C du tableau du cours) : argent 15,0 nΩ·m (α = 0,00411 /°C) ; cuivre 15,9 nΩ·m (0,00427) ; aluminium 26,0 nΩ·m (0,00439) ; tungstène 49,6 nΩ·m (0,0055) ; manganine 482 nΩ·m (0,000015) ; nichrome 1080 nΩ·m (0,00011).\nExemples : câble de cuivre de 2 km et 67 mm² → R = 15,9×10⁻⁹ × 2000 / 67×10⁻⁶ ≈ 0,47 Ω par conducteur (≈ 0,95 Ω pour l'aller et le retour d'un câble bifilaire). Ligne de cuivre de 100 mΩ à 0 °C : à 35 °C R ≈ 114,95 mΩ ; à −30 °C R ≈ 87,2 mΩ ; la variation est ≈ 27,8 mΩ.\n" + S_2NDE.format(1)},

{"titre": "association de resistors", "cles": [
    "association de resistors", "association de resistances", "resistances en serie", "resistances en parallele", "resistances en derivation", "resistance equivalente",
    "resistors en serie", "resistors en parallele", "calcul de la resistance equivalente", "resistance equivalente serie", "resistance equivalente parallele"],
 "reponse": "• En série : R_éq = R₁ + R₂ + … + R_n (la même intensité traverse chaque résistor).\n• En parallèle : 1/R_éq = 1/R₁ + 1/R₂ + … + 1/R_n (même tension aux bornes). Pour deux résistors : R_éq = R₁·R₂ / (R₁ + R₂).\nOn associe des résistors pour obtenir une valeur non normalisée.\nExemple : 100 Ω et 150 Ω en série → 250 Ω ; en parallèle → 100×150/250 = 60 Ω.\n" + S_2NDE.format(1)},

{"titre": "diviseur de tension et de courant", "cles": [
    "diviseur de tension", "diviseur de courant", "montage diviseur de tension", "montage diviseur de courant", "diviseur potentiometrique", "pont diviseur", "pont diviseur de tension",
    "formule du diviseur de tension", "formule du diviseur de courant"],
 "reponse": "• Diviseur de tension (R₁ et R₂ en série sous U) : U₁ = R₁/(R₁+R₂) × U et U₂ = R₂/(R₁+R₂) × U. Condition : les deux résistances sont en série, traversées par le même courant, et la sortie ne débite pas (pas de charge).\n• Diviseur de courant (R₁ et R₂ en parallèle, courant total I) : I₁ = R₂/(R₁+R₂) × I et I₂ = R₁/(R₁+R₂) × I. Condition : deux branches en parallèle recevant le courant I.\nPour N résistors en série : U_k = R_k / (ΣR) × U.\nApplications : sonde atténuatrice d'oscilloscope (R_s = 9 R_OK donne U_OK = U/10), shunt d'ampèremètre.\n" + S_2NDE.format(1)},

{"titre": "electromoteur generateur", "cles": [
    "dipole actif", "electromoteur", "electromoteur generateur", "force electromotrice", "f e m", "fem", "resistance interne", "u egale e moins r i",
    "tension a vide", "courant de court circuit d un generateur", "modele d un generateur", "caracteristique d un generateur"],
 "reponse": "Un dipôle actif (électromoteur) fournit de l'énergie (générateur : batterie, pile, cellule photovoltaïque) ou la consomme pour la transformer en énergie chimique ou mécanique (récepteur : moteur, cuve à électrolyse). Réversible s'il peut faire les deux (accumulateur).\n• Générateur : source de tension parfaite E (force électromotrice, f.é.m., ou tension à vide) en série avec une résistance interne r. Loi : U = E − r·I.\n• Courant de court-circuit : I_cc = E / r.\n• Équivalent en source de courant : I = I₀ − U/r.\nExemple : U = 5,4 V pour 0,1 A et 5 V pour 0,2 A → r = 4 Ω, E = 5,8 V, I_cc = 1,45 A.\n" + S_2NDE.format(1)},

{"titre": "electromoteur recepteur", "cles": [
    "electromoteur recepteur", "force contre electromotrice", "f c e m", "fcem", "u egale e prime plus r prime i", "recepteur actif", "moteur force contre electromotrice"],
 "reponse": "Électromoteur récepteur : source de tension parfaite E' (force contre-électromotrice, f.c.é.m.) en série avec une résistance interne r'. Loi : U = E' + r'·I.\nIl consomme de l'énergie électrique pour la transformer en énergie chimique (cuve à électrolyse, accumulateur en charge) ou mécanique (moteur).\nExemple : r' = 1 Ω, U = 9 V, I = 1,5 A → E' = 9 − 1,5 = 7,5 V.\n" + S_2NDE.format(1)},

{"titre": "bilan de puissances electromoteur", "cles": [
    "bilan de puissances", "bilan de puissance d un generateur", "bilan de puissances d un generateur", "bilan de puissances d un recepteur", "puissance utile d un generateur",
    "rendement d un generateur", "rendement d un electromoteur", "eta egale u sur e", "puissance perdue par effet joule dans le generateur"],
 "reponse": "• Générateur (U = E − rI) : P_a = E·I (puissance produite), P_u = U·I (puissance utile fournie), P_p = r·I² (perte par effet Joule). On a E·I = U·I + r·I². Rendement η = P_u/P_a = U/E.\n• Récepteur (U = E' + r'I) : P_a = U·I (absorbée), P_u = E'·I (utile), P_p = r'·I² (perdue). Rendement η = E'/U.\nExemple : générateur r = 2 Ω fournissant 180 W avec η = 90 % → P_a = 200 W ; pertes = 20 W = 2·I² donc I ≈ 3,16 A ; U = 180/3,16 ≈ 56,9 V ; E = U/0,9 ≈ 63,2 V.\n" + S_2NDE.format(1)},

{"titre": "association de generateurs", "cles": [
    "association de generateurs", "generateurs en serie", "generateurs en parallele", "association serie de generateurs", "association parallele de generateurs",
    "batteries en serie", "batteries en parallele", "piles en serie", "piles en parallele", "accumulateurs en serie", "accumulateurs en parallele", "pouillet", "loi de pouillet", "e equivalent", "mettre des piles en serie", "mettre des batteries en parallele"],
 "reponse": "Les générateurs sont polarisés (+ / −) : respecte les bornes !\n• En série (borne + de l'un reliée à la borne − du suivant) : E_éq = ΣE_i et R_éq = Σr_i. Les tensions s'ajoutent.\n• En parallèle, n générateurs identiques (+ avec +, − avec −) : E_éq = E et R_éq = r/n. Chaque générateur débite I/n ; la capacité (courant) augmente, pas la tension.\n• Loi de Pouillet (circuit en série avec plusieurs dipôles actifs et passifs) : I = (ΣE − ΣE') / ΣR.\nExemple : G₁(30 V ; 1 Ω), G₂(12 V ; 1 Ω), G₃(14 V ; 0,1 Ω) en série → E_éq = 56 V, R_éq = 2,1 Ω.\n" + S_2NDE.format(1)},

{"titre": "lois de kirchhoff", "cles": [
    "lois de kirchhoff", "loi de kirchhoff", "kirchhoff", "kirchoff", "analyse de reseaux electriques", "reseau electrique", "etude d un reseau electrique", "methode de kirchhoff"],
 "reponse": "Lois de Kirchhoff = loi des nœuds + loi des mailles. Méthode pour trouver les courants d'un réseau :\n1) Représenter le courant dans chaque branche et la tension aux bornes de chaque dipôle (en respectant les conventions).\n2) Écrire les équations : loi des nœuds pour N − 1 nœuds, loi des mailles pour M − 1 mailles.\n3) Résoudre le système.\nSi le sens réel n'est pas connu, on choisit un sens arbitraire : après calcul, un courant positif a le sens choisi, un courant négatif circule en sens contraire.\nExemple du cours : E₁ = 200 V, R₁ = 5 Ω ; E₂ = 100 V, R₂ = 10 Ω ; R₃ = 20 Ω (les trois branches en parallèle entre A et B, E₁ et E₂ orientées vers A) → U_AB ≈ 142,9 V ; I₁ ≈ 11,43 A, I₂ ≈ −4,29 A, I₃ ≈ 7,14 A. Le courant I₂ est négatif : E₂ fonctionne donc en récepteur ; la tension aux bornes de R₃ vaut ≈ 142,9 V.\nAutres outils : superposition, Thévenin, Norton, Millman.\n" + S_2NDE.format(1)},

{"titre": "theoreme de superposition", "cles": [
    "theoreme de superposition", "methode de superposition", "principe de superposition circuit", "superposition des sources", "eteindre une source de tension", "eteindre une source de courant"],
 "reponse": "Théorème de superposition : dans un circuit à plusieurs sources, le courant dans une branche est la somme algébrique des courants produits par chaque source agissant seule, les autres étant éteintes.\n• Éteindre une source de tension = la court-circuiter (la remplacer par un fil).\n• Éteindre une source de courant = l'enlever du circuit (circuit ouvert).\nMéthode : calculer I' (source 1 seule), I'' (source 2 seule), puis I = I' ± I'' selon les sens (par exemple I₁ = I'₁ − I''₁ et I₃ = I'₃ + I''₃ si les courants dus à chaque source sont de sens contraires dans la branche 1 et de même sens dans la branche 3).\n" + S_2NDE.format(1)},

{"titre": "theoreme de thevenin", "cles": [
    "theoreme de thevenin", "thevenin", "dipole equivalent de thevenin", "generateur de thevenin", "modele de thevenin", "resistance de thevenin", "tension de thevenin", "eth rth"],
 "reponse": "Théorème de Thévenin : tout circuit linéaire entre deux bornes A et B peut être remplacé par une source de tension E_Th en série avec une résistance R_Th.\n• E_Th = tension entre A et B à vide (charge débranchée, I = 0).\n• R_Th = résistance équivalente vue de A et B quand toutes les sources sont éteintes (sources de tension court-circuitées, sources de courant ouvertes).\n• Courant dans la charge R : I = E_Th / (R_Th + R).\nExemple : E = 8 V, R₁ = 4 Ω, R₂ = 12 Ω, charge R₃ = 9 Ω (R₁ en série avec la source, R₂ en dérivation) : E_Th = 6 V, R_Th = 3 Ω, I = 6/(3+9) = 0,5 A.\n" + S_2NDE.format(1)},

{"titre": "theoreme de norton", "cles": [
    "theoreme de norton", "norton", "dipole equivalent de norton", "generateur de norton", "courant de norton", "resistance de norton",
    "equivalence thevenin norton", "passer de thevenin a norton", "transformer un generateur de tension en generateur de courant"],
 "reponse": "Théorème de Norton : tout circuit linéaire entre deux bornes A et B peut être remplacé par une source de courant I_N en parallèle avec une résistance R_N.\n• I_N = courant mesuré entre A et B quand elles sont court-circuitées (U_AB = 0).\n• R_N = résistance équivalente quand toutes les sources sont éteintes (R_N = R_Th).\nÉquivalence Thévenin ↔ Norton : un générateur de tension (E ; R) équivaut à un générateur de courant (I₀ ; R₀) avec R₀ = R et I₀ = E/R (soit E = R₀·I₀).\nExemple : E_Th = 6 V et R_Th = 3 Ω donnent I_N = 2 A en parallèle avec 3 Ω.\n" + S_2NDE.format(1)},

{"titre": "theoreme de millman", "cles": [
    "theoreme de millman", "theoreme de millmann", "millman", "millmann", "formule de millman", "tension aux bornes des branches en parallele"],
 "reponse": "Théorème de Millman : dans un réseau de branches en parallèle, chacune comportant un générateur parfait en série avec un élément linéaire, la tension commune aux bornes des branches est :\nU = (Σ E_i / R_i) / (Σ 1/R_i)\n(somme des f.é.m. × conductances de la branche, divisée par la somme des conductances). Une branche sans source a E = 0.\nOn en déduit le courant de chaque branche : I_i = (E_i − U)/R_i.\nExemple du cours (E₁ et R₁ à gauche, R₃ au milieu sans source, E₂ et R₂ à droite) : E₁ = 12 V, E₂ = 16 V, R₁ = R₃ = 10 kΩ, R₂ = 12 kΩ → U = (12/10 + 16/12 + 0)/(1/10 + 1/12 + 1/10) ≈ 8,94 V, donc I₃ = U/R₃ ≈ 0,894 mA.\n" + S_2NDE.format(1)},

{"titre": "theoreme de kennelly", "cles": [
    "theoreme de kennelly", "kennelly", "kennely", "transformation etoile triangle", "transformation triangle etoile", "conversion etoile triangle de resistances",
    "conversion triangle etoile", "passage etoile triangle resistances"],
 "reponse": "Théorème de Kennelly (transformation étoile ↔ triangle) : simplifie un réseau de résistances.\n• Triangle (R₁, R₂, R₃) → étoile : R_a = R₁R₂/(R₁+R₂+R₃) ; R_b = R₁R₃/(R₁+R₂+R₃) ; R_c = R₂R₃/(R₁+R₂+R₃). Chaque résistance de l'étoile = produit des deux résistances du triangle qui touchent ce sommet / somme des trois.\n• Étoile → triangle : R₁ = (R_aR_b + R_aR_c + R_bR_c)/R_c ; R₂ = (…)/R_b ; R₃ = (…)/R_a. Cette transformation ajoute une maille ; elle sert surtout en triphasé.\nCas particulier : triangle de trois résistances égales R → étoile de trois résistances R/3.\n" + S_2NDE.format(1)},

# ------------------------------------------------------------------ CHAPITRE 2
{"titre": "la pile", "cles": [
    "la pile", "qu est ce qu une pile", "definition d une pile", "principe d une pile", "fonctionnement d une pile", "constituants d une pile", "reaction d oxydo reduction",
    "oxydo reduction", "pont salin", "demi pile", "anode et cathode", "anode cathode pile", "quelle est la difference entre anode et cathode"],
 "reponse": "Une pile est un générateur qui convertit de l'énergie chimique en énergie électrique grâce à une réaction d'oxydo-réduction (transfert d'électrons entre réactifs).\n• Elle comprend deux demi-piles reliées par un pont salin ; chaque demi-pile = une électrode (métal) + un électrolyte (solution d'ions).\n• Cathode : borne POSITIVE, les électrons y arrivent, réduction (les ions se déposent sur l'électrode).\n• Anode : borne NÉGATIVE, les électrons en partent, oxydation (le métal passe en solution).\n• Dans les fils, les électrons vont de l'anode vers la cathode ; le courant va de la cathode vers l'anode.\n" + S_2NDE.format(2)},

{"titre": "pile daniell", "cles": [
    "pile daniell", "pile de daniell", "daniell", "zinc cuivre pile", "electrode de zinc", "electrode de cuivre"],
 "reponse": "Pile Daniell : couples Cu²⁺/Cu et Zn²⁺/Zn.\n• Cathode (borne +, lame de cuivre) : Cu²⁺ + 2e⁻ → Cu (réduction, gain d'électrons).\n• Anode (borne −, lame de zinc) : Zn → Zn²⁺ + 2e⁻ (oxydation, perte d'électrons).\nLes électrons circulent de l'anode (zinc) vers la cathode (cuivre) dans le circuit extérieur ; le courant va du cuivre vers le zinc.\n" + S_2NDE.format(2)},

{"titre": "types de piles", "cles": [
    "types de piles", "differents types de piles", "pile leclanche", "pile saline", "pile alcaline", "pile a l oxyde d argent", "pile bouton", "pile baton", "formats de piles",
    "difference pile saline alcaline", "pile salines et alcalines"],
 "reponse": "Trois formats courants : pile « bâton », « rectangulaire » et « bouton ».\n• Pile Leclanché (saline) : forme bâton, f.é.m. 1,5 V, pour usage intermittent et faibles besoins (réveils, jouets, lampes de poche, télécommandes).\n• Pile alcaline : formats bâton et bouton, f.é.m. 1,3 à 1,6 V ; durée de vie plus longue, courant plus intense, usage en continu (baladeurs, appareils portables).\n• Pile à l'oxyde d'argent : surtout en format bouton.\n" + S_2NDE.format(2)},

{"titre": "caracteristiques d une pile", "cles": [
    "caracteristiques d une pile", "quantite d electricite d une pile", "constante de faraday", "faraday", "q egale n f", "ampere heure en coulomb", "1 ah en coulombs", "capacite d une pile",
    "force electromotrice d une pile", "fem d une pile"],
 "reponse": "• f.é.m. E : tension aux bornes de la pile quand elle ne débite pas de courant (valeur positive).\n• Quantité d'électricité fournie : Q = N·e = n·F, avec n le nombre de moles d'électrons, F = 96 500 C/mol (constante de Faraday), N_A = 6,02 × 10²³ mol⁻¹, e = 1,60 × 10⁻¹⁹ C.\n• Q = I × t (I en A, t en s). Si t est en heures, Q est en ampères-heures (Ah) : 1 Ah = 3600 C.\nExemple : une pile qui débite 0,5 A pendant 2 h fournit Q = 1 Ah = 3600 C.\n" + S_2NDE.format(2)},

{"titre": "accumulateur", "cles": [
    "accumulateur", "accumulateurs", "pile rechargeable", "batterie d accumulateurs", "charge et decharge d un accumulateur", "types d accumulateurs", "accumulateur au plomb", "accumulateur nickel cadmium",
    "ni cd", "ni mh", "lithium ion", "li ion", "difference pile accumulateur", "accumulateurs alcalins"],
 "reponse": "Un accumulateur (pile rechargeable) est un système électrochimique réversible : il stocke l'énergie électrique sous forme chimique (charge) et la restitue ensuite (décharge). Une pile n'est pas rechargeable.\n• Batterie d'accumulateurs : ensemble d'accumulateurs associés pour obtenir la tension et la capacité voulues.\n• Types : au plomb ; alcalins : nickel-cadmium (Ni-Cd : les moins chers mais les plus polluants), nickel-hydrure métallique (Ni-MH : téléphones sans fil), lithium-ion (Li-ion : ordinateurs, téléphones, appareils photo).\n• Durée de vie bien supérieure aux piles : on peut les recharger un grand nombre de fois.\n" + S_2NDE.format(2)},

{"titre": "caracteristiques d un accumulateur", "cles": [
    "caracteristiques d un accumulateur", "capacite d une batterie", "capacite d un accumulateur", "energie d une batterie", "energie d un accumulateur", "w egale q max fois e",
    "batterie 12v 70ah", "12v 70ah 640a", "signification 12v 70ah", "tension a vide d un accumulateur", "courant maximal d une batterie", "calculer l energie d une batterie"],
 "reponse": "Caractéristiques d'un accumulateur :\n• Tension à vide E (= f.é.m.).\n• Capacité Q_max : quantité d'électricité maximale qu'il peut fournir, en ampères-heures (Ah).\n• Courant maximal I_max : intensité maximale qu'il peut fournir pendant une durée très courte.\n• Énergie disponible : W = Q_max × E (en Wh) ; 1 Wh = 3600 J.\nExemple : batterie « 12 V, 70 Ah, 640 A » → 12 V = tension à vide, 70 Ah = capacité, 640 A = courant maximal (de courte durée) ; énergie W = 70 × 12 = 840 Wh = 3,024 × 10⁶ J.\nModèle : générateur de f.é.m. E avec résistance interne r : U = E − r·I.\n" + S_2NDE.format(2)},

# ------------------------------------------------------------------ CHAPITRE 3
{"titre": "diode a jonction", "cles": [
    "diode a jonction", "la diode", "qu est ce qu une diode", "definition d une diode", "role d une diode", "symbole de la diode", "anode et cathode de la diode",
    "jonction pn", "semi conducteur p et n", "barriere de potentiel", "a quoi sert une diode", "diode"],
 "reponse": "Une diode est un dipôle passif non linéaire et polarisé : elle se comporte comme un interrupteur commandé qui ne laisse passer le courant que dans un seul sens (de l'anode vers la cathode).\n• Constitution : deux semi-conducteurs, type P et type N, séparés par une jonction, dans un boîtier. L'anode (A) est reliée à la région P, la cathode (K) à la région N. Le courant circule dans le sens P → N.\n• Sur le composant, la cathode est repérée par un trait.\n• Une barrière de potentiel V₀ existe à la jonction.\n• Régime statique : grandeurs continues ; régime dynamique : grandeurs alternatives.\n" + S_2NDE.format(3)},

{"titre": "polarisation de la diode", "cles": [
    "polarisation de la diode", "polarisation directe", "polarisation inverse", "polariser un composant", "diode polarisee en direct", "diode polarisee en inverse",
    "courant direct", "courant inverse", "tension zener", "tension de claquage", "effet d avalanche", "avalanche"],
 "reponse": "Polariser un composant, c'est appliquer une tension continue entre ses bornes.\n• Polarisation directe : anode au pôle +, cathode au pôle −. La barrière de potentiel diminue (V₀ − V) ; la diode conduit (courant direct ou passant).\n• Polarisation inverse : anode au pôle −, cathode au pôle +. La barrière augmente ; seul un très faible courant inverse (courant de saturation I_S) circule.\n• Si la tension inverse dépasse la tension de claquage (tension Zener V_Z), les liaisons de covalence se rompent : c'est l'effet d'avalanche. Dans une diode ordinaire, la jonction est alors détruite.\n" + S_2NDE.format(3)},

{"titre": "caracteristique de la diode", "cles": [
    "caracteristique de la diode", "caracteristique courant tension de la diode", "tension de seuil", "tension de seuil de la diode", "seuil d une diode", "courbe de la diode",
    "tension de seuil silicium", "tension de seuil germanium", "0 6 v diode"],
 "reponse": "Caractéristique courant-tension I_D = f(V_D) d'une diode au silicium :\n• 0 < V_D < 0,6 V : aucun courant.\n• V_D ≈ 0,6 V : le courant apparaît ; c'est la tension de seuil (≈ 0,2 V pour le germanium).\n• Au-delà : le courant monte d'abord lentement puis très vite.\n• Tension négative : le courant inverse atteint la valeur de saturation I_S puis reste constant jusqu'à V_Z ; au-delà de V_Z il monte brutalement et la diode est détériorée (avalanche).\n" + S_2NDE.format(3)},

{"titre": "resistance de la diode", "cles": [
    "resistance de la diode", "resistance dynamique", "resistance statique", "resistance dynamique d une diode", "rd egale delta v sur delta i", "25 mv", "25mv sur id"],
 "reponse": "Une diode a deux résistances :\n• Résistance statique : R_D = U/I (peu utile).\n• Résistance dynamique : r_d = ΔV/ΔI (variation de tension / variation de courant). À 27 °C : r_d = 25 mV / I_d(mA), en ohms.\nExemple : I_d = 5 mA → r_d = 25/5 = 5 Ω.\n" + S_2NDE.format(3)},

{"titre": "diode ideale parfaite reelle", "cles": [
    "diode ideale", "diode parfaite", "diode reelle", "diodes ideales", "diode ideale diode reelle", "circuit equivalent d une diode", "modele de la diode", "schema equivalent de la diode"],
 "reponse": "• Diode idéale : en direct, résistance nulle et V_D = 0 (interrupteur fermé) ; en inverse, résistance infinie (interrupteur ouvert).\n• Diode parfaite : résistance interne r_d nulle mais tension de seuil V₀ non nulle.\n• Diode réelle : résistance finie en direct, très élevée en inverse, et tension de seuil V₀ ≠ 0. Schéma équivalent : source V₀ en série avec r_D.\nAvec une tension alternative et une diode parfaite : si u > 0 la diode conduit et v = u ; si u < 0 elle est bloquée et v = 0 (redressement simple alternance).\n" + S_2NDE.format(3)},

{"titre": "groupement de diodes", "cles": [
    "groupement de diodes", "diodes en serie", "diodes en parallele", "association de diodes", "mettre des diodes en serie", "mettre des diodes en parallele"],
 "reponse": "• Diodes en série : pour redresser de très hautes tensions ; la chute de tension directe et la tension inverse maximale admissible s'additionnent (U_D = U_D1 + U_D2).\n• Diodes en parallèle : pour augmenter le courant redressé, réduire la chute de tension et répartir la chaleur dégagée.\nPrécaution : n'utiliser que des diodes de même référence, même fabricant, même série (même date de fabrication).\n" + S_2NDE.format(3)},

{"titre": "tester une diode", "cles": [
    "tester une diode", "test d une diode", "teste des diodes", "tester une diode au multimetre", "comment tester une diode", "verifier une diode", "diode silicium germanium multimetre", "0l multimetre"],
 "reponse": "Pour tester une diode, utilise un multimètre en position « test de diode » :\n• Dans le sens passant (anode vers cathode), il affiche une valeur proche de 0,6 V pour une diode silicium (1N4004) ; environ 0,2 V pour une diode au germanium (1N270).\n• Dans l'autre sens, le courant ne passe pas : l'appareil affiche « 0L », « -- » ou « 1 » (dépassement de calibre).\nSi la diode conduit ou bloque dans les deux sens, elle est défectueuse. La cathode est repérée par une bague/trait sur le boîtier.\n" + S_2NDE.format(3)},

{"titre": "diode zener", "cles": [
    "diode zener", "diode zéner", "la diode zener", "role de la diode zener", "regulation avec une diode zener", "stabilisation de tension zener", "stabiliser une tension", "montage zener",
    "zener", "tension de reference zener"],
 "reponse": "La diode Zener est conçue pour exploiter le claquage inverse ; sa tension de claquage est la tension Zener V_Z.\n• Directe : elle se comporte comme une diode normale.\n• Inverse : elle conduit quand la tension inverse dépasse V_Z, et la tension à ses bornes reste presque constante.\n• Rôles : réguler/stabiliser une tension, fournir une tension de référence constante, protéger les circuits contre les surtensions.\n• Montage stabilisateur : résistance R en série, Zener en parallèle sur la charge ; il faut E > V_Z pour que la sortie soit constante (stabilisée).\nExemple (R = 1 kΩ, V_Z = 6,2 V, charge R_c = 2,2 kΩ) : avec E = 12 V la Zener conduit et U_Rc = 6,2 V ; avec E = 8 V elle est bloquée (la tension à vide 5,5 V est < V_Z) et U_Rc = 8 × 2,2/3,2 = 5,5 V, non stabilisée. Pour E = 12 V, la résistance de charge minimale pour rester stabilisé est R_cmin = 6,2 V / 5,8 mA ≈ 1,07 kΩ.\n" + S_2NDE.format(3)},

# ------------------------------------------------------------------ CHAPITRE 4
{"titre": "electrisation par frottement", "cles": [
    "electrisation", "electrisation par frottement", "corps electrise", "charges electriques", "deux types de charges", "charge positive et negative", "verre et plastique frottes",
    "attraction et repulsion des charges", "charges de meme signe", "charges de signes contraires"],
 "reponse": "Un corps frotté qui attire des corps légers est électrisé : il porte une charge électrique.\n• Il existe deux types de charges : positive (verre frotté, par convention) et négative (plastique frotté).\n• Deux charges de même signe se repoussent ; deux charges de signes contraires s'attirent.\n• Électrisation = transfert d'électrons : un corps qui perd des électrons est chargé positivement (défaut d'électrons), un corps qui en gagne est chargé négativement (excès d'électrons).\n" + S_2NDE.format(4)},

{"titre": "proprietes des charges", "cles": [
    "conservation de la charge", "quantification de la charge", "proprietes des charges electriques", "charge elementaire e", "q egale n e", "atome neutre", "structure de l atome",
    "charge du proton", "charge de l electron", "coulomb unite de charge"],
 "reponse": "• Atome : noyau (protons positifs + neutrons sans charge) entouré d'électrons négatifs. Il a autant de protons que d'électrons : il est neutre.\n• Conservation : la charge ne peut être ni créée ni détruite ; elle se transfère seulement (par les électrons les moins liés).\n• Quantification : toute charge est un multiple entier de la charge élémentaire : q = n·e, avec e = 1,6 × 10⁻¹⁹ C (n entier relatif). Proton : +e ; électron : −e ; neutron : 0.\nExemple : 7×10¹³ protons et 4×10¹³ électrons → q = (7−4)×10¹³ × 1,6×10⁻¹⁹ = 4,8 × 10⁻⁶ C.\nUnité : le coulomb (C).\n" + S_2NDE.format(4)},

{"titre": "conducteurs et isolants", "cles": [
    "conducteurs et isolants", "difference conducteur isolant", "qu est ce qu un isolant", "qu est ce qu un conducteur", "exemples de conducteurs", "exemples d isolants", "materiaux isolants", "materiaux conducteurs"],
 "reponse": "• Isolants : les charges y circulent difficilement (ébonite, verre, porcelaine, plastiques, résines) ; une charge produite par frottement reste localisée à l'endroit frotté.\n• Conducteurs : les charges y circulent librement (cuivre, or, argent, aluminium, mercure, carbone, corps humain) ; la charge se répartit sur toute la surface.\n" + S_2NDE.format(4)},

{"titre": "loi de coulomb", "cles": [
    "loi de coulomb", "force de coulomb", "force electrostatique", "forces electrostatiques", "f egale k q1 q2 sur r carre", "constante de coulomb", "permittivite du vide", "epsilon zero",
    "force entre deux charges", "calculer la force entre deux charges", "charges ponctuelles"],
 "reponse": "Loi de Coulomb : deux charges ponctuelles immobiles dans le vide q₁ et q₂, distantes de r, s'exercent des forces égales et opposées :\nF = K × |q₁ q₂| / r²  (F en newtons, q en C, r en m)\navec K = 1/(4π ε₀) ≈ 9 × 10⁹ N·m²/C² et ε₀ = 8,854 × 10⁻¹² F/m.\n• Direction : la droite qui joint les deux charges.\n• Sens : répulsion si q₁ et q₂ ont le même signe, attraction si signes contraires.\n• Point d'application : F₁₂ s'applique en M₂, F₂₁ en M₁ ; F₁₂ = −F₂₁.\nExemple : q₁ = q₂ = 1 µC à 3 cm → F = 9×10⁹ × 10⁻¹² / 9×10⁻⁴ = 10 N.\n" + S_2NDE.format(4)},

{"titre": "superposition des forces", "cles": [
    "force exercee par plusieurs charges", "principe de superposition electrostatique", "superposition des champs", "resultante des forces electrostatiques", "somme vectorielle des champs",
    "force resultante sur une charge", "champ resultant"],
 "reponse": "Principe de superposition : quand plusieurs charges q₁, q₂, q₃… agissent sur une charge q, la force résultante est la somme vectorielle des forces : F = F₁ + F₂ + F₃ + … ; de même le champ résultant E = E₁ + E₂ + E₃ + … Chaque force vaut q·E_i.\nMéthode : calculer chaque norme avec la loi de Coulomb, construire les vecteurs avec une échelle, faire la somme vectorielle (graphiquement, ou avec les composantes) puis mesurer/calculer la norme de la résultante.\n" + S_2NDE.format(4)},

{"titre": "champ electrostatique", "cles": [
    "champ electrostatique", "champ electrique", "vecteur champ electrique", "champ cree par une charge ponctuelle", "e egale f sur q", "unite du champ electrique", "newton par coulomb",
    "charge temoin", "lignes de champ electrique", "ligne de champ electrique", "lignes de champ", "champ electrique uniforme"],
 "reponse": "Une charge source crée un champ électrique E dans l'espace ; une charge q placée en M subit la force F = q·E.\n• Norme : E = F/|q| (en N/C ou V/m). Pour une charge ponctuelle source q à la distance r : E = K·|q|/r².\n• Direction de F ; sens : celui de F si q > 0, opposé si q < 0.\n• Lignes de champ : courbes orientées dans le sens de E, tangentes à E ; elles ne se coupent jamais et ne sont pas fermées (elles partent d'une charge + et finissent sur une charge −, ou vont à l'infini). Divergentes autour d'une charge +, convergentes autour d'une charge −. Parallèles si le champ est uniforme.\nExemple : q_A = +4,5 µC à 3,2 m → E = 9×10⁹ × 4,5×10⁻⁶ / 3,2² ≈ 3955 N/C.\n" + S_2NDE.format(4)},

{"titre": "potentiel electrique", "cles": [
    "potentiel electrique", "potentiel cree par une charge", "v egale k q sur r", "difference de potentiel electrostatique", "superposition des potentiels", "calcul du potentiel electrique",
    "potentiel d une charge ponctuelle"],
 "reponse": "Le potentiel électrique (en volts) détermine l'état électrique d'un point d'un champ.\n• Charge ponctuelle q à la distance r : V(M) = k·q/r, avec k = 1/(4πε₀) = 9×10⁹ N·m²/C².\n• Superposition : V = Σ V_i = k·Σ q_i/r_i.\n• On ne mesure que des différences : ΔV = V(A) − V(B) = k·q (1/r_A − 1/r_B).\n• Remarque du cours : « si le champ est nul en un point, le potentiel y est nul » et « deux charges identiques donnent un potentiel nul à mi-chemin ». Attention : en physique, c'est vrai pour deux charges égales et de signes OPPOSÉS (potentiel nul au milieu) ; un champ nul n'implique pas toujours un potentiel nul (deux charges identiques : champ nul au milieu, potentiel non nul). À confirmer avec ton professeur.\n" + S_2NDE.format(4)},

{"titre": "travail de la force electrostatique", "cles": [
    "travail d une force electrostatique", "travail de la force electrostatique", "w ab egale q va moins vb", "travail dans un champ uniforme", "va moins vb egale e ab",
    "deplacement d une charge dans un champ"],
 "reponse": "Une charge q déplacée de A à B dans un champ électrique reçoit de la force électrostatique le travail :\nW_AB = q × (V_A − V_B)  (en joules).\nSi le champ est uniforme : W_AB = F·AB = q·E·AB, d'où V_A − V_B = E × AB.\n" + S_2NDE.format(4)},

{"titre": "energie potentielle electrostatique", "cles": [
    "energie potentielle electrostatique", "energie electrostatique", "ep egale q v", "energie d un systeme de deux charges", "energie potentielle de deux charges", "energie potentielle electrique"],
 "reponse": "• Charge q_A placée en A : Ep_A = q_A × V_A (en joules).\n• Système de deux charges q_A et q_B distantes de r : Ep = ½ (Ep_A + Ep_B) = q_A·q_B / (4π ε₀ r) = k·q_A·q_B / r.\nExemple (proton et particule alpha q = +2e à 20 cm) : V = 9×10⁹ × 1,6×10⁻¹⁹ / 0,2 = 7,2×10⁻⁹ V ; Ep = 9×10⁹ × (1,6×10⁻¹⁹ × 3,2×10⁻¹⁹) / 0,2 ≈ 2,3×10⁻²⁷ J.\n" + S_2NDE.format(4)},

{"titre": "le condensateur", "cles": [
    "condensateur", "le condensateur", "qu est ce qu un condensateur", "definition d un condensateur", "armatures dielectrique", "dielectrique", "role du condensateur",
    "symbole du condensateur", "a quoi sert un condensateur", "charge et decharge d un condensateur", "charge d un condensateur", "decharge d un condensateur", "condensateur charge", "condensateur decharge"],
 "reponse": "Un condensateur est formé de deux armatures conductrices en regard, séparées par un isolant (diélectrique : air, huile, papier, mica, céramique, polyester). Il peut être polarisé ou non. Son rôle principal : emmagasiner temporairement des charges et donc de l'énergie électrique.\n• Charge : K en position 1, un bref courant de charge circule, la tension finit par valoir E. Le condensateur garde sa charge q et sa tension même débranché.\n• Décharge : K en position 2, un courant de sens inverse apparaît, la tension tombe à 0.\n• Complètement chargé : plus de courant, il se comporte comme un interrupteur ouvert. Complètement déchargé : tension nulle, il se comporte comme un interrupteur fermé.\n" + S_2NDE.format(4)},

{"titre": "capacite d un condensateur", "cles": [
    "capacite d un condensateur", "capacite du condensateur", "q egale c u", "farad", "microfarad", "nanofarad", "picofarad", "condensateur plan", "capacite d un condensateur plan",
    "quantite d electricite d un condensateur", "charge d un condensateur formule", "calculer la capacite", "calculer la charge d un condensateur"],
 "reponse": "• Capacité : Q = C × U_C, donc C = Q/U_C (Q en coulombs, U en volts, C en farads F).\n• Sous-multiples : 1 µF = 10⁻⁶ F ; 1 nF = 10⁻⁹ F ; 1 pF = 10⁻¹² F (le farad est une très grande unité).\n• Condensateur plan : C = ε₀·ε_r·S / e (S : surface d'une armature, e : épaisseur du diélectrique, ε_r : permittivité relative). C augmente avec S et ε, diminue quand e augmente.\n• Courant et charge : i = dq/dt.\nExemples : 53 µF sous 12 V → Q = 636 µC ; S = 10 dm², e = 100 µm, ε_r = 7 (mica) → C ≈ 62 nF.\n" + S_2NDE.format(4)},

{"titre": "energie d un condensateur", "cles": [
    "energie d un condensateur", "energie emmagasinee dans un condensateur", "w egale un demi c u carre", "formule de l energie d un condensateur", "energie stockee condensateur", "calculer l energie d un condensateur"],
 "reponse": "Énergie emmagasinée par un condensateur chargé : W = ½ × C × U_C²  (W en joules, C en farads, U_C en volts). On a aussi W = ½·Q·U = Q²/(2C).\nExemple : C = 0,12 mF sous 100 V → W = 0,5 × 0,12×10⁻³ × 100² = 0,6 J ; sa charge Q = C·U = 12 mC.\n" + S_2NDE.format(4)},

{"titre": "association de condensateurs", "cles": [
    "association de condensateurs", "condensateurs en serie", "condensateurs en parallele", "condensateurs en derivation", "capacite equivalente", "capacite equivalente en serie", "capacite equivalente en parallele",
    "ceq", "c equivalent"],
 "reponse": "• En parallèle : C_éq = C₁ + C₂ + … + C_n ; même tension U sur tous ; les charges s'ajoutent (Q = Q₁ + Q₂ + …).\n• En série : 1/C_éq = 1/C₁ + 1/C₂ + … + 1/C_n ; même charge Q sur tous ; les tensions s'ajoutent (U = U₁ + U₂ + …), U_k = Q/C_k.\nExemples (E = 12 V) : 10 µF, 10 µF, 22 µF en série → C_éq ≈ 4,07 µF ; 10 pF + 10 pF + 33 pF + 33 pF en parallèle → 86 pF.\nAttention : c'est l'inverse des résistances (série ↔ parallèle).\n" + S_2NDE.format(4)},
]

# Fiches issues du cours « Électrotechnique 1ère F3/EL » (M. Gildas Zonakpo, LYTEB)
S_1ERE = "(Source : cours Électrotechnique 1ère F3/EL, chap. {})"

FICHES_1ERE = [
{"titre": "programme 1ere", "cles": [
    "programme de premiere", "programme de la premiere", "programme de 1ere", "programme de la 1ere", "programme 1ere", "cours de premiere", "cours de 1ere", "electrotechnique 1ere", "electrotechnique premiere", "que contient le cours de premiere"],
 "reponse": "Programme d'électrotechnique de 1ère F3/EL (cours de M. Gildas Zonakpo, LYTEB) :\n• Chap. 1 : grandeurs variables (périodiques, sinusoïdales, Fresnel, complexes).\n• Chap. 2 : circuits RLC en monophasé (impédance, résonance, puissances, facteur de puissance).\n• Chap. 3 : systèmes triphasés.\n• Chap. 4 : transistors à jonction ; amplificateur opérationnel.\n• Chap. 5 : magnétisme (champ, flux, Lorentz, Laplace).\n• Chap. 6 : ferromagnétisme (aimantation, hystérésis).\n• Chap. 7 : induction électromagnétique (Lenz, Faraday, auto-induction, mutuelle).\nDemande-moi une notion, par exemple « valeur efficace », « résonance », « couplage étoile » ou « loi de Faraday »."},

# ------------------------------------------------------------------ CHAPITRE 1
{"titre": "grandeur variable", "cles": [
    "grandeur variable", "grandeurs variables", "grandeur periodique", "grandeurs periodiques", "tension unidirectionnelle", "tension bidirectionnelle", "courant unidirectionnel",
    "courant bidirectionnel", "tension periodique", "signal periodique", "grandeur alternative", "qu est ce qu une grandeur variable"],
 "reponse": "Une grandeur variable est une grandeur dont la valeur change au cours du temps ; on la note en lettre minuscule (u, i).\n• Unidirectionnelle : toujours positive ou toujours négative.\n• Bidirectionnelle : oscille entre valeurs positives et négatives.\n• Périodique : se reproduit identique à elle-même à intervalles de temps réguliers : u(t + T) = u(t).\n• Sinusoïdale : périodique, qui évolue comme une sinusoïde.\n• Alternative : de valeur moyenne nulle.\n" + S_1ERE.format(1)},

{"titre": "periode et frequence", "cles": [
    "periode d un signal", "periode et frequence d un signal", "frequence d un signal", "la periode", "la frequence", "f egale 1 sur t", "valeur maximale", "valeur minimale", "oscillogramme",
    "base de temps", "sensibilite verticale", "sensibilite horizontale", "lire un oscillogramme", "oscilloscope", "calculer la frequence", "calculer la periode"],
 "reponse": "Caractéristiques d'une grandeur périodique :\n• Période T : plus petite durée qui sépare deux instants où la grandeur se reproduit identique (en secondes).\n• Fréquence f = 1/T (en hertz, Hz) : nombre de périodes par seconde.\n• Valeur maximale U_max et valeur minimale U_min.\nSur un oscilloscope (oscillogramme) : T = S_h × n_T (S_h : base de temps en s/div) ; U_max = S_v × n_M (S_v : sensibilité verticale en V/div).\nExemples : base de temps 0,5 ms/div, période sur 4 divisions → T = 2 ms, f = 500 Hz ; T = 2,5 ms → f = 400 Hz ; f = 16 2/3 Hz → T = 60 ms.\n" + S_1ERE.format(1)},

{"titre": "valeur moyenne", "cles": [
    "valeur moyenne", "valeur moyenne d une grandeur", "composante continue", "calculer la valeur moyenne", "umoy", "u moy", "moyenne d un signal", "moyenne d une tension"],
 "reponse": "Valeur moyenne d'une grandeur périodique u(t) : U_moy = A / T, où A est la somme algébrique des aires entre la courbe et l'axe du temps sur une période (aires sous l'axe comptées négativement). On l'appelle aussi composante continue.\n• Mesure : voltmètre numérique en position DC, appareil magnétoélectrique, ou oscilloscope (valeur max en DC moins valeur max en AC).\n• Une grandeur de valeur moyenne nulle est dite alternative.\nExemple : tension égale à 5 V pendant une demi-période et −3 V pendant l'autre → U_moy = (5 − 3)/2 = 1 V. Triangle entre 2 V et −5 V → U_moy = −1,5 V.\n" + S_1ERE.format(1)},

{"titre": "valeur efficace", "cles": [
    "valeur efficace", "valeur efficace d une grandeur", "calculer la valeur efficace", "rms", "root mean square", "ueff", "u eff", "tension efficace", "courant efficace", "efficace"],
 "reponse": "Valeur efficace d'une grandeur périodique : X = √⟨x²(t)⟩ (racine carrée de la moyenne du carré, RMS). C'est la valeur du courant continu qui dissipe la même puissance dans une résistance : P = R·I².\n• Mesure : appareil numérique RMS en position AC+DC, ou appareil ferromagnétique.\n• Signal avec composante continue : U_eff² = U_moy² + U_ond² (U_ond : valeur efficace de la partie variable).\nExemples : 5 V puis −3 V (demi-périodes égales) → U_eff = √((25 + 9)/2) = √17 ≈ 4,12 V ; triangle entre −U_max et +U_max → U_eff = U_max/√3.\n" + S_1ERE.format(1)},

{"titre": "grandeur sinusoidale", "cles": [
    "grandeur sinusoidale", "grandeurs sinusoidales", "tension sinusoidale", "courant sinusoidal", "expression d une tension sinusoidale", "u egale u max sin", "pulsation", "valeur crete",
    "valeur crete a crete", "phase a l origine", "amplitude", "phase initiale", "caracteristiques d une grandeur sinusoidale", "u max sur racine de 2", "umax sur racine de 2"],
 "reponse": "Grandeur sinusoïdale : u(t) = U_max · sin(ωt + φ).\n• U_max : amplitude (valeur crête) ; ω : pulsation en rad/s ; φ : phase à l'origine (rad) ; (ωt + φ) : phase à l'instant t.\n• ω = 2πf = 2π/T.\n• Valeur crête à crête : U_cc = 2·U_max ; valeur minimale : U_min = −U_max.\n• Valeur moyenne : 0 (une sinusoïde est alternative).\n• Valeur efficace : U = U_max/√2 (donc U_max = U√2).\nRéseau de la SBEE : f = 50 Hz (T = 0,02 s) ; monophasé U = 220 V (valeur donnée dans le cours), soit U_max = 220√2 ≈ 311 V.\nExemple : u(t) = 60 sin(200πt + π/3) → U_max = 60 V, ω = 200π ≈ 628 rad/s, f = 100 Hz, T = 10 ms, U = 42,4 V, U_cc = 120 V, φ = π/3.\n" + S_1ERE.format(1)},

{"titre": "representation de fresnel", "cles": [
    "representation de fresnel", "vecteur de fresnel", "diagramme de fresnel", "fresnel", "representation vectorielle", "vecteur associe a une grandeur sinusoidale"],
 "reponse": "Représentation de Fresnel : à la grandeur s(t) = S_max·sin(ωt + φ) on associe un vecteur S⃗ tournant à ω, dont la norme est S_max (ou, en électricité, la valeur efficace S) et dont l'angle avec l'axe (Ox) est la phase φ.\nOn peut ainsi additionner ou soustraire des grandeurs sinusoïdales de même fréquence en additionnant ou soustrayant leurs vecteurs (impossible pour multiplier ou diviser).\nExemple d'échelle : 1 cm pour 5/√2 V, ou 1 cm pour 1 A.\n" + S_1ERE.format(1)},

{"titre": "representation complexe", "cles": [
    "representation complexe", "nombre complexe associe", "nombres complexes", "forme algebrique", "forme polaire", "forme exponentielle", "forme trigonometrique", "module et argument",
    "notation complexe", "complexe en electricite", "j au carre egale moins 1"],
 "reponse": "À une grandeur sinusoïdale u(t) = U_max·sin(ωt + φ) on associe un nombre complexe U de module |U| = U (valeur efficace) et d'argument φ.\n• Forme algébrique : U = a + jb (avec j² = −1) ; a = U cos φ, b = U sin φ, U = √(a² + b²), tan φ = b/a.\n• Forme trigonométrique : U = U(cos φ + j sin φ).\n• Forme exponentielle : U = U·e^{jφ}.\n• Forme polaire : U = [U ; φ].\nOpérations : addition/soustraction en forme algébrique ; multiplication : modules multipliés, arguments additionnés ; division : modules divisés, arguments soustraits ; inverse : 1/[U ; φ] = [1/U ; −φ].\nExemple : U = 8 + 20j → |U| ≈ 21,5 V, φ ≈ 68,2°.\n" + S_1ERE.format(1)},

{"titre": "dephasage", "cles": [
    "dephasage", "dephasage entre deux grandeurs", "en phase", "opposition de phase", "quadrature", "en avance", "en retard", "decalage horaire", "avance et retard", "position de deux grandeurs sinusoidales",
    "calculer le dephasage"],
 "reponse": "Déphasage de u₂ par rapport à u₁ (même fréquence) : φ(u₂/u₁) = φ₁ − φ₂ (formule du cours).\n• φ = 0 : en phase ; φ = π : opposition de phase ; φ = ±π/2 : quadrature (retard ou avance).\n• φ < 0 : u₂ est en avance sur u₁ ; φ > 0 : u₂ est en retard sur u₁ (selon la convention du cours).\n• Décalage horaire : Δt = (φ/2π) × T (φ en rad, Δt en s).\n• Astuce : si u₁(0) > u₂(0), alors u₂ est en retard.\nExemple : u = 25 sin(100πt) et v = 20 sin(100πt + π/3) → φ(v/u) = 0 − π/3 = −π/3, donc v est en avance sur u.\n" + S_1ERE.format(1)},

{"titre": "operations sur les grandeurs sinusoidales", "cles": [
    "operations sur les grandeurs sinusoidales", "additionner des grandeurs sinusoidales", "somme de deux tensions sinusoidales", "addition de tensions sinusoidales", "methode complexe", "methode vectorielle",
    "ajouter deux sinusoides", "soustraire des grandeurs sinusoidales"],
 "reponse": "Pour additionner, soustraire, multiplier ou diviser des grandeurs sinusoïdales de même fréquence, détermine d'abord la valeur efficace et la phase à l'origine de chacune, puis choisis :\n• Méthode 1, vecteurs de Fresnel (addition/soustraction seulement) : un vecteur par grandeur, diagramme avec une échelle, vecteur résultant → valeur efficace et phase → expression temporelle.\n• Méthode 2, nombres complexes (toutes les opérations) : un complexe par grandeur, forme algébrique pour + et −, puis module et argument → expression temporelle.\nLe résultat a la même fréquence.\nFormules : |U₁+U₂| = √((a₁+a₂)² + (b₁+b₂)²) ; Arg(U₁×U₂) = φ₁ + φ₂ ; Arg(U₁/U₂) = φ₁ − φ₂.\n" + S_1ERE.format(1)},

# ------------------------------------------------------------------ CHAPITRE 2
{"titre": "impedance et admittance", "cles": [
    "impedance", "impedance complexe", "admittance", "reactance", "susceptance", "conductance et susceptance", "z egale u sur i", "impedance d un dipole", "qu est ce que l impedance",
    "difference impedance resistance"],
 "reponse": "• Impédance Z d'un dipôle : opposition au passage du courant alternatif. Z = U/I (complexe) ; module Z = U/I en Ω ; argument φ = φ_u − φ_i (déphasage du courant par rapport à la tension).\n• Forme algébrique : Z = R + jX ; R est la résistance (partie réelle), X la réactance (partie imaginaire), toutes deux en Ω.\n• Admittance : Y = 1/Z = I/U (en siemens) ; Y = G + jB ; G conductance, B susceptance ; argument θ = −φ.\n" + S_1ERE.format(2)},

{"titre": "impedance de r l et c", "cles": [
    "impedance d une bobine", "impedance d un condensateur", "impedance d un resistor", "reactance d une bobine", "reactance d un condensateur", "l omega", "1 sur c omega", "bobine parfaite",
    "condensateur parfait", "resistor pur", "nature d un dipole", "dipole inductif", "dipole capacitif", "dipole resistif", "z egale j l omega"],
 "reponse": "Impédances des dipôles élémentaires (ω = 2πf) :\n• Résistor pur : Z_R = R ; φ = 0.\n• Bobine parfaite : Z_L = jLω ; module Lω ; φ = +π/2 (+90°).\n• Condensateur parfait : Z_C = 1/(jCω) = −j/(Cω) ; module 1/(Cω) ; φ = −π/2 (−90°).\nNature du dipôle : φ > 0 ou X > 0 → inductif ; φ = 0 ou X = 0 → résistif ; φ < 0 ou X < 0 → capacitif.\nExemples : condensateur 6,8 µF sous 48 V–400 Hz → X_C = 1/(2π×400×6,8×10⁻⁶) ≈ 58,5 Ω, I ≈ 0,82 A ; bobine de 0,1 H à 50 Hz → X_L ≈ 31,4 Ω.\n" + S_1ERE.format(2)},

{"titre": "association de dipoles en regime sinusoidal", "cles": [
    "association d impedances", "impedances en serie", "impedances en parallele", "circuit rc serie", "circuit rlc serie", "circuit rlc parallele", "rlc serie", "rlc parallele", "rc serie",
    "rl serie", "impedance equivalente", "circuit rlc"],
 "reponse": "• Impédances en série : Z_éq = ΣZ_i. En parallèle : 1/Z_éq = Σ1/Z_i (ou Y_éq = ΣY_i).\n• Toutes les lois du continu (nœuds, mailles, diviseurs, Millman…) restent valables en régime sinusoïdal à condition d'utiliser les nombres complexes ou les vecteurs de Fresnel.\n• RLC série : Z = R + j(Lω − 1/(Cω)). Si Lω > 1/(Cω) : inductif ; si Lω = 1/(Cω) : résistif (résonance) ; si Lω < 1/(Cω) : capacitif.\n• RLC parallèle : Y = 1/R + j(Cω − 1/(Lω)).\nExemple : R = 100 Ω et C = 16 µF sous 230 V–50 Hz → X_C ≈ 198,9 Ω ; Z = √(100² + 198,9²) ≈ 222,7 Ω ; I ≈ 1,03 A ; la tension est en retard de ≈ 63,3° sur le courant (circuit capacitif).\n" + S_1ERE.format(2)},

{"titre": "resonance serie", "cles": [
    "resonance", "resonnance", "circuit bouchon", "resonance d intensite", "frequence de resonance", "frequence propre", "f0 egale 1 sur 2 pi racine de lc", "circuit rlc a la resonance",
    "phenomene de resonance", "surtension a la resonance", "lc omega zero carre egale 1"],
 "reponse": "Résonance d'intensité d'un circuit RLC série (tension efficace U constante, fréquence variable) : l'intensité efficace est maximale pour f = f₀.\n• Fréquence propre : LC·ω₀² = 1, donc f₀ = 1/(2π√(LC)).\n• Impédance à la résonance : Z₀ = R (minimale).\n• Courant : I₀ = U/R (maximal) ; déphasage φ₀ = 0 (circuit résistif).\n• Tensions : U_R0 = U ; U_L0 = U_C0 = Lω₀·I₀ = I₀/(Cω₀) (peuvent dépasser U : surtension).\nExemple (U = 20 V, L = 1 mH, C = 100 µF, R = 10 Ω) : f₀ ≈ 503 Hz ; ω₀ ≈ 3162 rad/s ; I₀ = 2 A ; U_R = 20 V ; U_L = U_C ≈ 6,3 V.\n" + S_1ERE.format(2)},

{"titre": "puissances en monophase", "cles": [
    "puissance active", "puissance reactive", "puissance apparente", "triangle des puissances", "puissance complexe", "puissance instantanee", "p egale u i cos phi", "q egale u i sin phi",
    "s egale u i", "var volt ampere reactif", "voltampere", "puissances en regime sinusoidal", "puissances en monophase", "facteur de puissance d un dipole"],
 "reponse": "Puissances d'un dipôle en régime sinusoïdal monophasé :\n• Instantanée : p(t) = u(t)·i(t).\n• Active : P = U·I·cos φ (en W) ; cos φ est le facteur de puissance.\n• Réactive : Q = U·I·sin φ (en var).\n• Apparente : S = U·I (en VA).\n• Complexe : S = U·I* = P + jQ.\n• Triangle des puissances : S² = P² + Q² ; cos φ = P/S ; tan φ = Q/P.\n• Dipôles purs : résistor P = RI², Q = 0 ; bobine parfaite Q_L = U²/(Lω) > 0 ; condensateur parfait Q_C = −CωU² < 0.\n• Rendement : η = P_utile / P_absorbée.\nExemple : moteur de 18 kW utiles, η = 0,9, cos φ = 0,65 → P_absorbée = 20 kW ; Q = 20 × tan φ ≈ 23,4 kvar ; S ≈ 30,8 kVA.\n" + S_1ERE.format(2)},

{"titre": "theoreme de boucherot", "cles": [
    "theoreme de boucherot", "boucherot", "bilan de puissances d une installation", "puissance totale d une installation", "courant absorbe par une installation", "facteur de puissance d une installation",
    "bilan de puissance installation monophasee"],
 "reponse": "Théorème de Boucherot : les puissances actives s'additionnent (P_T = P₁ + P₂ + …) et les puissances réactives s'additionnent (Q_T = Q₁ + Q₂ + …), quel que soit le groupement (série ou parallèle). Il ne vaut PAS pour la puissance apparente.\nPuis : S_T = √(P_T² + Q_T²) ; courant absorbé I = S_T/U (monophasé) ; facteur de puissance cos φ = P_T/S_T.\nMéthode : tableau (P, Q) de chaque récepteur → totaux → S_T, I, cos φ.\nAstuce : une lampe à incandescence a Q = 0 ; un condensateur a Q < 0 (il compense les moteurs).\n" + S_1ERE.format(2)},

{"titre": "relever le facteur de puissance", "cles": [
    "relever le facteur de puissance", "relevement du facteur de puissance", "ameliorer le facteur de puissance", "ameliorer cos phi", "compensation de l energie reactive", "compenser l energie reactive",
    "condensateur de compensation", "batterie de condensateurs", "capacite pour relever cos phi", "calculer la capacite de compensation", "relever cos phi"],
 "reponse": "Un faible facteur de puissance augmente le courant absorbé donc les pertes et la facture. Pour le relever, on diminue la puissance réactive en ajoutant un condensateur (ou une batterie) EN PARALLÈLE sur l'installation.\n• Monophasé : C = P_T·(tan φ − tan φ') / (ω·U²), ω = 2πf ; nouveau courant I' = P_T / (U·cos φ').\n• Triphasé, condensateurs en triangle : C_Δ = P_T·(tan φ − tan φ') / (3·U²·ω) ; en étoile : C_Y = P_T·(tan φ − tan φ') / (U²·ω) = 3·C_Δ ; nouveau courant I' = P_T / (√3·U·cos φ'). Le couplage triangle est plus avantageux (capacité 3 fois plus petite).\nExemple : P = 20 kW, 230 V–50 Hz, cos φ = 0,65 relevé à 0,95 → C ≈ 1011 µF ; le courant passe de ≈ 133,8 A à ≈ 91,5 A.\n" + S_1ERE.format(2)},

# ------------------------------------------------------------------ CHAPITRE 3
{"titre": "reseau triphase", "cles": [
    "reseau triphase", "systeme triphase", "tension simple", "tension composee", "tensions simples et composees", "u egale v racine de 3", "tension etoilee", "tension de ligne", "tension de phase",
    "reseau triphase equilibre direct", "avantages du triphase", "pourquoi le triphase", "u egale v racine 3", "difference tension simple composee"],
 "reponse": "Réseau triphasé : trois tensions sinusoïdales de même fréquence, déphasées de 120° (2π/3) les unes par rapport aux autres. Équilibré si elles ont la même valeur efficace ; direct si les phases se suivent dans le sens trigonométrique.\n• Tension simple v (étoilée, de phase) : entre une phase et le neutre ; valeur efficace V. v₁ + v₂ + v₃ = 0.\n• Tension composée u (de ligne) : entre deux phases ; valeur efficace U. u₁₂ + u₂₃ + u₃₁ = 0.\n• Relation : U = V√3.\nNotations : « 3×400 V » = tension composée 400 V ; « 230 V/400 V » = V/U (simple/composée).\nAvantages : machines moins coûteuses à puissance égale, lignes de transport qui perdent moins, plus d'énergie transportée avec la même masse de cuivre, deux tensions disponibles.\nExemples : 133 V/230 V → V = 133 V, U = 230 V ; 3×400 V → V ≈ 231 V.\n" + S_1ERE.format(3)},

{"titre": "couplage triphase", "cles": [
    "couplage etoile", "couplage triangle", "couplage en etoile", "couplage en triangle", "recepteur triphase", "courants de ligne", "courants de phase", "choix du couplage", "couplage d un moteur",
    "couplage impossible", "plaque signaletique couplage", "relations du couplage etoile", "relations du couplage triangle", "i egale j racine de 3", "comment coupler un moteur"],
 "reponse": "Récepteur triphasé : trois dipôles monophasés (équilibré s'ils sont identiques). Courants de ligne : i (dans les fils du réseau) ; courants de phase : j (dans les dipôles). Chaque dipôle supporte une tension simple (V) ou composée (U) selon le couplage.\n• Étoile (Y) : I = J ; U = V√3 ; J = V/Z (chaque dipôle reçoit la tension simple) ; le neutre n'est pas nécessaire (i₁ + i₂ + i₃ = 0).\n• Triangle (Δ) : U aux bornes de chaque dipôle ; J = U/Z ; I = J√3.\n• Choix du couplage (plaque signalétique : tension nominale du récepteur = la plus faible tension indiquée) : égale à la plus petite tension du réseau → étoile ; égale à la plus grande tension du réseau → triangle ; égale à aucune → couplage impossible.\nExemples : moteur 230/400 V sur réseau 230/400 V → étoile ; moteur 230/400 V sur 127/230 V → triangle ; moteur 400 V sur 230/400 V → triangle ; moteur 127/230 V sur 230/400 V → impossible sans transformateur.\nZ = 50 Ω sur 133/230 V : étoile → J = 2,66 A ; triangle → J = 4,6 A et I ≈ 7,97 A.\n" + S_1ERE.format(3)},

{"titre": "puissances en triphase", "cles": [
    "puissances en triphase", "puissance triphasee", "puissance d un moteur triphase", "p egale racine de 3 u i cos phi", "racine de 3 u i", "puissance active triphasee", "puissance reactive triphasee",
    "calculer le courant d un moteur triphase", "courant absorbe par un moteur triphase", "puissance en triphase"],
 "reponse": "Pour un récepteur triphasé équilibré, quel que soit le couplage (U : tension composée, I : courant de ligne) :\n• P = √3·U·I·cos φ (W)\n• Q = √3·U·I·sin φ (var)\n• S = √3·U·I (VA) ; S² = P² + Q² ; cos φ = P/S.\nDétail : étoile P = 3·V·I·cos φ ; triangle P = 3·U·J·cos φ. Purement capacitif : Q_C = −3CωU_C² en triangle (−CωU² en étoile) ; purement inductif : Q_L = 3U²/(Lω) en triangle (U²/(Lω) en étoile).\nExemple : moteur étoile sur 3×400 V, 2,8 kW, cos φ = 0,85 → tension par enroulement V ≈ 231 V ; I = 2800/(√3×400×0,85) ≈ 4,75 A (= courant d'enroulement) ; Q ≈ 1735 var.\n" + S_1ERE.format(3)},

{"titre": "mesure des puissances en triphase", "cles": [
    "methode des deux wattmetres", "deux wattmetres", "un seul wattmetre", "wattmetre", "mesure de la puissance en triphase", "mesurer la puissance triphasee", "varmetre", "p egale p1 plus p2",
    "q egale racine de 3 p1 moins p2"],
 "reponse": "En triphasé, la puissance active se mesure au wattmètre, la puissance apparente par la méthode volt-ampèremétrique, la puissance réactive au varmètre ou par calcul.\n• Un wattmètre (récepteur équilibré) : P = 3·P_mes ; Q = √3·P_mes (selon le branchement du cours).\n• Deux wattmètres : P = P₁ + P₂ ; Q = √3·(P₁ − P₂).\nExemple : P₁ = 500 W et P₂ = −200 W → P = 300 W ; Q ≈ 1212 var ; S ≈ 1249 VA ; cos φ ≈ 0,24 ; sur 3×400 V, I = S/(√3·U) ≈ 1,8 A.\n" + S_1ERE.format(3)},

{"titre": "bilan et compensation triphase", "cles": [
    "installation triphasee", "bilan de puissances triphase", "compensation triphasee", "relever cos phi en triphase", "batterie de condensateurs triphasee", "condensateurs en etoile ou en triangle",
    "courant absorbe par une installation triphasee", "facteur de puissance triphase"],
 "reponse": "Installation triphasée : le théorème de Boucherot s'applique (P_T = ΣP, Q_T = ΣQ).\n• S_T = √(P_T² + Q_T²) ; courant de ligne I = S_T/(U√3) ; cos φ = P_T/S_T.\n• Relèvement du cos φ : batterie de trois condensateurs identiques, en étoile ou en triangle, branchée aux bornes de l'installation. C_Δ = P_T(tan φ − tan φ')/(3U²ω) ; C_Y = P_T(tan φ − tan φ')/(U²ω) = 3·C_Δ. Nouveau courant I' = P_T/(√3·U·cos φ').\nLe triangle est préféré : condensateurs trois fois plus petits donc moins volumineux et moins chers.\nExemple : P = 20 kW, 3×400 V–50 Hz, cos φ = 0,7 relevé à 0,93 → C_Δ ≈ 83 µF par condensateur.\n" + S_1ERE.format(3)},

# ------------------------------------------------------------------ CHAPITRE 4
{"titre": "transistor bipolaire", "cles": [
    "transistor", "transistor bipolaire", "transistor a jonction", "transistor npn", "transistor pnp", "emetteur base collecteur", "effet transistor", "qu est ce qu un transistor",
    "symbole du transistor", "role du transistor", "a quoi sert un transistor", "ib ic ie", "ic egale beta ib", "gain en courant", "coefficient beta"],
 "reponse": "Un transistor bipolaire est un semi-conducteur à deux jonctions PN : il existe en NPN et en PNP (complémentaires). Trois bornes : émetteur (repéré par une flèche qui indique le sens du courant dans la jonction base-émetteur), base, collecteur.\n• Courants : I_E = I_B + I_C.\n• Gain en courant : β = I_C/I_B (en amplification : I_C = β·I_B) ; α = I_C/I_E ; relation entre eux : β = α/(1 − α).\n• Six grandeurs : I_B, I_C, I_E, V_CE, V_BE, V_CB.\n• Petit courant de base → grand courant de collecteur : le transistor amplifie.\nExemple du cours : I_B = 20 µA et I_C = 5 mA → β = 250.\n" + S_1ERE.format(4)},

{"titre": "polarisation du transistor", "cles": [
    "polarisation du transistor", "transistor sature", "transistor bloque", "polarisation mixte", "transistor en commutation", "transistor en amplification", "point de repos", "droite de charge",
    "droite de charge statique", "droite d attaque", "point de saturation", "point de blocage", "regime lineaire du transistor", "saturation et blocage", "interrupteur electronique",
    "puissance dissipee par un transistor", "montages fondamentaux du transistor", "emetteur commun"],
 "reponse": "Polarisation (deux jonctions : émetteur et collecteur) :\n• Directe (les deux diodes en direct) : transistor saturé. Inverse (les deux en inverse) : transistor bloqué. Mixte : l'une en direct (base-émetteur), l'autre en inverse (base-collecteur) : zone active, fonctionnement en amplification.\nDeux régimes :\n• Amplification (linéaire) : I_C = β·I_B ; jonction BE directe, jonction CB inverse.\n• Commutation : interrupteur électronique. Bloqué : I_B = 0, I_C = 0, V_CE = V_CC (interrupteur ouvert). Saturé : V_CE ≈ 0, I_C,sat = V_CC/R_C (interrupteur fermé), avec I_B,sat > I_C,sat/β_min.\nDroite d'attaque : caractéristique d'entrée I_B = f(V_BE). Droite de charge statique : tous les points de fonctionnement possibles en sortie (elle coupe l'axe I_C pour V_CE = 0 et l'axe V_CE pour I_C = 0). Point de repos Q : sur cette droite, dans la zone active entre saturation et blocage.\nPuissance dissipée : P_d = V_CE·I_C + V_BE·I_B ≈ V_CE·I_C ; ne pas dépasser P_dmax (hyperbole I_C = P_dmax/V_CE).\nLes trois montages fondamentaux : émetteur commun (le plus utilisé), base commune, collecteur commun.\n" + S_1ERE.format(4)},

{"titre": "amplificateur operationnel", "cles": [
    "amplificateur operationnel", "aop", "ampli op", "ali", "ao", "amplificateur lineaire integre", "entree inverseuse", "entree non inverseuse", "regime lineaire de l aop", "regime sature",
    "mode comparateur", "comparateur de tension", "reaction negative", "contre reaction", "reaction positive", "tension de saturation", "aop ideal", "brochage de l aop", "qu est ce qu un aop"],
 "reponse": "L'amplificateur opérationnel (AOP, ALI) est un circuit intégré amplificateur différentiel : il amplifie la différence de potentiel entre ses deux entrées. Bornes principales : entrée inverseuse E− (−), entrée non inverseuse E+ (+), sortie S (et deux broches d'alimentation ±). Symbole : triangle (anglo-saxon) ou rectangle (français).\nRéaction : sortie reliée à E− → réaction négative (contre-réaction) → mode LINÉAIRE ; sortie reliée à E+ → réaction positive → mode non linéaire.\n• Mode linéaire (AOP idéal) : V+ = V− (ε = 0) ; i+ = i− = 0 ; −V_sat < V_s < +V_sat.\n• Mode comparateur (saturé, boucle ouverte, pas de liaison sortie–E−) : si V+ > V− alors V_s = +V_sat ; si V+ < V− alors V_s = −V_sat.\nBroches du boîtier à 8 broches : 1 réglage de décalage, 2 entrée inverseuse, 3 entrée non inverseuse, 4 alimentation négative, 5 réglage de décalage, 6 sortie, 7 alimentation positive, 8 non connectée.\n" + S_1ERE.format(4)},

{"titre": "montages a aop", "cles": [
    "montage suiveur", "montage inverseur", "montage non inverseur", "aop inverseur", "aop non inverseur", "aop suiveur", "montage amplificateur inverseur", "amplificateur inverseur", "amplificateur non inverseur", "montage non inverseur", "montage sommateur", "montage additionneur", "montage soustracteur",
    "gain de l amplificateur inverseur", "gain du non inverseur", "montages lineaires avec l aop", "montages fondamentaux de l aop", "suiveur de tension", "vs egale moins r2 sur r1 ve"],
 "reponse": "Montages linéaires fondamentaux (AOP idéal, ε = 0, courants d'entrée nuls) :\n• Suiveur : V_s = V_e, gain A = 1 (adaptation d'impédance : il « suit » l'entrée).\n• Amplificateur non inverseur : V_s = (1 + R₂/R₁)·V_e (V_e sur E+, R₁ à la masse, R₂ en contre-réaction) ; gain ≥ 1, pas de déphasage.\n• Amplificateur inverseur : V_s = −(R₂/R₁)·V_e (V_e à travers R₁ sur E−) ; déphasage de 180° (π) entre v_e et v_s.\n• Sommateur inverseur : V_s = −R₃·(V₁/R₁ + V₂/R₂) ; si R₁ = R₂ = R : la sortie est la somme des entrées (au signe près, avec R₃ = R).\n• Soustracteur : avec R₁ = R₂ = R' = R, la sortie est la différence des deux tensions d'entrée (V_s = V⁺ − V⁻ côté entrées).\nMéthode : vérifier le régime linéaire, écrire ε = 0, nommer courants et tensions, appliquer nœuds/mailles/Millman, puis exprimer V_s en fonction de V_e. Le résultat n'est valable que si |V_s| < V_sat.\nExemple : R₁ = 1 kΩ, R₂ = 10 kΩ, V_e = 0,5 V → non inverseur : V_s = 5,5 V ; inverseur : V_s = −5 V.\n" + S_1ERE.format(4)},

# ------------------------------------------------------------------ CHAPITRE 5
{"titre": "aimant et magnetisme", "cles": [
    "aimant", "aimants", "magnetisme", "magnetisme definition", "pole nord et pole sud", "poles d un aimant", "aimant permanent", "aimant naturel", "aimant artificiel", "electroaimant", "magnetite",
    "proprietes des aimants", "monopole magnetique", "corps ferromagnetiques"],
 "reponse": "Le magnétisme est la propriété de certains corps d'attirer des corps ferromagnétiques (fer, nickel, cobalt, aciers). Un aimant attire, par exemple, un clou en fer.\n• Aimant naturel : la magnétite (minerai de fer). Aimants artificiels : permanents (barreau, aimant en U, aiguille aimantée) ou temporaires (électroaimants).\n• Tout aimant a deux pôles, Nord (N) et Sud (S) : deux pôles de même nom se repoussent, de noms différents s'attirent.\n• Les pôles vont toujours par paires : pas de monopôle (si on coupe un aimant en deux, on obtient deux aimants complets).\n• Suspendu, un aimant oriente son pôle N vers le nord géographique.\n• L'attraction est plus forte quand la distance est petite.\n" + S_1ERE.format(5)},

{"titre": "champ magnetique", "cles": [
    "champ magnetique", "vecteur champ magnetique", "induction magnetique", "tesla", "teslametre", "ligne de champ magnetique", "spectre magnetique", "champ magnetique uniforme", "champ magnetique terrestre",
    "superposition des champs magnetiques", "unite du champ magnetique", "caracteristiques du champ magnetique", "entrefer"],
 "reponse": "Le champ magnétique est la région de l'espace où s'exercent des actions magnétiques. En chaque point on définit un vecteur champ magnétique (induction) B⃗ :\n• Direction : tangente à la ligne de champ.\n• Sens : à l'extérieur d'un aimant, du pôle Nord vers le pôle Sud (c'est le sens sud→nord de l'aiguille aimantée).\n• Intensité B en tesla (T), mesurée au teslamètre (1 mT = 10⁻³ T).\nLignes de champ : courbes de N vers S à l'extérieur ; l'ensemble forme le spectre magnétique. Champ uniforme : mêmes caractéristiques en tout point (ex. entrefer d'un aimant en U).\nSuperposition : B⃗ = B⃗₁ + B⃗₂ (somme vectorielle). Pour deux champs de 20 mT : de même sens → 40 mT ; de sens opposés → 0 ; perpendiculaires → 28,3 mT.\nLe champ terrestre protège la surface de la Terre des particules solaires ; sa composante horizontale est B_h = B_T·cos θ (θ : inclinaison).\n" + S_1ERE.format(5)},

{"titre": "flux magnetique", "cles": [
    "flux magnetique", "flux d un champ magnetique", "weber", "phi egale b s cos alpha", "flux a travers une bobine", "flux traversant une spire", "calculer le flux magnetique", "flux a travers une surface"],
 "reponse": "Flux magnétique à travers une surface plane d'aire S dans un champ uniforme B : Φ = B·S·cos α, avec α l'angle entre B⃗ et le vecteur surface S⃗ (normale). Unité : le weber (Wb) ; S en m², B en T.\nPour une bobine de N spires : Φ = N·B·S·cos α.\nMaximal si le champ est perpendiculaire à la surface (α = 0) ; nul si le champ est parallèle à la surface (α = 90°).\nExemple : bobine de 500 spires de 3 cm × 2 cm, B = 50 mT, axe parallèle aux lignes de champ → Φ (une spire) = 0,05 × 6×10⁻⁴ = 3×10⁻⁵ Wb ; flux total = 500 × 3×10⁻⁵ = 15 mWb.\n" + S_1ERE.format(5)},

{"titre": "champ cree par un courant", "cles": [
    "champ cree par un courant", "champ magnetique d un fil", "champ magnetique d un solenoide", "champ magnetique d une spire", "solenoide", "bobine plate", "bobine torique", "excitation magnetique",
    "regle de la main droite", "main droite", "mu zero", "permeabilite du vide", "b egale mu zero n i sur l", "champ au centre d une bobine", "champ au centre d une spire", "sens du champ magnetique"],
 "reponse": "Tout conducteur parcouru par un courant crée un champ magnétique.\n• Fil rectiligne infini, à la distance d : B = 2×10⁻⁷ · I/d (pas de pôles ; lignes circulaires).\n• Centre d'une spire de rayon r : B = 2π×10⁻⁷ · I/r ; bobine plate de N spires : B = 2π×10⁻⁷ · N·I/r.\n• Solénoïde (bobine longue de N spires, longueur ℓ), champ uniforme à l'intérieur : B = μ₀·N·I/ℓ = μ₀·n·I = μ₀·H, avec μ₀ = 4π×10⁻⁷ SI et H = N·I/ℓ (excitation magnétique, en A/m). Le spectre extérieur ressemble à celui d'un barreau aimanté ; la bobine est un électroaimant.\n• Bobine torique : B = μ₀·N·I/ℓ avec ℓ = 2πR_m et R_m = (R_i + R_e)/2.\nSens de B⃗ : règle de la main droite — fil : pouce = sens du courant, doigts = sens de B⃗ ; spire/bobine : doigts = sens du courant, pouce = sens de B⃗ (il indique aussi la face Nord).\nExemples : 10 A à 10 cm d'un fil → B = 2×10⁻⁵ T ; bobine de 20 cm, 1000 spires, 2 A → H = 10 000 A/m et B ≈ 12,6 mT.\n" + S_1ERE.format(5)},

{"titre": "force de lorentz", "cles": [
    "force de lorentz", "loi de lorentz", "force magnetique sur une charge", "regle des trois doigts", "f egale q v b sin alpha", "particule chargee dans un champ magnetique", "produit vectoriel",
    "regle des 3 doigts de la main droite"],
 "reponse": "Loi de Lorentz : une particule de charge q de vitesse v⃗ dans un champ magnétique uniforme B⃗ subit la force F⃗ = q·v⃗ ∧ B⃗.\n• Direction : perpendiculaire à v⃗ et à B⃗.\n• Sens : règle des trois doigts de la main droite — pouce dans le sens de q·v⃗, index dans le sens de B⃗, majeur (perpendiculaire aux deux) = sens de F⃗.\n• Intensité : F = |q|·v·B·sin α (F en N, q en C, v en m/s, B en T, α angle entre v⃗ et B⃗).\nSymboles : ⊙ vecteur dirigé vers l'avant du plan, ⊗ vers l'arrière.\nExemple : v = 2×10⁴ m/s, B = 0,1 T, |q| = 1,6×10⁻¹⁹ C, α = 90° → F = 3,2×10⁻¹⁶ N.\n" + S_1ERE.format(5)},

{"titre": "force de laplace", "cles": [
    "force de laplace", "loi de laplace", "observateur d ampere", "regle de l observateur d ampere", "f egale b l i sin alpha", "force electromagnetique", "conducteur dans un champ magnetique",
    "force sur un conducteur parcouru par un courant", "rails de laplace"],
 "reponse": "Loi de Laplace : un conducteur rectiligne de longueur ℓ, parcouru par un courant I et placé dans un champ uniforme B⃗, subit la force F⃗ = I·ℓ⃗ ∧ B⃗ (ℓ⃗ : parallèle au conducteur, dans le sens du courant).\n• Point d'application : milieu de la portion de conducteur dans le champ.\n• Direction : perpendiculaire à ℓ⃗ et à B⃗.\n• Sens : règle de l'observateur d'Ampère (allongé sur le conducteur, courant des pieds vers la tête, il regarde dans le sens de B⃗ : la force est vers sa gauche) ou règle des trois doigts de la main droite (pouce = courant, index = B⃗, majeur = F⃗).\n• Intensité : F = B·ℓ·I·sin α ; maximale (F = B·ℓ·I) si le conducteur est perpendiculaire au champ.\nSens et direction de F⃗ = sens et direction du déplacement du conducteur.\nExemple : ℓ = 20 cm, I = 2 A, B = 0,010 T, perpendiculaires → F = 4×10⁻³ N. Travail : W = F × déplacement.\n" + S_1ERE.format(5)},

{"titre": "applications de la force de laplace", "cles": [
    "applications de la force de laplace", "haut parleur", "haut parleur electrodynamique", "moteur a courant continu principe", "principe du moteur electrique", "stator et rotor", "collecteur et balais",
    "principe du haut parleur", "fonctionnement d un haut parleur", "fonctionnement d un moteur a courant continu", "comment fonctionne un moteur electrique"],
 "reponse": "• Haut-parleur électrodynamique : aimant cylindrique avec un entrefer, bobine mobile dans l'entrefer, membrane fixée à la bobine. Le courant alternatif du signal audio change sans cesse de sens, donc la force de Laplace aussi : la bobine et la membrane vibrent d'avant en arrière à la même fréquence que le courant, ce qui fait vibrer l'air et produit le son. Énergie électrique → mécanique → acoustique.\n• Moteur à courant continu : stator (aimants ou électroaimants : crée le champ) et rotor (spires mobiles). Dans une spire, le courant circule en sens opposés de chaque côté : deux forces de Laplace créent un couple qui fait tourner la spire. Le collecteur inverse le courant à chaque demi-tour pour que le sens de rotation se maintienne ; les balais amènent le courant à la partie mobile. Énergie électrique → mécanique.\nAutres applications : galvanomètre, rails de Laplace, freinage électromagnétique.\n" + S_1ERE.format(5)},

# ------------------------------------------------------------------ CHAPITRE 6
{"titre": "ferromagnetisme", "cles": [
    "ferromagnetisme", "materiau ferromagnetique", "materiaux ferromagnetiques", "aimantation", "courbe d aimantation", "courbe de premiere aimantation", "permeabilite magnetique", "permeabilite relative",
    "b egale mu h", "b egale mu fois h", "excitation magnetique h", "saturation magnetique", "mu r"],
 "reponse": "Le ferromagnétisme est la capacité d'un corps (fer, acier, cobalt, nickel) à s'aimanter dans un champ magnétique.\n• Excitation magnétique H (A/m) : pour créer un champ B dans un matériau, on lui fournit H ; B = μ·H, μ : perméabilité magnétique.\n• Air, vide, milieux non ferromagnétiques (cuivre, aluminium) : μ = μ₀ = 4π×10⁻⁷ SI → B = μ₀H (courbe B = f(H) droite).\n• Milieux ferromagnétiques : μ = μ₀·μ_r (μ_r : perméabilité relative, souvent de plusieurs centaines à milliers) → le champ est fortement amplifié.\n• Courbe de première aimantation B = f(H) : le champ croît d'abord vite puis se sature.\nExemple : solénoïde de 800 spires sur 20 cm, B = 2,5 mT sans noyau → H = B/μ₀ ≈ 1990 A/m, I = H·ℓ/N ≈ 0,5 A ; avec un noyau d'acier B = 2 T → μ = B/H ≈ 1,0×10⁻³ SI, μ_r ≈ 800.\n" + S_1ERE.format(6)},

{"titre": "hysteresis", "cles": [
    "hysteresis", "cycle d hysteresis", "champ remanent", "excitation coercitive", "materiau doux", "materiau dur", "materiaux ferromagnetiques doux", "materiaux ferromagnetiques durs", "pertes par hysteresis",
    "desaimantation", "aimantation remanente", "difference materiau doux dur"],
 "reponse": "Hystérésis : retard à la désaimantation des matériaux ferromagnétiques. Quand H varie entre −H_max et +H_max, la courbe B = f(H) se dédouble : c'est le cycle d'hystérésis.\n• B_r : champ rémanent (le champ qui reste dans le matériau quand H = 0).\n• H_c : excitation coercitive (celle qu'il faut pour annuler B).\n• Conséquence : pertes d'énergie qui échauffent le matériau (proportionnelles à l'aire du cycle).\n• Matériaux doux : cycle étroit, B_r et H_c faibles, pertes faibles, s'aimantent et se désaimantent facilement → circuits magnétiques des machines (moteurs, génératrices, transformateurs).\n• Matériaux durs : cycle large, B_r et H_c importants, pertes importantes, s'aimantent difficilement → aimants permanents.\n" + S_1ERE.format(6)},

# ------------------------------------------------------------------ CHAPITRE 7
{"titre": "induction electromagnetique", "cles": [
    "induction electromagnetique", "induction", "courant induit", "fem induite", "f e m induite", "inducteur et induit", "phenomene d induction", "variation du flux", "causes de l induction",
    "qu est ce que l induction", "experience d induction"],
 "reponse": "Induction électromagnétique : la variation du flux magnétique à travers un circuit fermé fait apparaître une f.é.m. induite et un courant induit dans ce circuit (Faraday et Henry, vers 1830). Elle est à l'origine des générateurs, des transformateurs et des ondes électromagnétiques.\n• Inducteur : l'aimant ou l'électroaimant qui crée le champ. Induit : la boucle de conducteur (circuit fermé).\n• Comme Φ = B·S·cos α, le flux varie si B varie (aimant qui bouge, courant variable dans une bobine voisine, interrupteur qui s'ouvre ou se ferme), si la surface S varie (boucle déformée) ou si l'orientation α varie (boucle qui tourne).\n• Si le mouvement s'arrête, le courant induit s'annule.\n" + S_1ERE.format(7)},

{"titre": "lois de lenz et faraday", "cles": [
    "loi de lenz", "lenz", "loi de faraday", "faraday", "e egale moins d phi sur dt", "fem induite faraday", "sens du courant induit", "champ magnetique induit", "f e m moyenne induite",
    "e egale b l v", "fem dans un conducteur en mouvement", "courants de foucault", "foucault", "enonce de la loi de lenz", "enonce de la loi de faraday"],
 "reponse": "• Loi de Lenz : le sens du courant induit est tel que ses effets s'opposent à la cause qui lui donne naissance. Si le flux augmente, le champ induit B⃗_ind est de sens contraire au champ inducteur ; si le flux diminue, B⃗_ind a le même sens que B⃗.\n• Loi de Faraday : la f.é.m. induite est l'opposé de la dérivée du flux : e = −dΦ/dt. Valeur moyenne : E = ΔΦ/Δt = (Φ₂ − Φ₁)/(t₂ − t₁). Dans un circuit de résistance R : i = e/R.\n• Conducteur de longueur ℓ qui se déplace à la vitesse v dans un champ uniforme : e = B·ℓ·v.\n• Courants de Foucault : courants induits dans les masses métalliques en mouvement dans un champ fixe, ou fixes dans un champ variable. Utiles pour le freinage (ralentisseurs de camions), mais ils échauffent le métal (pertes).\nExemples : spire de 50 cm² dans B = 0,5 T, perpendiculaire (Φ = 2,5 mWb), qui tourne de 90° en 25 ms (Φ = 0) → |e| = 0,1 V. Tige sur rails : B = 200 mT, ℓ = 10 cm, v = 1,5 m/s → e = 0,03 V.\n" + S_1ERE.format(7)},

{"titre": "auto induction", "cles": [
    "auto induction", "autoinduction", "inductance", "inductance d une bobine", "henry", "flux propre", "f e m d auto induction", "energie d une bobine", "energie emmagasinee dans une bobine",
    "w egale un demi l i carre", "inductance d un solenoide", "bobine reelle", "bobine ideale", "retard d etablissement du courant", "e egale moins l di dt", "u egale l di dt"],
 "reponse": "Auto-induction : une bobine parcourue par un courant variable est traversée par son propre flux variable ; elle est à la fois inducteur et induit. Elle s'oppose (Lenz) à la variation du courant : le courant s'établit avec retard à la fermeture, et un bref courant apparaît à l'ouverture.\n• Flux propre : Φ_P = L·i ; L = inductance en henrys (H).\n• f.é.m. d'auto-induction : e = −dΦ_P/dt = −L·di/dt ; valeur moyenne E = L·Δi/Δt.\n• Bobine idéale : u = L·di/dt. Bobine réelle (résistance R en série) : u = R·i + L·di/dt.\n• Énergie emmagasinée : W = ½·L·i² (en J). La bobine stocke l'énergie en se chargeant (récepteur) et la restitue à l'ouverture (générateur).\n• Solénoïde sans noyau : L₀ = μ₀·N²·S/ℓ ; avec noyau ferromagnétique : L = μ_r·L₀.\nExemples : L = 0,15 H et W = 0,4 J → i ≈ 2,31 A (et si i est doublé, W est multiplié par 4) ; 5000 spires, 50 cm, 10 cm² → L ≈ 63 mH ; courant de 2 A coupé en 10 ms avec L = 0,05 H → |e| = 10 V (sans noyau).\n" + S_1ERE.format(7)},

{"titre": "induction mutuelle", "cles": [
    "induction mutuelle", "inductance mutuelle", "mutuelle induction", "coefficient d induction mutuelle", "bobine primaire", "bobine secondaire", "m egale phi sur i", "deux bobines couplees", "couplage de deux bobines"],
 "reponse": "Induction mutuelle : le courant variable i₁ dans une bobine crée un champ variable dont le flux Φ₁₋₂ à travers une deuxième bobine (N₂ spires) induit une f.é.m. e₂ dans celle-ci.\n• Φ₁₋₂ = M·i₁.\n• e₂ = −N₂·dΦ/dt = −M·di₁/dt ; valeur moyenne E₂ = M·Δi₁/Δt.\n• M : inductance mutuelle en henrys (H), constante pouvant être positive ou négative. Même relation en échangeant les rôles (e₁ = −M·di₂/dt).\nC'est le principe du transformateur.\nExemple : M = 3,7 H, courant qui décroît de 7 A à 3 A en 2 s → |E| = 3,7 × 4/2 = 7,4 V.\n" + S_1ERE.format(7)},
]


# ===== MOTEURS ET MACHINES ÉLECTRIQUES =====
SRC = "\n(Source : notions classiques d'électrotechnique — machines électriques ; fiche rédigée pour Novi IA)"
FICHES_MACHINES = [
{"titre": "types de moteurs", "cles": [
 "types de moteurs", "differents types de moteurs", "les differents moteurs", "quels sont les moteurs electriques", "classification des moteurs", "les moteurs electriques", "famille de moteurs", "categories de moteurs", "moteurs electriques types"],
 "reponse": "Les moteurs électriques se classent selon le courant d'alimentation :\n• Courant continu (MCC) : excitation séparée, shunt (dérivation), série, composée (compound). Vitesse facile à régler.\n• Courant alternatif triphasé : asynchrone (rotor à cage d'écureuil ou rotor bobiné à bagues, moteur Dahlander à 2 vitesses, moteur à enroulements séparés) et synchrone.\n• Courant alternatif monophasé : à condensateur, à bague de déphasage, moteur universel (fonctionne aussi en continu).\n• Petits moteurs de commande : pas à pas, brushless (sans balais).\nLe plus utilisé dans l'industrie et dans les ateliers : le moteur asynchrone triphasé à cage (robuste, peu cher, sans entretien)." + SRC},

{"titre": "moteur asynchrone triphase", "cles": [
 "moteur asynchrone triphase", "moteur asynchrone", "principe du moteur asynchrone", "fonctionnement du moteur asynchrone", "champ tournant", "vitesse de synchronisme", "vitesse synchrone", "glissement", "constitution du moteur asynchrone", "stator et rotor", "comment fonctionne un moteur asynchrone", "machine asynchrone"],
 "reponse": "Moteur asynchrone triphasé :\n• Stator : 3 enroulements décalés de 120° alimentés par le réseau triphasé ; ils créent un champ tournant.\n• Rotor : (cage ou bobiné) ; il est entraîné par le champ tournant et tourne toujours un peu moins vite : c'est pour cela qu'il est dit « asynchrone ».\n• Vitesse de synchronisme : n_s = f / p (en tr/s), soit n_s = 60·f / p (en tr/min). À 50 Hz : p = 1 → 3000 tr/min ; p = 2 → 1500 ; p = 3 → 1000 ; p = 4 → 750 tr/min.\n• Glissement : g = (n_s − n) / n_s (souvent 2 à 6 % en charge).\nExemple : moteur 4 pôles (p = 2) à 50 Hz, n = 1440 tr/min → n_s = 1500 tr/min et g = (1500 − 1440)/1500 = 0,04 = 4 %.\n• Pour inverser le sens de rotation : permuter deux phases." + SRC},

{"titre": "moteur a cage d ecureuil", "cles": [
 "moteur a cage d ecureuil", "cage d ecureuil", "rotor a cage", "moteur a cage", "rotor en court circuit", "moteur a rotor en court circuit", "moteur asynchrone a cage", "avantages du moteur a cage"],
 "reponse": "Moteur à cage d'écureuil :\n• Rotor : des barres de cuivre ou d'aluminium logées dans un empilage de tôles, reliées à chaque extrémité par un anneau de court-circuit. Pas de bagues ni de balais.\n• Avantages : très robuste, simple, peu coûteux, presque sans entretien.\n• Inconvénients : au démarrage direct le courant est très élevé (de l'ordre de 4 à 8 fois le courant nominal) et le couple de démarrage est moyen.\n• On limite l'appel de courant par un démarrage étoile-triangle, statorique, par démarreur progressif ou par variateur de fréquence.\n• Utilisation : pompes, ventilateurs, compresseurs, machines-outils, convoyeurs." + SRC},

{"titre": "moteur a rotor bobine", "cles": [
 "moteur a rotor bobine", "rotor bobine", "moteur a bagues", "moteur asynchrone a bagues", "demarrage rotorique", "rheostat rotorique", "demarrage par resistances rotoriques", "bagues et balais"],
 "reponse": "Moteur asynchrone à rotor bobiné (à bagues) :\n• Rotor : 3 enroulements triphasés dont les extrémités sortent par 3 bagues et 3 balais.\n• Démarrage rotorique : on insère un rhéostat (3 résistances) dans le circuit du rotor, puis on le court-circuite progressivement. Résultat : fort couple de démarrage avec un courant d'appel réduit.\n• Une fois le moteur lancé, les bagues sont court-circuitées et il fonctionne comme un moteur à cage.\n• Inconvénients : plus cher, plus encombrant, balais à entretenir.\n• Utilisation : levage, ponts roulants, concasseurs, machines à démarrage en charge." + SRC},

{"titre": "moteur dahlander et enroulements separes", "cles": [
 "moteur dahlander", "dahlander", "thailander", "dalander", "moteur thailander", "moteur a deux vitesses", "deux vitesses", "moteur a enroulement separe", "moteur a enroulements separes", "enroulement separe", "enroulements separes", "petite vitesse grande vitesse", "changement de vitesse du moteur", "couplage dahlander", "moteur a 2 vitesses"],
 "reponse": "Moteurs asynchrones à deux vitesses :\n• Moteur Dahlander (à pôles commutables) : un seul enroulement par phase avec 6 bornes (1U 1V 1W et 2U 2V 2W). On change le nombre de paires de pôles en changeant le couplage ; le rapport des vitesses est de 2.\n  – Petite vitesse : couplage triangle (Δ), réseau sur 1U 1V 1W, bornes 2U 2V 2W libres.\n  – Grande vitesse : couplage double étoile (YY), bornes 1U 1V 1W reliées entre elles, réseau sur 2U 2V 2W.\n  Exemple à 50 Hz : 750 / 1500 tr/min (8 et 4 pôles) ou 1500 / 3000 tr/min (4 et 2 pôles).\n• Moteur à enroulements séparés : deux enroulements indépendants dans le stator, avec des nombres de pôles différents (rapport de vitesses quelconque, par exemple 1000 / 1500 tr/min). Plus encombrant et plus cher.\n• Dans les deux cas : un contacteur par vitesse, avec verrouillage pour qu'ils ne soient jamais fermés en même temps." + SRC},

{"titre": "plaque a bornes et couplage", "cles": [
 "plaque a bornes", "plaque signaletique", "plaque signaletique moteur", "boite a bornes", "couplage du moteur", "couplage moteur etoile", "couplage moteur triangle", "comment coupler un moteur", "brancher un moteur triphase", "brancher un moteur", "comment brancher un moteur", "coupler un moteur", "moteur 230 400 sur reseau 400", "quel couplage pour mon moteur", "branchement d un moteur", "moteur 230 400", "moteur 400 690", "plaque 230 400 v", "lire la plaque d un moteur", "bornes u1 v1 w1", "etoile ou triangle moteur"],
 "reponse": "Plaque signalétique et plaque à bornes d'un moteur triphasé :\n• La plaque indique les deux tensions possibles, par exemple 230 / 400 V : la 1re est la tension supportée par un enroulement en triangle, la 2e en étoile (enroulement = 230 V).\n• Règle : chaque enroulement doit recevoir sa tension nominale (ici 230 V).\n  – Réseau 3 × 400 V + plaque 230/400 V → couplage ÉTOILE (Y).\n  – Réseau 3 × 230 V + plaque 230/400 V → couplage TRIANGLE (Δ).\n  – Réseau 3 × 400 V + plaque 400/690 V → couplage TRIANGLE (Δ) (et le démarrage étoile-triangle est possible).\n• Plaque à bornes : bornes du haut U1 V1 W1, bornes du bas W2 U2 V2.\n  – Étoile : on relie entre elles W2, U2, V2 par une barrette.\n  – Triangle : barrettes verticales U1–W2, V1–U2, W1–V2.\n• Lire aussi : puissance utile (kW), courant nominal In, cos φ, vitesse (tr/min), fréquence, indice de protection IP.\n• Pour inverser le sens : permuter deux phases." + SRC},

{"titre": "demarrage des moteurs asynchrones", "cles": [
 "demarrage des moteurs", "demarrage des moteurs asynchrones", "types de demarrage", "modes de demarrage", "pourquoi un demarrage particulier", "demarrage etoile triangle explication", "demarrage statorique", "demarreur progressif", "demarrage par autotransformateur", "appel de courant du moteur", "courant de demarrage", "demarrage direct du moteur", "reduire le courant au demarrage", "reduire le courant de demarrage", "limiter le courant de demarrage", "diminuer le courant de demarrage", "comment reduire le courant de demarrage"],
 "reponse": "Démarrage des moteurs asynchrones triphasés (le courant de démarrage vaut environ 4 à 8 fois In) :\n• Direct : le plus simple (contacteur seul) ; pour les petits moteurs ou les réseaux solides.\n• Étoile-triangle : démarrage en Y puis passage en Δ. Le courant de ligne et le couple sont divisés par 3. Condition : la tension du réseau doit être la tension de l'enroulement en triangle (ex. plaque 400/690 V sur réseau 400 V). Il faut 3 contacteurs (réseau, étoile, triangle) et un temporisateur.\n• Statorique : résistances (ou inductances) en série avec le stator, court-circuitées ensuite.\n• Par autotransformateur : tension réduite au démarrage.\n• Rotorique : pour le moteur à rotor bobiné (rhéostat).\n• Démarreur progressif : rampe de tension électronique, arrêt progressif possible.\n• Variateur de fréquence : démarre et règle la vitesse en faisant varier f et U." + SRC},

{"titre": "bilan de puissances du moteur asynchrone", "cles": [
 "bilan de puissances moteur asynchrone", "bilan de puissance du moteur asynchrone", "bilan de puissance moteur asynchrone", "bilan de puissance du moteur", "bilan de puissance", "bilan de puissances du moteur asynchrone", "bilan des puissances du moteur", "pertes du moteur asynchrone", "rendement du moteur asynchrone", "rendement d un moteur", "couple utile du moteur", "puissance absorbee du moteur", "puissance utile du moteur", "pertes joules du rotor", "puissance transmise au rotor", "calcul du rendement du moteur"],
 "reponse": "Bilan de puissances du moteur asynchrone triphasé :\n• Puissance absorbée : Pa = √3·U·I·cos φ.\n• Pertes stator : Pjs (joules, 3·R·I²) + Pfs (fer) → puissance transmise au rotor : Ptr = Pa − Pjs − Pfs.\n• Pertes joules rotor : Pjr = g × Ptr.\n• Puissance mécanique : Pm = Ptr − Pjr = (1 − g)·Ptr = Tem × Ω.\n• Pertes mécaniques pm → puissance utile : Pu = Pm − pm = Tu × Ω avec Ω = 2π·n/60.\n• Rendement : η = Pu / Pa.\nExemple (4 pôles, 50 Hz, n = 1440 tr/min → g = 0,04) : Pa = 5000 W ; Pjs + Pfs = 300 W → Ptr = 4700 W ; Pjr = 0,04 × 4700 = 188 W ; Pm = 4512 W ; pm = 100 W → Pu = 4412 W ; η = 4412/5000 ≈ 88,2 % ; Ω = 2π × 1440/60 ≈ 150,8 rad/s → Tu ≈ 29,3 N·m." + SRC},

{"titre": "moteur synchrone", "cles": [
 "moteur synchrone", "machine synchrone", "principe du moteur synchrone", "moteur synchrone triphase", "compensateur synchrone", "moteur a aimants permanents", "synchronisme"],
 "reponse": "Moteur synchrone triphasé :\n• Stator identique à celui de l'asynchrone (champ tournant).\n• Rotor : électro-aimant alimenté en courant continu (par bagues) ou aimants permanents.\n• Le rotor tourne exactement à la vitesse de synchronisme : n = f / p (pas de glissement). Si la charge est trop forte, il « décroche » et s'arrête.\n• Il ne démarre pas seul sur le réseau : démarrage en asynchrone (cage amortisseuse) ou par variateur.\n• Avantages : vitesse constante, rendement élevé, et il peut fournir de la puissance réactive (compensateur synchrone) pour relever le facteur de puissance.\n• Même machine que l'alternateur, qui fonctionne en génératrice." + SRC},

{"titre": "alternateur", "cles": [
 "alternateur", "alternateur triphase", "fonctionnement de l alternateur", "principe de l alternateur", "frequence d un alternateur", "generatrice synchrone", "comment fonctionne un alternateur", "constitution de l alternateur", "alternateur de centrale", "groupe electrogene alternateur"],
 "reponse": "Alternateur triphasé (machine synchrone fonctionnant en génératrice) :\n• Rotor (inducteur) : électro-aimant alimenté en courant continu (excitation) ou aimants, entraîné par une turbine ou un moteur thermique.\n• Stator (induit) : 3 bobines décalées de 120° où naissent trois tensions sinusoïdales décalées de 120° (système triphasé).\n• Fréquence : f = p × n (n en tr/s) ; n (tr/min) = 60·f / p.\n  Exemple : 1500 tr/min = 25 tr/s, p = 2 → f = 50 Hz.\n• La tension produite dépend de la vitesse et du courant d'excitation : on règle la tension en réglant l'excitation.\n• Utilisation : centrales (hydrauliques, thermiques), groupes électrogènes, éoliennes, alternateur de voiture (avec redresseur)." + SRC},

{"titre": "machine a courant continu", "cles": [
 "machine a courant continu", "moteur a courant continu", "moteurs a courant continu", "moteur a courant continue", "moteurs a courant continue", "machines a courant continu", "moteur courant continu", "mcc", "moteur cc", "constitution du moteur a courant continu", "collecteur et balais", "inducteur et induit", "excitation separee", "moteur a excitation separee", "moteur shunt", "moteur serie", "moteur compound", "moteur a excitation composee", "types d excitation", "demarrage moteur courant continu", "vitesse du moteur a courant continu", "inverser le sens du moteur a courant continu"],
 "reponse": "Machine à courant continu (MCC) :\n• Inducteur (stator) : crée le flux Φ (bobines d'excitation ou aimants).\n• Induit (rotor) : bobinage relié au collecteur ; le collecteur et les balais redressent mécaniquement le courant.\n• Types d'excitation : séparée, shunt (en dérivation), série, composée (shunt + série).\n• Moteur : U = E + R·I ; E = K·Φ·n (force contre-électromotrice) ; couple Tem = K'·Φ·I ; vitesse n = (U − R·I) / (K·Φ).\n• On règle la vitesse avec la tension d'induit U (ou le flux Φ).\n• Au démarrage E = 0, donc le courant Id = U / R serait énorme : on démarre sous tension réduite (rhéostat ou hacheur/variateur).\n• Moteur série : très fort couple au démarrage (traction, démarreurs), mais il ne faut jamais le faire fonctionner à vide (emballement).\n• Inversion du sens : inverser le courant dans l'induit OU dans l'inducteur (pas les deux)." + SRC},

{"titre": "generatrice a courant continu", "cles": [
 "generatrice a courant continu", "dynamo", "generatrice courant continu", "generatrice cc", "fonctionnement en generatrice", "amorcage de la generatrice", "caracteristique de la dynamo", "dynamo a courant continu"],
 "reponse": "Génératrice à courant continu (dynamo) :\n• Une MCC entraînée mécaniquement produit une f.é.m. E = K·Φ·n.\n• Tension aux bornes : U = E − R·I (la chute R·I diminue la tension quand le courant augmente, comme un générateur réel : voir modèle E, r).\n• Excitation séparée : on règle E (donc U) avec le courant d'excitation.\n• Génératrice shunt : elle s'auto-amorce grâce au flux rémanent du fer.\n• Aujourd'hui, on utilise surtout des alternateurs avec redresseur, mais le principe reste le même." + SRC},

{"titre": "moteur monophase", "cles": [
 "moteur monophase", "moteur a condensateur", "moteur monophase a condensateur", "moteur a bague de deplacement", "moteur universel", "moteur asynchrone monophase", "moteur de machine a laver", "moteur de perceuse", "moteur pas a pas", "moteur brushless"],
 "reponse": "Moteurs monophasés et petits moteurs :\n• Moteur asynchrone monophasé à condensateur : un enroulement principal + un enroulement auxiliaire en série avec un condensateur ; le condensateur décale le courant d'environ 90° et crée un champ tournant (démarrage possible). Pour inverser le sens : inverser les connexions de l'enroulement auxiliaire.\n• Moteur à bague de déphasage : petits ventilateurs, faible couple.\n• Moteur universel : fonctionne en courant continu ou alternatif monophasé, grande vitesse (perceuse, aspirateur, mixeur) ; balais à entretenir.\n• Moteur pas à pas : avance par pas d'angle à chaque impulsion (imprimantes, machines CNC).\n• Moteur brushless : aimants au rotor, commutation électronique, sans balais." + SRC},

{"titre": "essais du moteur asynchrone", "cles": [
 "essai a vide", "essai a vide du moteur", "essai en court circuit", "essai a rotor bloque", "essai du moteur asynchrone", "essais du moteur asynchrone", "essais de laboratoire moteur", "mesure de la resistance du stator", "mesure du glissement", "essai en charge du moteur", "tp moteur asynchrone", "manipulation moteur asynchrone"],
 "reponse": "Essais de laboratoire sur un moteur asynchrone :\n• Mesure de la résistance d'un enroulement du stator : en courant continu (méthode voltampèremétrique ou ohmmètre), moteur à froid ou à température connue.\n• Essai à vide : tension nominale, aucune charge. La vitesse est presque n_s (g ≈ 0), le courant I0 est faible et le facteur de puissance très faible. La puissance mesurée P0 ≈ pertes fer + pertes mécaniques (+ petites pertes joules stator).\n• Essai à rotor bloqué (court-circuit) : sous tension réduite jusqu'à obtenir le courant nominal. La puissance mesurée est alors pratiquement égale aux pertes joules (stator + rotor) ; on en déduit la résistance et la réactance de fuites ramenées.\n• Essai en charge : on mesure U, I, P (méthode des deux wattmètres), la vitesse n et le couple (frein ou génératrice) pour calculer η, g et cos φ.\n• Mesure de la vitesse : tachymètre ou stroboscope ; glissement g = (n_s − n)/n_s.\n• Mesure de puissance : voir la méthode des deux wattmètres (P = W1 + W2 ; Q = √3·(W1 − W2))." + SRC},

{"titre": "appareils de mesure", "cles": [
 "appareils de mesure", "appareils de mesure electrique", "voltmetre", "ampermetre", "amperemetre", "comment mesurer une tension", "comment mesurer un courant", "voltmetre et amperemetre", "wattmetre", "megohmmetre", "mesure d isolement", "pince amperemetrique", "multimetre", "ohmmetre", "branchement du voltmetre", "branchement de l amperemetre", "mesure sur le laboratoire", "materiel de laboratoire d electrotechnique"],
 "reponse": "Appareils de mesure en électrotechnique et branchement :\n• Ampèremètre (A) : en SÉRIE dans le circuit ; résistance interne très faible. Jamais en parallèle (court-circuit).\n• Voltmètre (V) : en DÉRIVATION (parallèle) aux bornes du dipôle ; résistance interne très grande.\n• Wattmètre (W) : bobine de courant en série, bobine de tension en dérivation ; il mesure P = U·I·cos φ. En triphasé : deux wattmètres (P = W1 + W2).\n• Ohmmètre : mesure de résistance, circuit hors tension.\n• Mégohmmètre : mesure l'isolement (moteur, câbles) sous tension d'essai (500 V ou plus), circuit hors tension et déconnecté.\n• Pince ampèremétrique : mesure le courant sans ouvrir le circuit.\n• Multimètre : tension, courant, résistance, continuité, diode.\n• Bonnes pratiques : choisir le bon calibre (commencer par le plus grand), vérifier le type de courant (continu ou alternatif), respecter la sécurité (hors tension pour câbler)." + SRC},

{"titre": "protection d un moteur", "cles": [
 "protection d un moteur", "protection du moteur", "protection des moteurs", "circuit de puissance du moteur", "schema de puissance du moteur", "relais thermique", "reglage du relais thermique", "contacteur", "sectionneur", "fusibles am", "disjoncteur moteur", "disjoncteur magnetothermique moteur", "comment proteger un moteur"],
 "reponse": "Protection et commande d'un départ moteur (circuit de puissance) :\n• Sectionneur porte-fusibles (Q1) : isole le départ et protège contre les courts-circuits (fusibles de type aM, adaptés aux moteurs).\n• Contacteur (KM1) : commande la marche et l'arrêt, commandé par le circuit de commande (boutons, bobine).\n• Relais thermique (F1) : protège contre les surcharges ; on le règle sur le courant nominal In lu sur la plaque du moteur.\n• Un disjoncteur-moteur (magnétothermique) peut remplacer à lui seul sectionneur, fusibles et relais thermique.\n• Le circuit de commande contient aussi : bouton d'arrêt, bouton de marche, contact auxiliaire d'auto-maintien et contact du relais thermique.\n• Autres protections : différentiel, terre (PE), sondes thermiques (CTP) dans les enroulements." + SRC},
]

for _fiche in FICHES_MACHINES:
    _fiche["mot_entier"] = True
CONNAISSANCES_GENERALES.extend(FICHES_MACHINES)

for _fiche in FICHES_2NDE + FICHES_1ERE:
    _fiche["mot_entier"] = True
CONNAISSANCES_GENERALES.extend(FICHES_2NDE)
CONNAISSANCES_GENERALES.extend(FICHES_1ERE)

# ===== BÉNIN : culture, tourisme, langues, gouvernement, géographie =====
FICHES_BENIN = [{'titre': 'benin presentation', 'cles': ['benin', 'le benin', 'presente le benin', 'presentation du benin', 'parle moi du benin', 'parle du benin', 'dis moi des choses sur le benin', 'informations sur le benin', 'le benin en general', 'renseignements sur le benin', 'c est quoi le benin', 'qu est ce que le benin', 'fiche pays benin', 'carte d identite du benin', 'faits sur le benin', 'benin en bref', 'benin general'], 'reponse': "Le Bénin (République du Bénin), ancien Dahomey, est un pays d'Afrique de l'Ouest sur le golfe de Guinée.\n• Capitale officielle : Porto-Novo ; capitale économique et plus grande ville : Cotonou.\n• Superficie : 114 763 km² ; population : environ 14 millions d'habitants (14,1 millions en 2023).\n• Langue officielle : le français ; langues nationales : fon, yoruba, bariba, dendi, adja, goun, etc.\n• Monnaie : le franc CFA (FCFA, code XOF).\n• Indépendance : 1er août 1960 (ancienne colonie française) ; fête nationale le 1er août.\n• Régime : république présidentielle ; Constitution du 11 décembre 1990.\n• Devise : « Fraternité – Justice – Travail » ; hymne : « L'Aube nouvelle ».\n• Voisins : Togo (ouest), Nigeria (est), Burkina Faso et Niger (nord) ; côte atlantique au sud.\n• Berceau du vodun, avec une riche histoire (royaume du Dahomey) et des sites touristiques majeurs (Ouidah, Abomey, Ganvié, Pendjari).\nDis-moi ce que tu veux approfondir : géographie, histoire, culture, tourisme, langues, gouvernement…\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'geographie du benin', 'cles': ['geographie du benin', 'situation geographique du benin', 'ou se trouve le benin', 'ou est le benin', 'position du benin', 'relief du benin', 'climat du benin', 'fleuves du benin', 'cours d eau du benin', 'limites du benin', 'frontieres du benin', 'pays voisins du benin', 'voisins du benin', 'point culminant du benin', 'plus haute montagne du benin', 'saisons au benin', 'vegetation du benin', 'les regions naturelles du benin', 'benin geographie'], 'reponse': "Géographie du Bénin :\n• Situation : Afrique de l'Ouest, entre 6° et 12° de latitude nord environ ; pays long et étroit (environ 670 km du nord au sud) avec une côte d'environ 120 à 125 km sur l'océan Atlantique (golfe de Guinée).\n• Voisins : Togo à l'ouest, Nigeria à l'est, Burkina Faso et Niger au nord (le fleuve Niger marque une partie de la frontière avec le Niger).\n• Relief : surtout des plaines et plateaux ; la chaîne de l'Atacora au nord-ouest est la principale zone de hauteur (point culminant : le mont Sokbaro, environ 658 m).\n• Cours d'eau : l'Ouémé (le plus long fleuve du pays), le Mono, le Couffo, la Pendjari, le Niger au nord ; lacs et lagunes : lac Nokoué, lac Ahémé, lagune de Porto-Novo.\n• Climat : au sud, subéquatorial avec deux saisons des pluies (mars-juillet et septembre-novembre) ; au nord, soudanien avec une saison des pluies (mai-octobre) et une saison sèche marquée ; l'harmattan, vent sec et poussiéreux, souffle de décembre à février.\n• Paysages : mangroves et lagunes au sud, savanes au centre et au nord, forêts classées et parcs nationaux.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'superficie et population du benin', 'cles': ['superficie du benin', 'surface du benin', 'taille du benin', 'quelle est la superficie du benin', 'combien de km2 fait le benin', 'population du benin', 'combien d habitants au benin', 'nombre d habitants du benin', 'habitants du benin', 'densite du benin', 'demographie du benin', 'chiffres du benin', 'le benin en chiffres', 'combien de beninois'], 'reponse': "Le Bénin en chiffres :\n• Superficie : 114 763 km² (environ 115 000 km²), soit à peu près la taille de Cuba ; longueur nord-sud d'environ 670 km.\n• Population : environ 14 millions d'habitants (14,1 millions en 2023), soit une densité d'environ 85 habitants/km². Elle est plus dense dans le sud (Cotonou, Abomey-Calavi, Porto-Novo) que dans le nord.\n• Environ la moitié de la population vit en ville.\n• Population très jeune : une grande partie des Béninois a moins de 25 ans.\n• Côte : environ 120 km ; 12 départements et 77 communes.\n• Le dernier recensement général (RGPH) date de 2013 ; les chiffres plus récents sont des estimations.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'departements et communes du benin', 'cles': ['departements du benin', 'les departements du benin', 'combien de departements au benin', 'liste des departements du benin', 'communes du benin', 'combien de communes au benin', 'chefs lieux des departements', 'chef lieu de departement benin', 'decoupage administratif du benin', 'organisation territoriale du benin', 'les 12 departements du benin', 'regions du benin', 'chef lieu du mono', 'chef lieu du zou', 'chef lieu du borgou', 'chef lieu de l atacora', 'chef lieu du plateau', 'chef lieu de la donga', 'chef lieu de l alibori', 'chef lieu des collines', 'chef lieu du couffo', 'chef lieu de l atlantique', 'chef lieu de l oueme'], 'reponse': 'Le Bénin compte 12 départements et 77 communes. Départements et chefs-lieux :\n• Alibori : Kandi\n• Atacora : Natitingou\n• Atlantique : Allada\n• Borgou : Parakou\n• Collines : Dassa-Zoumè\n• Couffo : Aplahoué\n• Donga : Djougou\n• Littoral : Cotonou\n• Mono : Lokossa\n• Ouémé : Porto-Novo\n• Plateau : Pobè\n• Zou : Abomey\nChaque département est dirigé par un préfet ; chaque commune par un maire élu. Le Littoral est formé de la seule ville de Cotonou.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)'}, {'titre': 'villes du benin', 'cles': ['villes du benin', 'grandes villes du benin', 'principales villes du benin', 'plus grande ville du benin', 'capitale du benin', 'quelle est la capitale du benin', 'porto novo ou cotonou', 'difference porto novo cotonou', 'capitale economique du benin', 'capitale politique du benin', 'ville la plus peuplee du benin', 'parakou', 'abomey calavi', 'djougou', 'natitingou', 'kandi', 'lokossa', 'bohicon', 'ou est bohicon'], 'reponse': "Villes du Bénin :\n• Porto-Novo : capitale officielle (siège de l'Assemblée nationale), sur la lagune ; ville d'art et d'histoire.\n• Cotonou : capitale économique, grand port et aéroport international ; siège de la présidence (Palais de la Marina) et des ministères ; la plus grande ville (près de 700 000 habitants en 2013, bien davantage avec l'agglomération).\n• Abomey-Calavi : commune la plus peuplée avec Cotonou, siège de l'Université d'Abomey-Calavi, juste au nord de Cotonou.\n• Parakou : grande ville du nord, carrefour commercial (3e ville du pays).\n• Abomey et Bohicon : ancienne capitale du royaume du Dahomey et ville voisine, carrefour ferroviaire et routier (Bohicon : lycée technique LYTEB).\n• Ouidah : ville historique et vodun, sur la côte.\n• Djougou, Natitingou, Kandi : villes du nord-ouest et du nord ; Lokossa : chef-lieu du Mono (à l'ouest du sud).\nNe confonds pas : Porto-Novo est la capitale administrative officielle, Cotonou est la capitale économique.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'drapeau et symboles du benin', 'cles': ['drapeau du benin', 'couleurs du drapeau du benin', 'drapeau beninois', 'symboles du benin', 'devise du benin', 'hymne national du benin', 'hymne du benin', 'l aube nouvelle', 'quelle est la devise du benin', 'armoiries du benin', 'embleme du benin', 'que signifient les couleurs du drapeau', 'fraternite justice travail', 'symboles nationaux du benin'], 'reponse': "Symboles du Bénin :\n• Drapeau : vert (bande verticale à gauche), jaune (en haut à droite) et rouge (en bas à droite). D'après l'interprétation usuelle : vert = l'espérance et le renouveau, jaune = la richesse du pays, rouge = le courage des ancêtres. Le drapeau actuel a été rétabli en 1990.\n• Devise : « Fraternité – Justice – Travail ».\n• Hymne national : « L'Aube nouvelle ».\n• Fête nationale : le 1er août (indépendance, 1960).\n• Monnaie : le franc CFA (XOF) ; 1 euro = 655,957 FCFA (parité fixe).\nTape « schéma du drapeau du Bénin » pour le voir.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'histoire du benin', 'cles': ['histoire du benin', 'histoire du dahomey', 'le dahomey', 'ancien nom du benin', 'pourquoi le dahomey est devenu benin', 'independance du benin', 'quand le benin est devenu independant', 'date de l independance du benin', 'colonisation du benin', 'colonisation du dahomey', 'resume de l histoire du benin', 'conference nationale de 1990', 'conference nationale du benin', 'kerekou', 'mathieu kerekou', 'regime marxiste au benin', 'historique du benin', 'benin histoire', '1er aout 1960'], 'reponse': "Histoire du Bénin (repères) :\n• XVIIe-XIXe siècles : le royaume du Dahomey, avec Abomey pour capitale, devient une puissance régionale ; c'est aussi une période de traite des esclaves (ports de Ouidah, Porto-Novo).\n• 1892-1894 : guerres franco-dahoméennes ; le roi Béhanzin résiste, puis est vaincu et exilé. Le Dahomey devient colonie française (Afrique-Occidentale française).\n• 1er août 1960 : indépendance du Dahomey ; premier président : Hubert Maga.\n• 1960-1972 : forte instabilité politique, plusieurs coups d'État.\n• 1972 : Mathieu Kérékou prend le pouvoir ; régime marxiste-léniniste. 1975 : le pays est rebaptisé République populaire du Bénin.\n• Février 1990 : Conférence nationale des forces vives de la Nation, qui ouvre la transition démocratique (modèle suivi par d'autres pays africains) ; Constitution de décembre 1990 ; le pays devient République du Bénin.\n• 1991 : Nicéphore Soglo est élu ; 1996-2006 : retour de Kérékou ; 2006-2016 : Thomas Boni Yayi ; 2016-2026 : Patrice Talon ; 2026 : Romuald Wadagni.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'royaume du dahomey', 'cles': ['royaume du dahomey', 'rois du dahomey', 'les rois d abomey', 'liste des rois du dahomey', 'dynastie d abomey', 'roi behanzin', 'behanzin', 'qui est behanzin', 'roi glele', 'glele', 'roi agadja', 'agadja', 'agaja', 'houegbadja', 'roi houegbadja', 'les amazones du dahomey', 'amazones', 'agojie', 'les amazones', 'femmes guerrieres du dahomey', 'guerrieres du dahomey', 'armee du dahomey'], 'reponse': "Royaume du Dahomey (XVIIe-XIXe siècles), capitale Abomey :\n• Rois principaux : Houégbadja (fondateur de la dynastie, XVIIe s.), Agadja (début XVIIIe s., conquiert Allada puis Ouidah vers 1727, ouvrant l'accès à la mer), Tégbessou, Guézo (1818-1858, qui rend le royaume indépendant et puissant), Glèlè, Béhanzin (1889-1894), dernier grand roi, puis Agoli-Agbo.\n• Béhanzin résiste aux troupes françaises en 1890 puis 1892-1894 ; vaincu, il est exilé en Martinique puis en Algérie, où il meurt en 1906.\n• Les Amazones (Agojié, ou Mino, « nos mères ») : régiment de femmes guerrières du Dahomey, célèbres pour leur discipline et leur courage ; elles ont combattu les troupes françaises.\n• Les palais royaux d'Abomey, classés par l'UNESCO, gardent ce passé (bas-reliefs, trônes, musée d'histoire).\n• En 1892, des trésors royaux ont été emportés en France ; 26 œuvres ont été restituées au Bénin en novembre 2021.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'gouvernement et institutions du benin', 'cles': ['gouvernement du benin', 'le gouvernement du benin', 'institutions du benin', 'regime politique du benin', 'systeme politique du benin', 'constitution du benin', 'pouvoirs au benin', 'pouvoir executif du benin', 'pouvoir legislatif du benin', 'pouvoir judiciaire du benin', 'assemblee nationale du benin', 'cour constitutionnelle du benin', 'le benin est une republique', 'quel est le regime du benin', 'politique au benin', 'palais de la marina', 'siege de la presidence du benin', 'info gouvernement benin', 'information gouvernement benin', 'organisation de l etat beninois', 'senat du benin', 'deputes du benin', 'premier ministre du benin'], 'reponse': "Institutions du Bénin :\n• Régime : république présidentielle ; le président de la République est à la fois chef de l'État et chef du gouvernement (il n'y a pas de Premier ministre dans l'organisation en vigueur au Bénin).\n• Constitution : adoptée par référendum en décembre 1990 (promulguée le 11 décembre 1990), plusieurs fois révisée ; des réformes constitutionnelles récentes ont modifié notamment la durée des mandats et les institutions (vérifie les textes à jour).\n• Exécutif : le président, élu au suffrage universel direct, et le gouvernement (ministres nommés par le président). Siège : Palais de la Marina, à Cotonou.\n• Législatif : l'Assemblée nationale, à Porto-Novo (109 députés depuis 2023, à vérifier).\n• Judiciaire : Cour constitutionnelle (contrôle de la constitutionnalité et des élections), Cour suprême, cours et tribunaux.\n• Sites officiels : gouv.bj (gouvernement) et presidence.bj.\nPour le président actuel, demande « qui est le président du Bénin ? ».\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'president du benin', 'cles': ['president du benin', 'qui est le president du benin', 'president actuel du benin', 'le president du benin', 'chef de l etat du benin', 'chef de l etat beninois', 'qui dirige le benin', 'romuald wadagni', 'wadagni', 'qui est romuald wadagni', 'president wadagni', 'qui est wadagni', 'investiture de wadagni', 'election presidentielle 2026 au benin', 'presidentielle de 2026 au benin', 'elections au benin', 'election presidentielle au benin', 'patrice talon', 'qui est patrice talon', 'talon', 'president talon', 'ancien president du benin', 'anciens presidents du benin', 'liste des presidents du benin', 'presidents du benin', 'boni yayi', 'nicephore soglo', 'hubert maga'], 'reponse': "Présidents du Bénin :\n• Actuel : Romuald Wadagni. D'après la presse, il a été élu dès le premier tour de l'élection présidentielle du 12 avril 2026 (plus de 94 % des voix selon les résultats validés par la Cour constitutionnelle) et investi le dimanche 24 mai 2026 à Cotonou ;. Il a été, avant cela, ministre de l'Économie et des Finances (information à vérifier).\n• Avant lui : Patrice Talon (2016-2026), Thomas Boni Yayi (2006-2016), Mathieu Kérékou (1996-2006), Nicéphore Soglo (1991-1996), Mathieu Kérékou (1972-1991), puis, depuis l'indépendance, Hubert Maga (1960-1963), premier président.\n• Depuis 1990, Wadagni est le 5e président de la période démocratique (Soglo, Kérékou, Boni Yayi, Talon, Wadagni).\nAttention : l'actualité politique évolue ; vérifie les informations récentes sur gouv.bj ou dans la presse officielle.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'langues du benin', 'cles': ['langues du benin', 'langues parlees au benin', 'quelles langues parle t on au benin', 'langue officielle du benin', 'langues nationales du benin', 'combien de langues au benin', 'les langues beninoises', 'langues beninoises', 'dialectes du benin', 'peuples du benin', 'ethnies du benin', 'groupes ethniques du benin', 'fon yoruba bariba', 'plus grande ethnie du benin', 'quelles sont les ethnies du benin', 'population beninoise ethnies'], 'reponse': "Langues et peuples du Bénin :\n• Langue officielle : le français (administration, école).\n• Une cinquantaine de langues sont parlées. Principales langues nationales : le fon (la plus parlée, surtout au sud et au centre, avec le goun et l'adja-mina), le yoruba (sud-est et centre, ex. Kétou, Savè), le bariba (baatonum, au nord-est), le dendi (nord), l'adja et le mina (sud-ouest), le goun (Porto-Novo), l'otamari/ditamari (Atacora), le peul (fulfulde), le yom-lokpa, etc.\n• Peuples : Fon (le plus nombreux, environ 40 %), Adja, Yoruba, Bariba, Peuls, Otamari (Somba), Dendi, Goun, Mina, etc.\n• Beaucoup de Béninois sont bilingues ou trilingues : français + une langue locale.\n• Le Bénin est un pays de brassage : chaque peuple a ses danses, ses fêtes et ses traditions.\nTape « mots en fon » pour des expressions utiles.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'langue fon', 'cles': ['langue fon', 'le fon', 'parler fon', 'mots en fon', 'expressions en fon', 'dire bonjour en fon', 'bonjour en fon', 'comment dire bonjour en fon', 'merci en fon', 'comment dire merci en fon', 'au revoir en fon', 'bienvenue en fon', 'salutations en fon', 'apprendre le fon', 'vocabulaire fon', 'phrases en fon', 'dire en fon', 'yoruba bonjour', 'bonjour en yoruba', 'merci en yoruba', 'mots en yoruba', 'apprendre le yoruba', 'langue yoruba', 'langue bariba', 'langue dendi', 'salutations beninoises'], 'reponse': "Quelques expressions en fon (l'écriture varie selon les sources, car le fon s'écrit avec un alphabet à tons) :\n• Bienvenue : « kuwabô » (aussi écrit « kuabo »)\n• Bonjour : « koudo zanzan » (d'après une source de vulgarisation)\n• Au revoir : « édabô »\n• Merci : « énantchè nouwé » (d'après la même source)\n• Comment ça va ? : « nin noulèdégbon »\nEn yoruba (très parlé à l'est du pays, aussi au Nigeria) : « E kaàrọ̀ » = bonjour (le matin) ; « E ṣé » = merci.\nCes mots sont donnés à titre indicatif : demande à un locuteur natif pour la prononciation et l'orthographe exactes. Je peux t'aider à compléter ma base avec ce que tu me dis : « apprends : … = … ».\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'religions et vodun', 'cles': ['religions du benin', 'religions au benin', 'religion au benin', 'quelles religions au benin', 'vodun', 'vaudou', 'le vodun', 'qu est ce que le vodun', 'c est quoi le vodun', 'difference vodun vaudou', 'vodoun', 'berceau du vodun', 'benin berceau du vaudou', 'divinites vodun', 'dieux vodun', 'mawu lisa', 'legba', 'sakpata', 'heviosso', 'hevioso', 'xevioso', 'mami wata', 'fa geomancie', 'le fa', 'divination fa', 'zangbeto', 'zangbeto ouidah', 'egungun', 'gelede', 'masques gelede', 'fete du 10 janvier', '10 janvier benin', 'fete nationale du vodun', 'fete du vodun', 'vodun days', 'journee des religions traditionnelles'], 'reponse': "Religions et vodun au Bénin :\n• Religions : le christianisme (catholiques, protestants, christianisme céleste, etc.), l'islam (surtout au nord) et les religions endogènes (vodun) cohabitent. D'après le recensement de 2013 : islam environ 28 %, catholiques environ 25 %, vodun environ 12 %, le reste réparti entre autres églises et religions.\n• Vodun (« vaudou » en français d'ailleurs, mais « vodun » est le nom d'origine) : religion originaire du golfe du Bénin, avec un Être suprême (Mawu) et de nombreuses divinités qui incarnent les forces de la nature : Legba (messager, gardien des carrefours), Hèviosso/Xêvioso (foudre), Sakpata (terre et maladies), Mami Wata (eaux), etc. Le vodun est une religion, pas de la « magie noire ».\n• Fâ : système de divination et de sagesse, très respecté.\n• Zangbeto : « gardiens de la nuit », esprits-masques de Ouidah/Porto-Novo ; Egungun (ancêtres) et Gèlèdè (masques yoruba, patrimoine immatériel UNESCO) sont d'autres expressions culturelles.\n• 10 janvier : fête nationale des religions endogènes/traditionnelles, célébrée surtout à Ouidah sur la plage (cérémonies, libations, consultation du Fâ, défilé des divinités) ; elle existe depuis le milieu des années 1990 et attire de nombreux visiteurs.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'fetes et jours feries du benin', 'cles': ['jours feries du benin', 'jours feries au benin', 'fetes du benin', 'fetes nationales du benin', 'fetes au benin', 'calendrier des fetes du benin', 'fete de l independance', 'fete du gaani', 'gaani nikki', 'fete de l igname', 'festivals du benin', 'festival au benin', 'evenements culturels du benin', 'fetes traditionnelles du benin', 'fetes culturelles du benin', 'fete nationale du benin', 'carnaval au benin'], 'reponse': "Fêtes et jours fériés au Bénin (principaux) :\n• 1er janvier : Nouvel An.\n• 10 janvier : fête nationale des religions endogènes (vodun), célébrée à Ouidah.\n• Lundi de Pâques, 1er mai (fête du travail), Ascension, lundi de Pentecôte.\n• 1er août : fête de l'indépendance.\n• 15 août : Assomption ; 1er novembre : Toussaint ; 25 décembre : Noël.\n• Fêtes musulmanes (dates variables) : Maouloud, Aïd el-Fitr (fin du ramadan), Tabaski (Aïd el-Kébir).\n• Fêtes traditionnelles : Gaani (fête des Bariba à Nikki, en rapport avec le roi et le Nouvel An bariba), fêtes de l'igname (récolte des ignames, chez plusieurs peuples), Zangbeto, Egungun, Gèlèdè, Vodun Days (Ouidah, en janvier).\nLa liste officielle des jours fériés peut changer d'une année à l'autre : vérifie sur le site du gouvernement.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'cuisine beninoise', 'cles': ['cuisine beninoise', 'plats beninois', 'plats du benin', 'nourriture du benin', 'nourriture beninoise', 'specialites beninoises', 'specialite du benin', 'que manger au benin', 'gastronomie beninoise', 'gastronomie du benin', 'plat typique du benin', 'pate beninoise', 'pate rouge', 'pate blanche', 'amiwo', 'akassa', 'aloko', 'atassi', 'wagashi', 'gari', 'tchoukoutou', 'sodabi', 'igname pilee', 'boissons du benin', 'boisson beninoise', 'poulet bicyclette', 'sauce gombo', 'sauce arachide', 'recette beninoise', 'recettes du benin'], 'reponse': "Cuisine béninoise :\n• Pâtes de céréales (maïs, mil, manioc) servies avec une sauce : pâte blanche (wô), pâte rouge (amiwô, cuite dans une sauce tomate/huile), akassa (pâte de maïs fermentée enveloppée dans des feuilles).\n• Sauces : gombo (sauce collante), arachide, feuilles (gboman, légumes-feuilles), tomate, poisson ou viande fumés.\n• Igname pilée (surtout au centre et au nord), pâte d'igname, gari (semoule de manioc), foufou.\n• Riz-haricots (atassi), aloko (banane plantain frite) servi avec piment et poisson/œuf, poulet bicyclette (poulet fermier grillé), poisson braisé, fromage peul frit (wagashi).\n• Boissons : sodabi (eau-de-vie de palme, forte, à consommer avec modération), tchoukoutou (bière de mil), jus de bissap (hibiscus), gingembre, ananas.\n• Les piments et les condiments fumés relèvent presque tous les plats ; l'ananas et les fruits tropicaux sont très présents.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'musique danse et artisanat du benin', 'cles': ['musique du benin', 'musique beninoise', 'danses du benin', 'danses traditionnelles du benin', 'danse beninoise', 'agbadja', 'tchinkoume', 'chanteurs beninois', 'artistes beninois', 'angelique kidjo', 'qui est angelique kidjo', 'djimon hounsou', 'art beninois', 'artisanat du benin', 'artisanat beninois', 'bas reliefs d abomey', 'appliques d abomey', 'masques beninois', 'art du benin', 'culture du benin', 'la culture beninoise', 'culture beninoise', 'traditions du benin', 'traditions beninoises', 'musees du benin', 'fondation zinsou', 'romuald hazoume', 'cinema beninois', 'litterature beninoise', 'personnalites beninoises', 'beninois celebres', 'celebrites du benin', 'bernardin gantin', 'paulin hountondji'], 'reponse': "Culture et art du Bénin :\n• Musique et danse : chaque peuple a ses rythmes : agbadja (Mono/Couffo), tchinkoumé, tam-tams et chants des couvents vodun, danses guerrières bariba. Le Bénin a une scène musicale vivante (afrobeat, gospel, zouk, rap, etc.).\n• Artistes et personnalités : Angélique Kidjo (chanteuse mondialement connue, née à Ouidah, plusieurs fois récompensée aux Grammy), Djimon Hounsou (acteur, né à Cotonou), le cardinal Bernardin Gantin (Église catholique), Paulin Hountondji (philosophe), Romuald Hazoumè (artiste contemporain).\n• Artisanat : bas-reliefs en terre des palais d'Abomey, appliqués (tentures cousues) du Dahomey, masques et statuettes, poterie, vannerie, tissage, bronze (travail du métal), perles.\n• Musées et lieux d'art : musée d'histoire d'Abomey (palais royaux), musée d'histoire de Ouidah (ancien fort portugais), fondation Zinsou à Cotonou et Ouidah, palais royal et musées de Porto-Novo, musée Honmè.\n• Patrimoine immatériel : le Gèlèdè (masques yoruba) est inscrit à l'UNESCO.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'tourisme au benin', 'cles': ['tourisme au benin', 'tourisme benin', 'visiter le benin', 'que visiter au benin', 'sites touristiques du benin', 'lieux touristiques du benin', 'meilleurs endroits a visiter au benin', 'incontournables du benin', 'attractions du benin', 'que faire au benin', 'partir au benin', 'voyage au benin', 'destination benin', 'sites du benin', 'patrimoine mondial du benin', 'unesco benin', 'sites unesco du benin', 'monuments du benin', 'lieux a visiter au benin', 'top sites benin', 'idees de visite benin', 'circuit benin'], 'reponse': "À visiter au Bénin (les incontournables) :\n• Ouidah : Route des Esclaves (environ 4 km), Porte du Non-Retour, temple des pythons, musée d'histoire (fort portugais), forêt sacrée de Kpassè ; capitale du vodun.\n• Abomey : palais royaux du Dahomey (patrimoine mondial UNESCO depuis 1985) et musée d'histoire, bas-reliefs.\n• Ganvié : village lacustre sur pilotis sur le lac Nokoué, accessible en pirogue.\n• Porto-Novo : quartier afro-brésilien, palais royal, musée Honmè, Grande Mosquée de style brésilien, jardin des plantes.\n• Cotonou : marché Dantokpa (l'un des plus grands d'Afrique de l'Ouest), plages (Fidjrossè), fondation Zinsou, artisanat.\n• Grand-Popo et Bouche du Roy : plages, mangroves, villages de pêcheurs ; Possotomé et lac Ahémé : sources d'eau gazeuse, villages lacustres.\n• Nord : parc national de la Pendjari (savane : éléphants, lions, hippopotames) et parc du W (complexe W-Arly-Pendjari, patrimoine mondial UNESCO depuis 2017), Tata Somba de l'Atacora (maisons fortifiées en terre des Batammariba), chutes de Kota et de Tanougou, Natitingou.\nDemande-moi un site précis (« parle-moi de Ouidah ») ou « conseils pour voyager au Bénin ».\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'ouidah', 'cles': ['ouidah', 'ville de ouidah', 'parle moi de ouidah', 'visiter ouidah', 'route des esclaves', 'la route des esclaves', 'porte du non retour', 'temple des pythons', 'temple python ouidah', 'foret sacree de kpasse', 'kpasse', 'musee d histoire de ouidah', 'fort portugais de ouidah', 'traite negriere ouidah', 'ouidah histoire', 'sites de ouidah'], 'reponse': "Ouidah, ville côtière et capitale du vodun :\n• Histoire : grand port de traite des esclaves à l'époque du royaume du Dahomey (conquis vers 1727) ; des centaines de milliers de captifs y ont embarqué vers les Amériques.\n• Route des Esclaves (environ 4 km) : de la place Chacha jusqu'à la plage ; elle passe notamment par l'arbre de l'oubli, la place des enchères et la Porte du Non-Retour, monument au bord de l'océan.\n• Temple des pythons : lieu de culte où l'on vénère le python (Dagbé), animal sacré.\n• Musée d'histoire de Ouidah : dans l'ancien fort portugais Saint-Jean-Baptiste ; il retrace la traite et le vodun.\n• Forêt sacrée de Kpassè : forêt de divinités vodun et sculptures.\n• Fondation Zinsou (art contemporain) ; couvents vodun, marchés et festival annuel.\n• Chaque 10 janvier, la plage de Ouidah accueille la fête nationale du vodun.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'abomey', 'cles': ['abomey', 'ville d abomey', 'palais royaux d abomey', 'palais d abomey', 'palais royaux du dahomey', 'visiter abomey', 'parle moi d abomey', 'musee historique d abomey', 'abomey unesco', 'capitale du royaume du dahomey', 'palais du roi guezo', 'palais du roi glele'], 'reponse': "Abomey, ancienne capitale du royaume du Dahomey (département du Zou) :\n• Les palais royaux d'Abomey sont inscrits au patrimoine mondial de l'UNESCO depuis 1985 : plusieurs palais construits par les rois successifs (de Houégbadja à Béhanzin) dans une vaste enceinte.\n• Musée d'histoire d'Abomey, installé dans les palais de Guézo et de Glèlè : trônes royaux, objets de culte, armes, tentures (appliqués) et bas-reliefs en terre qui racontent les règnes et symbolisent chaque roi (par un animal : lion, buffle, requin…).\n• Les Amazones du Dahomey, l'armée du roi, y avaient leur résidence ; la ville garde le souvenir de cette histoire.\n• Bohicon, tout près, est un grand carrefour routier et ferroviaire (siège du lycée technique LYTEB).\n• En novembre 2021, la France a restitué 26 œuvres des trésors royaux d'Abomey (emportées en 1892), présentées ensuite au Bénin.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'ganvie et lac nokoue', 'cles': ['ganvie', 'village de ganvie', 'ganvie lac nokoue', 'venise de l afrique', 'village lacustre', 'villages lacustres du benin', 'lac nokoue', 'lac aheme', 'possotome', 'visiter ganvie', 'parle moi de ganvie', 'grand popo', 'bouche du roy', 'mangroves du benin', 'plages du benin', 'plages de cotonou', 'sources d eau gazeuse possotome'], 'reponse': "Villes sur l'eau et littoral du sud :\n• Ganvié : village lacustre de plus de 30 000 habitants (d'après un guide de voyage), construit sur pilotis sur le lac Nokoué, près de Cotonou ; on s'y rend en pirogue. Les Toffin s'y sont installés au XVIIIe siècle pour échapper aux raids des guerriers du Dahomey ; on y vit de la pêche et du commerce sur l'eau. Surnommé « la Venise de l'Afrique ». Mieux vaut y aller tôt et en semaine.\n• Possotomé (lac Ahémé) : villages de pêcheurs, sources d'eau naturellement gazeuse, villages de potiers, balade en pirogue.\n• Grand-Popo : petite ville balnéaire près du Togo, plages, mangroves et pirogues sur la Bouche du Roy (embouchure du Mono). Attention : baignade risquée à cause des courants.\n• Plages de Cotonou (Fidjrossè) : animées, mais baignade à éviter (courants forts).\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'parcs nationaux pendjari et w', 'cles': ['parc de la pendjari', 'pendjari', 'parc national de la pendjari', 'parc du w', 'parc national du w', 'parcs nationaux du benin', 'reserves du benin', 'safari au benin', 'animaux du benin', 'faune du benin', 'elephants au benin', 'lions au benin', 'complexe w arly pendjari', 'w arly pendjari', 'faune sauvage du benin', 'tanguieta', 'parc national du benin'], 'reponse': "Parcs nationaux du nord du Bénin :\n• Parc national de la Pendjari (nord-ouest, près de Tanguiéta) : une des plus belles réserves de savane d'Afrique de l'Ouest, avec éléphants, lions, léopards, buffles, hippopotames, antilopes et nombreux oiseaux.\n• Parc national du W (nord-est, sur le fleuve Niger) : partagé avec le Niger et le Burkina Faso.\n• Le complexe W-Arly-Pendjari est inscrit au patrimoine mondial de l'UNESCO (extension aux parcs béninois en 2017).\n• Meilleure période : saison sèche (de novembre à avril environ), quand les animaux se rassemblent autour des points d'eau.\n• Sécurité : des autorités étrangères ont déconseillé certaines zones frontalières du nord. Vérifie toujours les conseils aux voyageurs officiels et passe par un opérateur agréé avant d'y aller.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'atacora et tata somba', 'cles': ['tata somba', 'tata somba atacora', 'maisons somba', 'somba', 'peuple somba', 'batammariba', 'otamari', 'atacora', 'chutes de kota', 'chutes de tanougou', 'cascade de tanougou', 'cascades du benin', 'visiter natitingou', 'nord du benin tourisme', 'tourisme au nord du benin', 'nord benin'], 'reponse': "L'Atacora (nord-ouest du Bénin) :\n• Tata Somba : maisons-forteresses en terre des Batammariba (Otamari, anciennement appelés « Somba »), à plusieurs niveaux, avec des tours de guet ; elles servent d'habitation, de grenier et de lieu de culte. Elles se visitent autour de Natitingou et de Boukombé. Prévoyez au moins deux jours, car le site est à environ 600 km de Cotonou.\n• Chutes de Kota (environ 20 m) et cascade de Tanougou (bassin naturel où l'on peut se baigner, eau toute l'année).\n• Natitingou : chef-lieu de l'Atacora, base pour les excursions ; Tanguiéta : porte d'entrée de la Pendjari.\n• Chaîne de l'Atacora : relief le plus marqué du pays (mont Sokbaro, environ 658 m).\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'porto novo', 'cles': ['porto novo', 'ville de porto novo', 'parle moi de porto novo', 'visiter porto novo', 'palais honme', 'musee honme', 'musee da silva', 'grande mosquee de porto novo', 'quartier afro bresilien', 'jardin des plantes de porto novo', 'jardin des plantes et de la nature', 'capitale officielle du benin', 'histoire de porto novo', 'assemblee nationale porto novo'], 'reponse': "Porto-Novo, capitale officielle du Bénin (département de l'Ouémé) :\n• Siège de l'Assemblée nationale ; ville d'art et d'histoire, au bord d'une lagune.\n• Palais royal Honmè (ancien palais du roi Toffa) et musée Honmè : histoire de la monarchie de Hogbonou (ancien nom de la ville).\n• Quartier afro-brésilien : maisons colorées du XIXe siècle construites par d'anciens esclaves revenus d'Amérique ; la Grande Mosquée de style brésilien est l'un des monuments les plus connus.\n• Musée Da Silva (histoire afro-brésilienne), musée ethnographique, jardin des plantes et de la nature.\n• Marché Ouando, plus calme que Dantokpa. Population : environ 264 000 habitants en 2013.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'cotonou', 'cles': ['cotonou', 'ville de cotonou', 'parle moi de cotonou', 'visiter cotonou', 'dantokpa', 'marche dantokpa', 'port de cotonou', 'aeroport de cotonou', 'aeroport du benin', 'etoile rouge', 'monument de l etoile rouge', 'cathedrale de cotonou', 'fidjosse', 'que faire a cotonou', 'pont de cotonou', 'histoire de cotonou'], 'reponse': "Cotonou, capitale économique du Bénin (département du Littoral) :\n• Plus grande ville du pays et siège de la présidence (Palais de la Marina) et de la plupart des ministères et ambassades.\n• Port autonome de Cotonou : poumon de l'économie, il dessert aussi le Niger, le Burkina Faso et le Mali.\n• Aéroport international Cardinal Bernardin Gantin.\n• Marché Dantokpa : l'un des plus grands marchés d'Afrique de l'Ouest (commerce de tout : tissus, épices, électronique, etc.).\n• À voir : plages de Fidjrossè, monument de l'Étoile Rouge, cathédrale Notre-Dame-de-Miséricorde, fondation Zinsou, artisanat, lac Nokoué (Ganvié à proximité).\n• La ville est dense et le trafic est important : les « zémidjans » (taxis-motos) sont le transport le plus courant.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'conseils pour voyager au benin', 'cles': ['conseils pour voyager au benin', 'voyager au benin', 'visa pour le benin', 'visa benin', 'e visa benin', 'vaccin pour le benin', 'vaccin fievre jaune benin', 'meilleure periode pour visiter le benin', 'quand partir au benin', 'saison seche benin', 'monnaie du benin', 'argent au benin', 'securite au benin', 'transport au benin', 'zemidjan', 'taxi moto benin', 'preparer un voyage au benin', 'sante au benin', 'que faut il savoir avant de partir au benin', 'touriste au benin'], 'reponse': "Conseils pour voyager au Bénin (à vérifier avant le départ sur les sites officiels) :\n• Visa : un e-visa (en ligne) est généralement exigé pour les voyageurs étrangers ; vérifie les conditions de ton pays.\n• Santé : le vaccin contre la fièvre jaune est obligatoire à l'entrée ; prévoir aussi une protection contre le paludisme (antipaludique, moustiquaire, répulsif) et de l'eau en bouteille.\n• Meilleure période : saison sèche, de novembre à mars environ (moins de pluie, mais l'harmattan souffle de décembre à février) ; le 10 janvier (fête du vodun à Ouidah) est un moment fort.\n• Monnaie : franc CFA (XOF) ; 1 € = 655,957 FCFA ; espèces utiles hors des grandes villes ; Mobile Money très répandu.\n• Transport : zémidjans (taxis-motos), taxis, bus, bateaux et pirogues pour Ganvié, vols intérieurs limités.\n• Sécurité : le sud est en général calme, mais certaines zones frontalières du nord sont déconseillées : vérifie les conseils officiels de ton pays avant d'aller vers la Pendjari et le W.\n• Politesse : on salue toujours avant de demander quelque chose ; demande l'autorisation avant de photographier une personne, un couvent ou une cérémonie vodun.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'economie du benin', 'cles': ['economie du benin', 'economie beninoise', 'l economie du benin', 'ressources du benin', 'agriculture au benin', 'coton au benin', 'anacarde au benin', 'noix de cajou benin', 'produits du benin', 'exportations du benin', 'port de cotonou economie', 'zone industrielle de glo djigbe', 'glo djigbe', 'gdiz', 'monnaie du benin xof', 'franc cfa', 'fcfa', 'xof', 'parite du franc cfa', 'taux de change fcfa euro', 'pib du benin', 'croissance du benin', 'secteurs economiques du benin', 'commerce avec le nigeria', 'commerce du benin'], 'reponse': "Économie du Bénin :\n• Monnaie : franc CFA (XOF), utilisé par l'UEMOA et lié à l'euro à parité fixe : 1 euro = 655,957 FCFA.\n• Agriculture : coton (l'une des premières cultures d'exportation, avec la noix de cajou/anacarde), igname, manioc, maïs, ananas, huile de palme, karité.\n• Services et commerce : le port de Cotonou, le transit vers le Niger, le Burkina Faso et le Nigeria, et le commerce avec le Nigeria sont très importants.\n• Industrie : la zone industrielle de Glo-Djigbé (GDIZ), près de Cotonou, vise à transformer sur place les produits agricoles (coton, anacarde, etc.).\n• Croissance : environ 6 % par an ces dernières années (6,4 % en 2023 selon Larousse), mais le pays reste marqué par la pauvreté (IDH de 0,504 en 2022).\n• Une grande part de l'emploi est dans le secteur informel.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'energie et electricite au benin', 'cles': ['electricite au benin', 'energie au benin', 'energie electrique au benin', 'sbee', 'societe beneinoise d energie electrique', 'societe beninoise d energie electrique', 'ceb', 'communaute electrique du benin', 'reseau electrique du benin', 'production electrique du benin', 'tension du reseau au benin', 'frequence au benin', 'courant au benin', 'electrification du benin', 'solaire au benin', 'energie solaire benin', 'centrale electrique du benin', 'distribution electrique benin', 'coupures de courant au benin', 'delestage benin', 'tension 220 v benin', 'prise electrique benin'], 'reponse': "Électricité au Bénin :\n• Distribution : la SBEE (Société béninoise d'énergie électrique) distribue l'électricité aux particuliers et aux entreprises.\n• Production et transport : le pays produit une partie de son électricité et en importe une grande part, notamment du Nigeria et du Ghana ; la CEB (Communauté électrique du Bénin, avec le Togo) gère une partie des échanges et du réseau de transport.\n• Le réseau est en courant alternatif 50 Hz ; la tension de distribution est annoncée à 230/400 V en triphasé (souvent appelée 220/380 V dans l'usage courant).\n• L'électrification progresse, y compris avec le solaire (kits solaires, mini-réseaux) dans les zones rurales ; des centrales solaires sont aussi développées.\n• Pour les calculs et installations, utilise mes calculateurs (section de câble, puissance, couplage moteur…) : par exemple « quelle section de câble pour un moteur de 5 kW 230 V monophasé cos phi 0,8 ». Je donne les formules, mais le dimensionnement final doit respecter la norme et être validé par un professionnel.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'education et enseignement technique au benin', 'cles': ['education au benin', 'ecole au benin', 'systeme educatif beninois', 'systeme educatif du benin', 'enseignement technique au benin', 'lycee technique au benin', 'filieres techniques au benin', 'serie f3', 'bac f3', 'bac technique au benin', 'bac au benin', 'universites du benin', 'universite d abomey calavi', 'uac', 'universite de parakou', 'unstim', 'ecoles superieures au benin', 'etudier au benin', 'bourse au benin', 'lytec', 'lyteb'], 'reponse': "Éducation au Bénin :\n• Parcours : maternelle, primaire (6 ans, CEP), collège (BEPC), lycée (baccalauréat) ; l'enseignement technique et professionnel prépare aux métiers : séries F1 (construction mécanique), F2 (électronique), F3 (électrotechnique), F4 (génie civil), G (gestion), etc. (liste à vérifier selon les années).\n• Lycées techniques : par exemple le Lycée Technique de Bohicon (LYTEB) et d'autres lycées techniques à Cotonou, Porto-Novo, Parakou…\n• Universités publiques : Université d'Abomey-Calavi (UAC), Université de Parakou, UNSTIM (Abomey), Université nationale d'agriculture (Kétou) ; nombreuses écoles et instituts supérieurs privés.\n• Après un bac F3, on peut poursuivre en BTS ou licence professionnelle (électrotechnique, maintenance industrielle…), comme certains de nos amis de LYTEB.\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}, {'titre': 'faits rapides sur le benin', 'cles': ['faits rapides sur le benin', 'le saviez vous benin', 'anecdote benin', 'anecdotes sur le benin', 'choses a savoir sur le benin', 'curiosites du benin', 'informations amusantes sur le benin', 'fait amusant benin', 'quiz sur le benin', 'culture generale benin', 'tout sur le benin', 'dis moi quelque chose sur le benin', 'raconte moi le benin', 'benin fierte', 'pourquoi le benin est celebre', 'le benin est connu pour quoi', 'pour quoi le benin est connu'], 'reponse': "Le Bénin est connu pour :\n• Être le berceau du vodun, avec la grande fête nationale du 10 janvier à Ouidah.\n• Le royaume du Dahomey, ses rois (Guézo, Béhanzin) et ses Amazones.\n• Les palais royaux d'Abomey (UNESCO), la Route des Esclaves et la Porte du Non-Retour de Ouidah.\n• Le village sur pilotis de Ganvié et ses lacs.\n• Ses parcs de la Pendjari et du W, avec les éléphants et les lions.\n• Sa Conférence nationale de 1990, qui a lancé la démocratisation en Afrique francophone.\n• Sa musique (Angélique Kidjo), sa cuisine (pâte, amiwô, aloko, wagashi) et ses zémidjans.\n• La restitution de 26 œuvres des trésors royaux par la France en 2021.\nTape « quiz Bénin » pour t'amuser et tester tes connaissances !\n(Source : Larousse, presse béninoise et documentation touristique ; à vérifier pour les chiffres récents)"}]
for _fiche in FICHES_BENIN:
    _fiche["mot_entier"] = True
CONNAISSANCES_GENERALES[0:0] = FICHES_BENIN  # en tête : priorité sur les anciennes fiches Bénin

# ===== BASE ÉLARGIE v33 =====
_RM_V33 = set(['algorithme', 'ampere', 'api', 'apprendre a programmer', 'apprendre le yoruba', 'arduino', 'auto maintien', 'automate', 'biocarburant', 'biogaz', 'biomasse', 'bonjour en yoruba', 'boucle for', 'boucle while', 'calibre du disjoncteur', 'centrale electrique', 'centrale hydraulique', 'centrale nucleaire', 'centrale thermique', 'chimie', 'chute de tension', 'comment depanner', 'conducteur de protection', 'consignation', 'consignation electrique', 'continents', 'css', 'debogage', 'decodeur', 'demarrage direct', 'demarrage progressif', 'depannage', 'diagnostic de panne', 'dictionnaire python', 'dimensionnement solaire', 'dimensionner une installation solaire', 'disjoncteur', 'double allumage', 'efficacite energetique', 'energie eolienne', 'energie hydraulique', 'energie marine', 'energie renouvelable', 'energies renouvelables', 'entretien d embauche', 'eolienne', 'fonction python', 'geographie du monde', 'groupe electrogene', 'haute tension', 'html', 'hydroelectricite', 'hydrogene', 'informatique', 'intelligence artificielle', 'javascript', 'joule', 'kilowattheure', 'langue yoruba', 'lettre formelle', 'lire un schema electrique', 'liste python', 'maintenance', 'mathematiques', 'mecanique', 'merci en yoruba', 'mesurer un courant', 'methode de depannage', 'microcontroleur', 'mise a la terre', 'moteur qui ne demarre pas', 'mouvement rectiligne uniforme', 'nombre complexe', 'oceans', 'onduleur', 'page web', 'panne electrique', 'physique', 'prise de terre', 'programmation', 'rapport de transformation', 'raspberry', 'regulateur de charge', 'schema de commande', 'schema de puissance', 'schema electrique', 'section de cable', 'simple allumage', 'site web', 'soft starter', 'symboles electriques', 'thomas edison', 'transformateur', 'transformateur monophase', 'unites du systeme international', 'unites si', 'variateur', 'variateur de frequence', 'variateur de vitesse'])
for _f in CONNAISSANCES_GENERALES:
    if True:
        _f['cles'] = [_c for _c in _f['cles'] if _c not in _RM_V33]
FICHES_V33 = [{'titre': 'maintenance industrielle', 'cles': ['maintenance industrielle', 'la maintenance', 'c est quoi la maintenance', 'definition de la maintenance', 'role de la maintenance', 'objectifs de la maintenance', 'maintenance des systemes industriels', 'technicien de maintenance', 'metier de la maintenance', 'maintenance'], 'reponse': "Maintenance industrielle : ensemble des actions techniques, administratives et de gestion destinées à maintenir ou rétablir un bien dans un état lui permettant d'accomplir la fonction requise (norme NF EN 13306).\n• Objectifs : garder la disponibilité des machines, réduire les pannes et les coûts, assurer la sécurité, allonger la durée de vie, respecter la qualité et l'environnement.\n• Deux grandes familles : maintenance préventive (avant la panne) et corrective (après la panne).\n• Cinq niveaux de maintenance (de 1 à 5) selon la complexité : du simple réglage par l'opérateur jusqu'à la reconstruction chez le constructeur.\n• Outils : GMAO, AMDEC, plan de maintenance, indicateurs (MTBF, MTTR, disponibilité, TRS), 5S, TPM.\nDemande-moi : « types de maintenance », « niveaux de maintenance », « MTBF MTTR », « GMAO », « AMDEC », « TPM », « 5S ».", 'mot_entier': True}, {'titre': 'types de maintenance', 'cles': ['types de maintenance', 'les types de maintenance', 'maintenance preventive', 'maintenance corrective', 'maintenance curative', 'maintenance palliative', 'maintenance systematique', 'maintenance conditionnelle', 'maintenance previsionnelle', 'maintenance predictive', 'difference preventive corrective', 'formes de maintenance', 'schema de la maintenance'], 'reponse': "Types de maintenance (norme NF EN 13306) :\n• Maintenance corrective (après défaillance) : palliative (dépannage provisoire) ou curative (réparation définitive).\n• Maintenance préventive (avant la défaillance) :\n  – systématique : selon un échéancier (heures, cycles, calendrier), par exemple changer l'huile toutes les 500 h ;\n  – conditionnelle : déclenchée quand un seuil est atteint après mesure (vibrations, température, usure) ;\n  – prévisionnelle (prédictive) : fondée sur l'analyse de l'évolution de paramètres pour prévoir la panne.\n• Maintenance améliorative : modifier le matériel pour le fiabiliser.\nÀ retenir : plus on anticipe, moins les arrêts imprévus coûtent cher, mais la préventive a elle aussi un coût : il faut choisir selon la criticité de la machine.", 'mot_entier': True}, {'titre': 'niveaux de maintenance', 'cles': ['niveaux de maintenance', 'les 5 niveaux de maintenance', 'cinq niveaux de maintenance', 'maintenance niveau 1', 'maintenance niveau 2', 'maintenance niveau 3', 'maintenance niveau 4', 'maintenance niveau 5', '1er niveau de maintenance', 'niveau de maintenance'], 'reponse': "Les 5 niveaux de maintenance (FD X 60-000) :\n• Niveau 1 : réglages simples, remplacement d'éléments accessibles sans outillage spécial (voyant, fusible) — par l'opérateur.\n• Niveau 2 : dépannage par échange standard d'éléments prévus, opérations mineures de préventive (graissage, contrôles) — technicien habilité.\n• Niveau 3 : identification et diagnostic de pannes, réparation par échange de composants fonctionnels, réglages — technicien spécialisé.\n• Niveau 4 : travaux importants de maintenance corrective ou préventive, réglages et étalonnage des appareils de mesure — équipe spécialisée.\n• Niveau 5 : rénovation, reconstruction, réparations importantes confiées à un atelier central ou au constructeur.", 'mot_entier': True}, {'titre': 'indicateurs de maintenance mtbf mttr', 'cles': ['mtbf', 'mttr', 'mtbf mttr', 'mtbf et mttr', 'disponibilite', 'calcul de la disponibilite', 'taux de disponibilite', 'fiabilite', 'maintenabilite', 'taux de defaillance', 'lambda defaillance', 'indicateurs de maintenance', 'temps moyen entre pannes', 'temps moyen de reparation', 'formule de la disponibilite', 'difference mtbf mttr'], 'reponse': 'Indicateurs de maintenance :\n• MTBF (Mean Time Between Failures) = temps moyen entre deux pannes = temps de bon fonctionnement total / nombre de pannes.\n• MTTR (Mean Time To Repair) = temps moyen de réparation = temps total de réparation / nombre de pannes.\n• Taux de défaillance : λ = 1 / MTBF (en pannes par heure).\n• Disponibilité intrinsèque : D = MTBF / (MTBF + MTTR).\n• Exemple : MTBF = 450 h et MTTR = 50 h ⇒ D = 450 / 500 = 0,9 soit 90 %.\n• Fiabilité R(t) = e^(−λ·t) (loi exponentielle, taux de défaillance constant).\n• Fiabilité = aptitude à fonctionner sans panne ; maintenabilité = aptitude à être remise en état vite ; disponibilité = aptitude à être en état de fonctionner au moment voulu.', 'mot_entier': True}, {'titre': 'gmao', 'cles': ['gmao', 'c est quoi la gmao', 'gestion de maintenance assistee par ordinateur', 'logiciel de maintenance', 'ordre de travail', 'bon de travail', 'historique des pannes', 'fiche d intervention', 'dossier machine'], 'reponse': "GMAO : Gestion de la Maintenance Assistée par Ordinateur (CMMS en anglais). C'est un logiciel qui centralise :\n• le parc machines (arborescence, codification, dossier machine) ;\n• les ordres de travail (OT) préventifs et correctifs, avec priorité, date, durée, pièces utilisées ;\n• le planning des interventions et les alertes préventives ;\n• le stock de pièces de rechange et les achats ;\n• l'historique des pannes pour calculer MTBF, MTTR, coûts ;\n• les rapports et tableaux de bord.\nExemples de logiciels : Carl Source, Coswin, Maximo, GMAO libres comme OpenMaint. Sans logiciel, un cahier d'intervention bien tenu et un tableur peuvent déjà servir de début de GMAO.", 'mot_entier': True}, {'titre': 'amdec', 'cles': ['amdec', 'c est quoi l amdec', 'analyse des modes de defaillance', 'criticite amdec', 'indice de criticite', 'gravite frequence detection', 'mode de defaillance', 'effet de la defaillance', 'cause de defaillance', 'calcul de la criticite'], 'reponse': "AMDEC (Analyse des Modes de Défaillance, de leurs Effets et de leur Criticité) :\nMéthode préventive qui liste, pour chaque composant, ce qui peut tomber en panne, pourquoi, avec quelles conséquences.\n• Colonnes : élément – fonction – mode de défaillance – cause – effet – détection actuelle – cotation.\n• Cotation sur 1 à 10 (ou 1 à 4) : G = gravité, F (ou O) = fréquence d'apparition, D = probabilité de non-détection.\n• Criticité C = G × F × D (indice de priorité de risque).\n• On fixe un seuil (par exemple C ≥ 100) : au-dessus, il faut une action corrective (surveillance, redondance, changement de pièce, plan de maintenance préventive).\n• Exemple : roulement d'un moteur : défaillance = grippage ; cause = manque de graisse ; effet = arrêt de la ligne ; action = graissage périodique + mesure vibratoire.", 'mot_entier': True}, {'titre': 'tpm et 5s', 'cles': ['tpm', 'total productive maintenance', 'maintenance productive totale', '5s', 'les 5s', 'methode 5s', 'seiri seiton seiso', 'trier ranger nettoyer', '5 s', 'maintenance autonome', 'amelioration continue kaizen', 'kaizen', 'lean manufacturing', 'lean'], 'reponse': "5S : méthode japonaise d'organisation du poste de travail, base de la TPM.\n• Seiri : Trier (éliminer l'inutile) ; Seiton : Ranger (chaque chose à sa place) ; Seiso : Nettoyer (nettoyer, c'est inspecter) ; Seiketsu : Standardiser (règles visibles) ; Shitsuke : Respecter/ Maintenir la discipline.\nTPM (Total Productive Maintenance) : tout le monde participe à la maintenance, les opérateurs font la maintenance autonome de niveau 1 (nettoyage, graissage, inspection) pour éviter pannes, microarrêts et défauts. Objectif : zéro panne, zéro défaut, zéro accident.\nKaizen : amélioration continue par petits pas ; Lean : supprimer les gaspillages (attentes, stocks, déplacements inutiles).", 'mot_entier': True}, {'titre': 'trs taux de rendement synthetique', 'cles': ['trs', 'taux de rendement synthetique', 'calcul du trs', 'taux de rendement global', 'rendement synthetique', 'trs formule', 'taux de qualite', 'taux de performance'], 'reponse': "TRS (Taux de Rendement Synthétique, OEE en anglais) mesure l'efficacité réelle d'une machine :\nTRS = Disponibilité × Performance × Qualité.\n• Disponibilité = temps de fonctionnement / temps requis.\n• Performance = cadence réelle / cadence nominale.\n• Qualité = pièces bonnes / pièces produites.\nExemple : D = 0,9 ; P = 0,95 ; Q = 0,98 ⇒ TRS = 0,9 × 0,95 × 0,98 ≈ 0,838 soit 83,8 %.\nUn TRS de 85 % est souvent cité comme très bon niveau. On cherche les pertes : pannes, changements de série, microarrêts, ralentissements, rebuts.", 'mot_entier': True}, {'titre': 'outils de resolution de problemes', 'cles': ['ishikawa', 'diagramme d ishikawa', 'diagramme en arete de poisson', 'les 5m', '5 pourquoi', 'methode des 5 pourquoi', 'pareto', 'diagramme de pareto', 'loi des 80 20', 'qqoqccp', 'methode qqoqccp', 'outils qualite', 'resolution de probleme', 'brainstorming', 'roue de deming', 'pdca', 'cycle pdca', 'methode 8d'], 'reponse': "Outils de résolution de problèmes (maintenance et qualité) :\n• Ishikawa (arêtes de poisson) : cherche les causes selon les 5M : Matière, Matériel, Méthode, Main-d'œuvre, Milieu.\n• 5 Pourquoi : on demande « pourquoi ? » 5 fois pour remonter à la cause racine (ex. le moteur a chauffé → le ventilateur ne tourne pas → la courroie a cassé → elle était usée → pas de contrôle prévu).\n• Pareto (80/20) : 20 % des causes produisent 80 % des effets ; on traite d'abord les plus fréquentes.\n• QQOQCCP : Qui, Quoi, Où, Quand, Comment, Combien, Pourquoi — pour décrire un problème.\n• PDCA (roue de Deming) : Plan (planifier), Do (faire), Check (vérifier), Act (corriger/standardiser).", 'mot_entier': True}, {'titre': 'consignation et habilitation electrique', 'cles': ['consignation', 'consignation electrique', 'etapes de la consignation', 'les 5 etapes de la consignation', 'separer condamner identifier vat mise a la terre', 'verification d absence de tension', 'lock out tag out', 'cadenassage', 'habilitation electrique', 'habilitations electriques', 'b0 h0', 'br bc', 'b1 b2', 'h1 h2', 'nf c 18 510', 'titre d habilitation', 'travaux sous tension', 'permis de travail', 'securite electrique'], 'reponse': "Sécurité électrique : consignation et habilitation.\n• Consignation (5 étapes) : 1) Séparer l'installation de toutes les sources (ouvrir, débrancher) ; 2) Condamner en position ouverte (cadenas, étiquette « Ne pas manœuvrer ») ; 3) Identifier l'ouvrage concerné ; 4) Vérifier l'absence de tension (VAT) avec un détecteur adapté, testé avant et après ; 5) Mettre à la terre et en court-circuit si nécessaire (surtout en HT/HTA ou si risque de retour de tension).\n• Habilitation (norme NF C 18-510, France ; référence souvent utilisée) : B0/H0 = non-électricien ; BR = intervention en basse tension ; BC = consignation en BT ; B1/B2 = exécutant/chargé de travaux en BT ; H1/H2 = idem en haute tension ; BE = essais/mesurages. Le titre est délivré par l'employeur après formation.\n• Règles d'or : on travaille hors tension, avec EPI (gants isolants, écran facial, outils isolés), à deux personnes pour les opérations à risque. Respecte les consignes de ton entreprise.", 'mot_entier': True}, {'titre': 'lubrification et roulements', 'cles': ['lubrification', 'graissage', 'graisse ou huile', 'types de graisse', 'huile de lubrification', 'viscosite', 'viscosite de l huile', 'roulement', 'roulements', 'defauts de roulement', 'panne de roulement', 'duree de vie d un roulement', 'choix d un roulement', 'roulement a billes', 'roulement a rouleaux', 'jeu des roulements', 'montage des roulements', 'bruit de roulement', 'palier', 'coussinet', 'etancheite', 'joint a levres'], 'reponse': "Lubrification et roulements :\n• Rôle du lubrifiant : réduire frottement et usure, évacuer la chaleur, protéger de la corrosion, étancher.\n• Huile : pour grandes vitesses, bonne évacuation de la chaleur ; viscosité choisie selon température et charge (ex. ISO VG 46). Graisse : plus simple d'emploi, reste en place (roulements de moteurs) ; consistance NLGI (grades 0 à 3 ; 2 est courant).\n• Roulements : à billes (vitesses élevées, charges moyennes), à rouleaux (fortes charges), à aiguilles (faible encombrement).\n• Causes de défaillance : manque ou excès de graisse (surgraissage = surchauffe), pollution, mauvais montage (coups de marteau), désalignement, surcharge, courant de fuite sur moteurs à variateur.\n• Signes : bruit, vibrations, échauffement. Dépose avec extracteur, pose à la presse ou à chaud (chauffage par induction vers 80-100 °C), jamais en frappant sur la bague non concernée.", 'mot_entier': True}, {'titre': 'surveillance vibratoire et thermographie', 'cles': ['analyse vibratoire', 'vibrations', 'surveillance vibratoire', 'vibrations machines', 'balourd', 'desalignement', 'desequilibre', 'thermographie', 'camera thermique', 'thermographie infrarouge', 'controle par ultrasons', 'analyse d huile', 'techniques de controle non destructif', 'maintenance conditionnelle technique', 'spectre vibratoire', 'accelerometre', 'mesure de temperature'], 'reponse': "Techniques de maintenance conditionnelle :\n• Analyse vibratoire : un accéléromètre mesure les vibrations d'une machine tournante. Le spectre (FFT) montre l'origine : balourd (pic à 1 × la fréquence de rotation), désalignement (2 ×), jeu mécanique, défaut de roulement (fréquences caractéristiques), engrènement.\n• Thermographie infrarouge : une caméra thermique détecte les points chauds (connexions desserrées, disjoncteurs, moteurs) sans contact ni arrêt.\n• Analyse d'huile : particules d'usure, eau, viscosité (réducteurs, hydraulique).\n• Ultrasons : fuites d'air comprimé, défauts de roulements.\n• Mesures électriques : courant, isolement (mégohmmètre), résistance des enroulements.\nIntérêt : intervenir seulement quand c'est nécessaire, avant la panne.", 'mot_entier': True}, {'titre': 'transmission mecanique et courroies', 'cles': ['courroie', 'courroies', 'courroie trapezoidale', 'courroie crantee', 'poulie', 'transmission par courroie', 'tension d une courroie', 'accouplement', 'accouplements', 'accouplement elastique', 'reducteur', 'reducteur de vitesse', 'chaine de transmission', 'pignon', 'engrenage', 'engrenages', 'rapport de reduction', 'transmission de puissance', 'calcul de rapport de transmission', 'vitesse de sortie du reducteur'], 'reponse': "Transmission de puissance :\n• Courroies : trapézoïdales (SPZ, SPA…), crantées (synchrones, sans glissement), plates. Avantages : silence, souplesse, économie. Contrôles : tension (flèche ≈ 1,5 % de l'entraxe sous charge normalisée), alignement des poulies, usure (fissures, brillance), ne jamais mélanger vieilles et neuves.\n• Rapport de transmission : i = n_sortie / n_entrée = D_entrée / D_sortie (poulies) = Z_entrée / Z_sortie (engrenages). Exemple : moteur 1 450 tr/min, poulie motrice 100 mm, poulie réceptrice 250 mm ⇒ n = 1 450 × 100 / 250 = 580 tr/min.\n• Réducteur : réduit la vitesse et augmente le couple : C_sortie ≈ C_entrée × rapport × rendement.\n• Accouplements : relient deux arbres en tolérant un petit défaut d'alignement (élastique, à plots, à denture) ; vérifier l'alignement au comparateur ou au laser.\n• Chaîne : bon rendement (≈ 98 %), graissage nécessaire.", 'mot_entier': True}, {'titre': 'pompes et ventilateurs', 'cles': ['pompe', 'pompes', 'pompe centrifuge', 'cavitation', 'amorcage d une pompe', 'hauteur manometrique', 'debit d une pompe', 'puissance d une pompe', 'calcul de puissance d une pompe', 'pompe volumetrique', 'pompe a piston', 'pompe a engrenages', 'choix d une pompe', 'ventilateur', 'ventilateurs', 'debit d air', 'courbe de pompe', 'pompe immergee', 'surpresseur', 'groupe motopompe', 'motopompe'], 'reponse': "Pompes et ventilateurs :\n• Pompe centrifuge : un rotor (roue à aubes) accélère le liquide ; débit variable, pression modérée ; courbe H(Q) décroissante. Pompes volumétriques (piston, engrenages, à vis) : débit presque constant, hautes pressions.\n• Puissance hydraulique : P_h = ρ · g · Q · H (en W, avec ρ = 1 000 kg/m³ pour l'eau, Q en m³/s, H en m). Puissance sur l'arbre : P = P_h / η_pompe ; puissance électrique = P / η_moteur.\n• Exemple : Q = 20 m³/h = 0,00556 m³/s, H = 30 m, η = 0,6 ⇒ P_h = 1 000 × 9,81 × 0,00556 × 30 ≈ 1 636 W ; P_arbre ≈ 2 727 W.\n• Cavitation : formation de bulles quand la pression d'aspiration est trop basse → bruit de cailloux, vibrations, usure. Solutions : réduire la hauteur d'aspiration, diamètre de tuyau plus grand, vanne d'aspiration ouverte.\n• Amorçage : la pompe centrifuge doit être pleine de liquide avant de démarrer (ne jamais la faire tourner à sec).\n• Lois de similitude (ventilateurs/pompes) : le débit varie comme n, la pression comme n², la puissance comme n³ — d'où l'intérêt du variateur de vitesse pour économiser l'énergie.", 'mot_entier': True}, {'titre': 'pneumatique et hydraulique', 'cles': ['pneumatique', 'hydraulique', 'circuit pneumatique', 'circuit hydraulique', 'verin', 'verins', 'verin simple effet', 'verin double effet', 'distributeur pneumatique', 'distributeur 5 2', 'distributeur 3 2', 'electrovanne', 'compresseur', 'compresseur d air', 'air comprime', 'unite de conditionnement d air', 'pression et force d un verin', 'calcul de la force d un verin', 'vitesse d un verin', 'difference pneumatique hydraulique', 'schema pneumatique', 'vanne', 'regulateur de pression'], 'reponse': "Pneumatique et hydraulique (énergie fluide) :\n• Pneumatique : air comprimé (6 à 8 bars en général). Propre, rapide, simple ; force limitée.\n• Hydraulique : huile sous haute pression (jusqu'à plusieurs centaines de bars) ; très grandes forces, mouvements précis.\n• Vérin : transforme la pression en mouvement linéaire. Simple effet (un seul orifice, rappel par ressort) ou double effet (deux orifices). Force F = P × S (avec S = π·D²/4). Exemple : D = 50 mm, P = 6 bar = 6·10⁵ Pa ⇒ S = 1,96·10⁻³ m² ⇒ F ≈ 1 178 N (en théorie, moins avec les frottements).\n• Distributeurs (5/2, 3/2…) : le premier chiffre = nombre d'orifices, le second = nombre de positions ; commande électrique (électrovanne), pneumatique ou manuelle.\n• Chaîne pneumatique : compresseur → réservoir → FRL (filtre, régulateur, lubrificateur) → distributeur → vérin.\n• Sécurité : ne jamais débrancher sous pression, purger avant intervention.", 'mot_entier': True}, {'titre': 'diagnostic de panne', 'cles': ['depannage', 'methode de depannage', 'diagnostic de panne', 'comment depanner', 'demarche de depannage', 'chercher une panne', 'panne electrique', 'recherche de panne', 'moteur qui ne demarre pas', 'moteur qui chauffe', 'le moteur ne tourne pas', 'moteur bruyant', 'disjoncteur qui saute', 'le disjoncteur declenche', 'relais thermique qui declenche', 'que faire en cas de panne', 'pannes courantes du moteur', 'pannes frequentes', 'panne de contacteur', 'contacteur qui ne colle pas'], 'reponse': "Méthode de dépannage (à suivre dans l'ordre) :\n1) Comprendre : que faisait la machine, depuis quand, quel symptôme, qu'est-ce qui a changé ? Interroger l'opérateur.\n2) Observer : voyants, alarmes, odeur, bruit, chaleur, câbles, fusibles — sans toucher aux parties sous tension.\n3) Mettre en sécurité (consigner) avant tout démontage.\n4) Localiser : remonter de l'effet vers la cause (alimentation → protection → commande → puissance → récepteur), en coupant l'installation en blocs et en mesurant.\n5) Réparer, remplacer, contrôler les causes (ne pas seulement changer le fusible : savoir pourquoi il a fondu).\n6) Tester, remettre en service, noter dans le cahier d'intervention.\nExemples : moteur qui ne démarre pas → vérifier tension aux bornes, fusibles, relais thermique, bobine du contacteur, contacts, circuit de commande. Moteur qui chauffe → surcharge, déséquilibre ou manque d'une phase, ventilation bouchée, roulement. Disjoncteur qui saute → court-circuit, surcharge, défaut d'isolement, calibre trop faible.", 'mot_entier': True}, {'titre': 'genie electrique presentation', 'cles': ['genie electrique', 'c est quoi le genie electrique', 'domaines du genie electrique', 'electrotechnique definition', 'qu est ce que l electrotechnique', 'difference electricite electronique electrotechnique', 'difference electrotechnique electronique', 'electricien ou electrotechnicien', 'metier d electrotechnicien', 'debouches en electrotechnique', 'metiers de l electricite', 'metiers du genie electrique'], 'reponse': "Génie électrique : domaine qui couvre la production, le transport, la distribution, la transformation et l'utilisation de l'énergie électrique, ainsi que le traitement de l'information (électronique, automatismes).\n• Électrotechnique : courants forts — machines électriques, transformateurs, réseaux, installations, protections, commande de moteurs.\n• Électronique : courants faibles — composants (diode, transistor, circuits intégrés), signaux, amplification, numérique, microcontrôleurs.\n• Automatisme : commande et supervision de procédés (API, capteurs, actionneurs, régulation).\n• Maintenance industrielle : entretien et dépannage des installations et machines.\nMétiers : électricien du bâtiment, électrotechnicien, technicien de maintenance, monteur-câbleur, automaticien, technicien solaire, agent de la SBEE, chef de chantier, ingénieur électricien, formateur ; possibilité de s'installer à son compte.", 'mot_entier': True}, {'titre': 'transformateur', 'cles': ['transformateur', 'transformateurs', 'transformateur monophase', 'rapport de transformation', 'rapport de transformation du transformateur', 'transformateur parfait', 'transformateur reel', 'rendement du transformateur', 'pertes du transformateur', 'pertes fer pertes cuivre', 'essai a vide du transformateur', 'essai en court circuit du transformateur', 'plaque signaletique du transformateur', 'puissance apparente du transformateur', 'choix d un transformateur', 'autotransformateur', 'transformateur de courant', 'transformateur d isolement', 'tension de court circuit'], 'reponse': "Transformateur monophasé : deux enroulements (primaire N₁, secondaire N₂) sur un circuit magnétique ; il fonctionne en courant alternatif uniquement.\n• Rapport de transformation : m = N₂/N₁ = U₂/U₁ = I₁/I₂ (transformateur parfait).\n• Puissance apparente : S = U × I (en VA ou kVA), identique au primaire et au secondaire pour le transformateur parfait.\n• Pertes : fer (hystérésis + courants de Foucault, dépendent de la tension, constantes) et cuivre (effet Joule, P = R·I², dépendent de la charge).\n• Rendement : η = P₂ / (P₂ + pertes fer + pertes cuivre). Exemple : P₂ = 9 kW, pertes fer 150 W, cuivre 250 W ⇒ η = 9 000 / 9 400 = 95,7 %.\n• Essais : à vide (donne rapport de transformation et pertes fer), en court-circuit sous tension réduite (donne pertes cuivre et tension de court-circuit u_cc).\n• Autotransformateur : un seul enroulement avec prise intermédiaire (économique, sans isolement galvanique). Transformateur d'isolement : rapport 1 pour séparer du réseau (sécurité). TC et TP : mesure de courants et tensions élevés.", 'mot_entier': True}, {'titre': 'transformateur triphase couplages', 'cles': ['transformateur triphase', 'couplage du transformateur triphase', 'couplage dyn11', 'dyn11', 'yzn11', 'indice horaire', 'indice horaire du transformateur', 'couplage etoile triangle transformateur', 'transformateur de distribution', 'poste de transformation', 'poste hta bt', 'poste de livraison', 'poste de distribution publique', 'transformateur 20 kv 400 v', 'transformateur sec ou dans l huile', 'transformateur immerge', 'neutre du transformateur'], 'reponse': "Transformateur triphasé :\n• Couplages possibles au primaire/secondaire : étoile (Y/y), triangle (D/d), zigzag (z). Notation : majuscule = enroulement HT (primaire), minuscule = BT ; n = neutre sorti.\n• Indice horaire : déphasage entre tensions primaire et secondaire par pas de 30° (11 h = 330°, soit −30°). Le couplage Dyn11 (triangle au primaire, étoile avec neutre au secondaire) est le plus courant pour les transformateurs de distribution HTA/BT (ex. 20 kV / 400 V).\n• Rapport de transformation entre tensions composées : il dépend du couplage. Pour Dyn : U₂ (composée) / U₁ (composée) = √3 × N₂/N₁ ; pour Dd ou Yy : = N₂/N₁.\n• Refroidissement : immergé dans l'huile (ONAN) ou sec enrobé dans la résine (intérieur, sécurité incendie).\n• Poste de transformation : cellules HTA (interrupteur, protection), transformateur, tableau BT. En BT, tension simple 230 V / composée 400 V.\n• Mise en parallèle de transformateurs : même rapport, même indice horaire, tensions de court-circuit voisines.", 'mot_entier': True}, {'titre': 'reseaux electriques production transport distribution', 'cles': ['reseaux electriques', 'production transport distribution', 'transport de l electricite', 'distribution de l electricite', 'haute tension', 'hta', 'bt', 'basse tension', 'htb', 'domaines de tension', 'niveaux de tension', 'pourquoi transporter en haute tension', 'pertes en ligne', 'ligne haute tension', 'centrale electrique', 'centrale thermique', 'centrale hydraulique', 'centrale solaire', 'centrale nucleaire', 'interconnexion', 'reseau interconnecte', 'poste source', 'sous station', 'pylone'], 'reponse': "Réseau électrique : de la centrale jusqu'à la prise.\n• Production : centrales thermiques (gaz, charbon), hydrauliques, nucléaires, éoliennes, solaires… Les alternateurs produisent du triphasé 50 Hz (60 Hz dans certains pays).\n• Transport : en très haute tension pour limiter les pertes (P = R·I² : à puissance égale, plus la tension monte, plus le courant baisse).\n• Distribution : postes sources HTB/HTA, réseaux HTA (1 à 50 kV, ex. 15 kV et 20 kV), puis postes HTA/BT et réseau BT 230/400 V jusqu'aux abonnés.\n• Domaines de tension (courant alternatif) : TBT ≤ 50 V ; BTA 50 à 500 V ; BTB 500 à 1 000 V ; HTA 1 à 50 kV ; HTB > 50 kV.\n• Exemple d'intérêt de la HT : pour transporter 1 MW sous 400 V il faut I ≈ 1 443 A (triphasé, cos φ = 1), sous 20 kV seulement 29 A, donc 2 500 fois moins de pertes Joule à section égale.\n• Au Bénin : production et importation (Nigeria, Ghana), transport par la CEB, distribution par la SBEE.", 'mot_entier': True}, {'titre': 'schemas de liaison a la terre', 'cles': ['regime de neutre', 'schema de liaison a la terre', 'slt', 'regime tt', 'regime tn', 'regime it', 'tt tn it', 'tn c', 'tn s', 'tn c s', 'difference tt tn it', 'mise a la terre', 'prise de terre', 'resistance de terre', 'piquet de terre', 'terre electrique', 'electrode de terre', 'valeur de la prise de terre', 'mesure de terre', 'telurometre', 'conducteur de protection', 'liaison equipotentielle', 'conducteur pe', 'terre et neutre'], 'reponse': "Schémas de liaison à la terre (régimes de neutre) — 2 lettres :\n• 1re lettre = situation du neutre du transformateur : T = relié à la terre ; I = isolé (ou impédant).\n• 2e lettre = situation des masses de l'installation : T = reliées à une terre locale ; N = reliées au neutre.\n• TT : neutre à la terre, masses à la terre locale. Protection par disjoncteur différentiel (obligatoire). Régime le plus courant chez les particuliers (réseau public BT).\n• TN (TN-C, TN-S, TN-C-S) : masses reliées au neutre ; protection par disjoncteurs/fusibles (défaut = court-circuit) ou différentiel.\n• IT : neutre isolé, le premier défaut n'est pas dangereux (continuité de service : hôpitaux, blocs opératoires, industrie) ; contrôleur permanent d'isolement obligatoire, déclenchement au 2e défaut.\n• Prise de terre : piquet, ruban, fond de fouille ; valeur recherchée faible ; condition de sécurité en TT : R_A × I_Δn ≤ 50 V (ex. avec un différentiel de 30 mA, R_A ≤ 1 666 Ω ; on vise bien moins, par exemple moins de 100 Ω). Mesure au télluromètre.\n• Couleurs : conducteur de protection PE vert/jaune ; neutre bleu clair ; phases noir/marron/gris ou rouge selon les pays.", 'mot_entier': True}, {'titre': 'protections electriques', 'cles': ['disjoncteur', 'disjoncteurs', 'disjoncteur differentiel', 'interrupteur differentiel', 'differentiel 30 ma', 'sensibilite du differentiel', 'fusible', 'fusibles', 'fusible gg', 'fusible am', 'courbe b c d', 'courbe de declenchement', 'disjoncteur courbe c', 'calibre du disjoncteur', 'pouvoir de coupure', 'surcharge et court circuit', 'protection contre les surintensites', 'protection des personnes', 'contact direct contact indirect', 'parafoudre', 'parasurtenseur', 'choix d un disjoncteur', 'relais differentiel', 'disjoncteur magneto thermique', 'protection des circuits'], 'reponse': "Protections électriques :\n• Surcharge (courant un peu trop élevé, durée longue) : protection thermique (bilame). Court-circuit (courant très élevé) : protection magnétique (bobine). Le disjoncteur magnéto-thermique fait les deux.\n• Calibre In : courant assigné ; règle de base : I_B ≤ I_n ≤ I_z (courant d'emploi ≤ calibre ≤ courant admissible du câble).\n• Courbes de déclenchement magnétique : B (3 à 5 × In ; longues lignes, générateurs), C (5 à 10 × In ; usage général), D (10 à 14 × In ; moteurs, transformateurs avec fort appel de courant).\n• Pouvoir de coupure (PdC) : courant de court-circuit maximal que l'appareil sait couper ; doit être ≥ I_cc présumé au point d'installation.\n• Fusibles : gG (usage général), aM (accompagnement moteur, ne protège que du court-circuit : prévoir relais thermique).\n• Différentiel : détecte le courant de fuite à la terre ; 30 mA = protection des personnes (contacts directs), 300 mA = protection des biens/incendie. Il ne protège pas contre les surcharges (sauf disjoncteur différentiel).\n• Parafoudre : écoule les surtensions dues à la foudre.\n• Contacts directs = toucher une partie active ; indirects = toucher une masse mise accidentellement sous tension.", 'mot_entier': True}, {'titre': 'section des cables et chute de tension', 'cles': ['section des cables', 'calcul de section', 'choix de la section d un cable', 'section de cable', 'dimensionner un cable', 'chute de tension', 'calcul de la chute de tension', 'chute de tension admissible', 'courant admissible', 'intensite admissible', 'cable 1 5 2 5 4 6', 'section de fil', 'quel fil pour', 'quelle section pour', 'cable cuivre ou aluminium', 'resistivite du cuivre', 'longueur maximale du cable', 'facteur de correction', 'mode de pose'], 'reponse': "Dimensionner un câble (démarche) :\n1) Courant d'emploi I_B : monophasé I = P / (U·cos φ) ; triphasé I = P / (√3·U·cos φ).\n2) Choisir la protection (calibre I_n ≥ I_B).\n3) Section selon le courant admissible I_z ≥ I_n, corrigé par les facteurs de pose (température, groupement, mode de pose).\n4) Vérifier la chute de tension : monophasé ΔU = 2·ρ·L·I / S ; triphasé ΔU = √3·ρ·L·I·cos φ / S (ρ cuivre ≈ 0,0225 Ω·mm²/m à chaud ; aluminium ≈ 0,036). Limite usuelle : 3 % pour l'éclairage, 5 % pour les autres usages (en BT depuis le tableau d'abonné).\n• Repères courants (cuivre, circuits domestiques, à vérifier selon la norme) : 1,5 mm² éclairage (10 A, disjoncteur 16 A max selon règles locales) ; 2,5 mm² prises (20 A) ; 6 mm² cuisinière (32 A) ; 10 mm² forts courants.\n• Exemple : 2 kW, 230 V, L = 40 m, S = 2,5 mm² ⇒ I = 8,7 A ; ΔU = 2 × 0,0225 × 40 × 8,7 / 2,5 = 6,3 V soit 2,7 % : acceptable.\nPour un calcul pas à pas, utilise mes calculateurs : « quelle section de câble pour … ».", 'mot_entier': True}, {'titre': 'bilan de puissance et installation domestique', 'cles': ['calcul du bilan de puissance', 'puissance installee', 'puissance d utilisation', 'facteur de simultaneite', 'facteur d utilisation', 'facteur d extension', 'puissance souscrite', 'abonnement electrique', 'installation electrique d une maison', 'tableau electrique', 'tableau de repartition', 'circuits d une maison', 'nombre de prises par circuit', 'installation domestique', 'norme nf c 15 100', 'nfc 15 100', 'nf c 15100', 'schema unifilaire', 'schema multifilaire', 'plan d installation electrique', 'nombre de points lumineux', 'gaine technique logement', 'etapes d une installation electrique', 'va et vient', 'telerupteur', 'minuterie', 'contacteur jour nuit', 'prise commandee', 'simple allumage', 'double allumage', 'detecteur de presence'], 'reponse': "Installation électrique d'un logement :\n• Bilan de puissance : P_installée = somme des puissances des récepteurs ; P_utilisée = P_installée × k_u × k_s (facteurs d'utilisation et de simultanéité). On en déduit la puissance de l'abonnement et la taille du disjoncteur de branchement.\n• Tableau de répartition : disjoncteur de branchement, interrupteurs/disjoncteurs différentiels 30 mA (type AC ou A), puis disjoncteurs divisionnaires par circuit : éclairage, prises, cuisinière, chauffe-eau, climatiseur, etc.\n• Règles usuelles (NF C 15-100, référence courante, à adapter aux règles locales) : circuits séparés pour éclairage et prises ; protections 16 A pour prises 2,5 mm² (max 8 prises par circuit en 16 A selon la norme) ; liaison équipotentielle dans les pièces d'eau ; volumes de sécurité dans la salle de bain ; prises avec terre.\n• Commandes : simple allumage (1 point), double allumage, va-et-vient (2 interrupteurs pour 1 lampe), permutateur (3 points ou +), télérupteur (nombreux points), minuterie, détecteur de présence.\n• Schémas : unifilaire (vue d'ensemble), multifilaire (câblage réel), plan d'implantation. Toujours faire vérifier par un professionnel avant mise sous tension.", 'mot_entier': True}, {'titre': 'eclairage', 'cles': ['eclairage', 'calcul d eclairage', 'flux lumineux', 'lumen', 'candela', 'eclairement', 'niveau d eclairement', 'lampe led', 'lampe a incandescence', 'lampe fluorescente', 'tube fluocompact', 'lampe a decharge', 'efficacite lumineuse', 'lm w', 'nombre de luminaires', 'methode du flux total', 'eclairage public', 'temperature de couleur', 'indice de rendu des couleurs', 'ballast', 'driver led', 'choisir une lampe', 'led ou halogene', 'economie d energie eclairage'], 'reponse': "Éclairage :\n• Grandeurs : flux lumineux Φ (lumen, lm) ; éclairement E (lux = lm/m²) ; intensité lumineuse I (candela) ; efficacité lumineuse (lm/W).\n• Niveaux d'éclairement conseillés : couloir 100 lux, salon/bureau 300 à 500 lux, atelier 300 à 750 lux, travail de précision 1 000 lux ou plus.\n• Nombre de luminaires (méthode du flux total) : N = (E × S) / (Φ_lampe × U × d) — U = facteur d'utilisation (0,4 à 0,7), d = facteur de dépréciation. Exemple : salle de 30 m², E = 300 lux, flux d'un luminaire = 3 000 lm, U = 0,6, d = 0,8 ⇒ N = 9 000 / (3 000 × 0,6 × 0,8) = 6,25 → 7 luminaires.\n• Sources : incandescence (10 à 15 lm/W, chaleur), halogène (15-25), fluocompacte (50-70), LED (80 à 150 lm/W et plus), décharge (sodium, iodures métalliques : éclairage public).\n• Température de couleur : 2 700 K blanc chaud, 4 000 K neutre, 6 500 K froid. IRC (rendu des couleurs) : viser > 80.\n• LED : alimentation par driver à courant constant ; durée de vie 25 000 à 50 000 h.", 'mot_entier': True}, {'titre': 'redressement et onduleur', 'cles': ['redressement', 'redresseur', 'pont de graetz', 'pont de diodes', 'redressement simple alternance', 'redressement double alternance', 'filtrage condensateur', 'ondulation', 'valeur moyenne redressee', 'tension redressee', 'alimentation continue', 'onduleur', 'onduleurs', 'ups', 'asi', 'alimentation sans interruption', 'onduleur solaire', 'onduleur de tension', 'convertisseur dc ac', 'convertisseur ac dc', 'hacheur', 'hacheurs', 'variation de vitesse mcc', 'rapport cyclique', 'hacheur serie', 'hacheur buck', 'boost', 'convertisseur dc dc', 'gradateur', 'convertisseur statique', 'electronique de puissance', 'thyristor redressement'], 'reponse': "Électronique de puissance (convertisseurs statiques) :\n• Redresseur (AC → DC) : simple alternance (1 diode), double alternance (pont de Graetz à 4 diodes). Valeur moyenne : U_moy = 2·U_max/π ≈ 0,637·U_max (double alternance) ; avec U_max = 230·√2 = 325 V ⇒ U_moy ≈ 207 V. Un condensateur de filtrage lisse la tension ; le triphasé (pont PD3) donne moins d'ondulation. Avec thyristors : redressement commandé (la tension moyenne varie avec l'angle d'amorçage).\n• Hacheur (DC → DC) : interrupteur commandé (MOSFET, IGBT) ; U_moy = α × U (α = rapport cyclique) pour le hacheur série (buck, abaisseur) ; le hacheur parallèle (boost) élève la tension : U_s = U_e / (1 − α). Utilisé pour la variation de vitesse des moteurs à courant continu et les alimentations à découpage.\n• Onduleur (DC → AC) : produit une tension alternative à partir d'une batterie ou d'un panneau solaire ; commande MLI (PWM) pour approcher une sinusoïde ; onduleurs « sinus pur » ou « modifié ».\n• Gradateur : fait varier la tension alternative (lumière, chauffage) avec triac.\n• Variateur de vitesse (moteur asynchrone) : redresseur + bus continu + onduleur MLI : permet de faire varier fréquence et tension.", 'mot_entier': True}, {'titre': 'variateur de vitesse et demarreur progressif', 'cles': ['variateur de vitesse', 'variateur de frequence', 'variateur', 'altivar', 'parametrage du variateur', 'rampe d acceleration', 'frequence de sortie', 'moteur asynchrone avec variateur', 'soft starter', 'demarrage progressif', 'difference variateur demarreur progressif', 'economie d energie variateur', 'pwm moteur', 'defaut variateur', 'freinage electrique', 'freinage par injection de courant continu', 'freinage a contre courant'], 'reponse': "Variateur de vitesse et démarreur progressif :\n• Variateur de fréquence : permet de régler la vitesse d'un moteur asynchrone en faisant varier la fréquence (n_s = 60·f/p) tout en maintenant le rapport U/f constant (flux constant). Avantages : démarrage progressif (rampe), vitesse variable, économie d'énergie sur pompes/ventilateurs (puissance ∝ n³), freinage contrôlé, protections intégrées.\n• Paramètres de base : tension et fréquence nominales, courant nominal, rampes d'accélération et de décélération, fréquences mini et maxi, sens de marche, source de consigne (potentiomètre, 0-10 V, 4-20 mA, bus de terrain).\n• Précautions : câble blindé, longueur limitée entre variateur et moteur, filtre CEM ; refroidissement du variateur ; consigner et attendre la décharge des condensateurs (plusieurs minutes) avant d'intervenir.\n• Démarreur progressif : augmente la tension progressivement (gradateur à thyristors) pour limiter l'appel de courant et le choc mécanique ; la vitesse finale reste celle du réseau (pas de variation).\n• Freinage : par injection de courant continu, par contre-courant, ou par résistance de freinage.", 'mot_entier': True}, {'titre': 'groupe electrogene', 'cles': ['groupe electrogene', 'generateur electrique', 'groupe electrogene calcul', 'choix d un groupe electrogene', 'puissance du groupe electrogene', 'kva ou kw', 'difference kva kw', 'inverseur de source', 'inverseur reseau groupe', 'commutation reseau groupe electrogene', 'entretien groupe electrogene', 'vidange groupe electrogene', 'groupe de secours', 'groupe electrogene diesel', 'consommation du groupe electrogene', 'branchement du groupe electrogene', 'couplage de groupes'], 'reponse': "Groupe électrogène (moteur thermique + alternateur) :\n• Choix de la puissance : somme des puissances des récepteurs × coefficient de simultanéité, puis marge (20 à 30 %), et tenir compte des appels de courant au démarrage des moteurs (3 à 7 × In). Un moteur de pompe de 2 kW peut demander un groupe de 5 kVA ou plus.\n• kVA et kW : S (kVA) = P (kW) / cos φ ; avec cos φ = 0,8, un groupe de 10 kVA fournit environ 8 kW.\n• Branchement : toujours passer par un inverseur de sources (manuel ou automatique, ATS) pour ne jamais coupler le groupe avec le réseau public (risque pour les agents SBEE et pour le matériel).\n• Mise à la terre du groupe obligatoire ; protection différentielle et disjoncteur en sortie.\n• Entretien : niveau d'huile, filtre à air, filtre gasoil, batterie de démarrage, refroidissement ; vidange toutes les 100 à 250 h selon le constructeur ; lui faire tourner à vide quelques minutes pour l'échauffer et le refroidir ; ne jamais faire le plein moteur en marche ; utiliser à l'air libre (monoxyde de carbone).\n• Consommation moyenne d'un diesel : environ 0,25 à 0,3 L par kWh produit à bonne charge.", 'mot_entier': True}, {'titre': 'energie solaire dimensionnement', 'cles': ['dimensionner une installation solaire', 'calcul installation solaire', 'dimensionnement solaire', 'calcul panneau solaire', 'nombre de panneaux solaires', 'puissance crete', 'wattcrete', 'capacite de batterie', 'batterie solaire', 'regulateur de charge', 'regulateur mppt', 'mppt pwm', 'autonomie des batteries', 'profondeur de decharge', 'installation solaire autonome', 'site isole solaire', 'kit solaire', 'installation photovoltaique', 'choisir un onduleur solaire', 'branchement des panneaux solaires serie parallele', 'panneaux en serie', 'panneaux en parallele', 'ensoleillement au benin', 'heures de soleil'], 'reponse': "Dimensionner une installation solaire autonome (méthode simple) :\n1) Besoins : E_jour (Wh/jour) = Σ (puissance × heures d'utilisation).\n2) Panneaux : P_crête = E_jour / (H_s × k), H_s = heures de plein soleil équivalent (au Bénin environ 4,5 à 6 h/jour selon la région et la saison), k ≈ 0,6 à 0,75 (pertes câbles, température, régulateur, batterie).\n3) Batteries : C (Ah) = E_jour × N_jours_autonomie / (U_batterie × DoD) ; DoD = profondeur de décharge admissible (50 % plomb, 80 % lithium).\n4) Régulateur : courant ≥ P_crête / U_batterie × 1,25 ; MPPT plus performant que PWM.\n5) Onduleur : puissance ≥ 1,25 × puissance simultanée maximale, tension d'entrée = tension batterie (12/24/48 V).\nExemple : E = 1 500 Wh/jour, H_s = 5 h, k = 0,7 ⇒ P_crête = 1 500 / (5 × 0,7) ≈ 430 Wc → 2 panneaux de 250 Wc. Batterie 12 V, 1 jour d'autonomie, DoD 50 % ⇒ C = 1 500 / (12 × 0,5) = 250 Ah.\nPanneaux en série : les tensions s'additionnent ; en parallèle : les courants s'additionnent. Vérifie toujours que la tension du champ reste dans la plage du régulateur.", 'mot_entier': True}, {'titre': 'installation de chantier et securite', 'cles': ['securite sur chantier', 'epi', 'equipements de protection individuelle', 'gants isolants', 'casque', 'chaussures de securite', 'lunettes de protection', 'travail en hauteur', 'echelle', 'harnais', 'risques electriques', 'electrocution', 'effets du courant sur le corps', 'seuil de dangerosite', 'premiers secours electrisation', 'que faire en cas d electrocution', 'incendie electrique', 'extincteur co2', 'classe d incendie', 'signalisation de securite', 'panneaux de securite', 'consignes de securite', 'danger du courant', 'tension dangereuse', 'ou est le danger'], 'reponse': "Sécurité électrique et sur chantier :\n• Effets du courant alternatif 50 Hz sur le corps (ordre de grandeur) : perception vers 0,5 mA ; contraction musculaire (impossible de lâcher) à partir de 10 mA ; fibrillation cardiaque possible à partir de 30 mA selon durée ; très dangereux à plusieurs centaines de mA. D'où le différentiel 30 mA.\n• Tension limite de sécurité : 50 V en milieu sec, 25 V en milieu humide (courant alternatif), 12 V en milieu très humide ou immergé.\n• EPI de base : casque, chaussures de sécurité isolantes, gants isolants (classe adaptée à la tension), lunettes, vêtements non conducteurs ; harnais en hauteur ; échelle bien calée, pas de bijoux métalliques.\n• Électrisation : couper d'abord l'alimentation (ou écarter la victime avec un objet isolant sec), appeler les secours, vérifier la respiration, pratiquer le massage cardiaque si arrêt ; ne jamais toucher la victime encore sous tension.\n• Incendie électrique : couper le courant, utiliser un extincteur au CO₂ ou à poudre ; jamais d'eau sur une installation sous tension.\n• Toujours : consigner, vérifier l'absence de tension, baliser la zone, travailler à deux.", 'mot_entier': True}, {'titre': 'moteur monophase et condensateur', 'cles': ['condensateur de demarrage', 'condensateur permanent', 'capacite du condensateur du moteur', 'condensateur de moteur', 'brancher un moteur monophase', 'inversion de sens moteur monophase', 'moteur de pompe monophase', 'moteur de ventilateur', 'moteur qui ronfle', 'moteur qui ne demarre pas condensateur', 'calcul du condensateur de moteur monophase', 'bobine auxiliaire', 'enroulement auxiliaire', 'moteur a bague de court circuit', 'moteur a poles fendus'], 'reponse': "Moteur asynchrone monophasé :\n• Un seul enroulement ne crée pas de champ tournant : il faut une phase auxiliaire déphasée de 90°, obtenue avec un condensateur.\n• Condensateur permanent : reste en service, bon fonctionnement, couple de démarrage moyen (pompes, ventilateurs). Condensateur de démarrage : supprimé après démarrage par un interrupteur centrifuge ou un relais (couple de démarrage fort : compresseurs, perceuses).\n• Capacité approximative : environ 70 à 80 µF par kW sous 230 V pour un condensateur permanent (ordre de grandeur ; suivre la plaque du moteur). Tension de service du condensateur ≥ 400 V.\n• Inversion du sens : inverser les bornes de l'enroulement auxiliaire par rapport à l'enroulement principal (ou le condensateur de côté).\n• Pannes classiques : moteur qui ronfle sans démarrer = condensateur hors service (testé à la capacité-mètre) ou enroulement auxiliaire coupé ; roulement serré ; moteur qui chauffe = condensateur trop petit/grand, surcharge.\n• Moteur universel (machines portatives) : fonctionne en continu comme en alternatif, balais, vitesse élevée.", 'mot_entier': True}, {'titre': 'code des couleurs des resistances', 'cles': ['code des couleurs', 'code couleur resistance', 'code couleurs des resistances', 'couleurs des resistances', 'lire une resistance', 'valeur d une resistance', 'bagues de resistance', 'resistance 4 bagues', 'resistance 5 bagues', 'noir marron rouge orange jaune vert bleu violet gris blanc', 'tolerance d une resistance', 'serie e12', 'serie e24', 'valeurs normalisees resistances', 'puissance d une resistance', 'resistance cms'], 'reponse': 'Code des couleurs des résistances :\nNoir 0 · Marron 1 · Rouge 2 · Orange 3 · Jaune 4 · Vert 5 · Bleu 6 · Violet 7 · Gris 8 · Blanc 9.\n• 4 bagues : 1er chiffre, 2e chiffre, multiplicateur (10^n), tolérance (or = ±5 %, argent = ±10 %).\n• 5 bagues : 3 chiffres, multiplicateur, tolérance (marron = ±1 %, rouge = ±2 %).\n• Exemple : jaune – violet – rouge – or = 4 7 × 10² = 4 700 Ω = 4,7 kΩ ±5 %.\n• Multiplicateur or = ×0,1 ; argent = ×0,01.\n• Séries normalisées : E12 (10, 12, 15, 18, 22, 27, 33, 39, 47, 56, 68, 82 et leurs multiples) et E24.\n• Puissance courante : 1/4 W, 1/2 W, 1 W… Toujours vérifier P = R·I² avant de choisir. Les résistances CMS portent un code chiffré (ex. 472 = 47 × 10² = 4,7 kΩ).', 'mot_entier': True}, {'titre': 'composants passifs', 'cles': ['composants electroniques', 'composants passifs', 'resistance condensateur bobine', 'condensateur electrolytique', 'condensateur ceramique', 'polarite du condensateur', 'reactance du condensateur', 'bobine inductance', 'constante de temps rc', 'constante de temps rl', 'marquage des condensateurs', 'potentiometre', 'ldr', 'photoresistance', 'thermistance', 'ctn', 'ctp', 'varistance', 'vdr', 'quartz', 'buzzer', 'relais electromagnetique', 'le relais'], 'reponse': "Composants passifs :\n• Résistance : limite le courant (loi d'Ohm). Potentiomètre : résistance variable à 3 broches (réglage de volume, diviseur de tension). LDR (photorésistance) : R baisse avec la lumière. CTN : R baisse quand la température monte ; CTP : R monte. Varistance (VDR) : protège contre les surtensions.\n• Condensateur : stocke des charges, Q = C·U ; énergie W = ½·C·U². Électrolytique (polarisé : respecter + et −, grande capacité), céramique/film (non polarisés, faible capacité). Réactance X_C = 1/(2π·f·C). Constante de temps du circuit RC : τ = R·C ; charge à 63 % en τ, quasi complète en 5τ.\n• Bobine (inductance) : stocke de l'énergie magnétique, W = ½·L·I² ; X_L = 2π·f·L ; τ = L/R.\n• Relais : interrupteur commandé par une bobine ; isole le circuit de commande du circuit de puissance ; mettre une diode de roue libre en parallèle de la bobine en continu.\n• Quartz : donne une fréquence très stable (horloges de microcontrôleurs).", 'mot_entier': True}, {'titre': 'led et calcul de resistance', 'cles': ['resistance pour une led', 'quelle resistance pour une led', 'calculer la resistance d une led', 'resistance de la led', 'resistance led', 'led et resistance', 'quelle resistance mettre avec une led', 'resistance en serie avec la led', 'led', 'diode electroluminescente', 'calcul de la resistance d une led', 'resistance en serie avec une led', 'resistance pour led', 'brancher une led', 'couleur de led', 'tension de led', 'courant d une led', 'led rouge verte bleue', 'led rgb', 'afficheur 7 segments', '7 segments', 'anode commune', 'cathode commune', 'led 5v', 'led sur arduino', 'led 3v3', 'led esp32', 'pourquoi une resistance avec une led'], 'reponse': "LED (diode électroluminescente) :\n• Elle ne se branche jamais directement sur une source : il faut une résistance en série pour limiter le courant.\n• Formule : R = (U_alimentation − U_LED) / I_LED.\n• Tension directe typique : rouge 1,8 à 2 V ; jaune/orange ≈ 2 V ; verte 2 à 3 V ; bleue/blanche 3 à 3,3 V.\n• Courant usuel : 10 à 20 mA (5 à 10 mA suffisent souvent pour un voyant).\n• Exemple : alimentation 5 V, LED rouge 2 V, 15 mA ⇒ R = (5 − 2)/0,015 = 200 Ω → prendre 220 Ω (valeur normalisée). Avec une alimentation 3,3 V (ESP32) : R = (3,3 − 2)/0,01 = 130 Ω → 150 Ω.\n• Polarité : la patte longue est l'anode (+), la plus courte la cathode (−) ; le méplat du boîtier côté cathode.\n• Plusieurs LED : en série (même courant, tensions qui s'ajoutent) ou chacune avec sa propre résistance en parallèle (mieux).\n• Afficheur 7 segments : 7 LED + point ; à anode commune ou cathode commune.", 'mot_entier': True}, {'titre': 'transistor bipolaire et mosfet', 'cles': ['transistor npn pnp', 'beta du transistor', 'saturation du transistor', 'transistor interrupteur', 'calcul resistance de base', 'resistance de base', 'mosfet', 'igbt', 'transistor mosfet', 'mosfet de puissance', 'grille source drain', 'commande d un relais par transistor', 'darlington', '2n2222', 'bc547', 'irf540', 'transistor de puissance', 'transistor amplificateur', 'montage emetteur commun'], 'reponse': 'Transistors :\n• Bipolaire (BJT) NPN/PNP : trois broches Base, Collecteur, Émetteur. Un petit courant de base I_B commande un courant de collecteur I_C = β·I_B (β ≈ 100 en moyenne). Régimes : bloqué (I_B = 0), linéaire (amplificateur), saturé (interrupteur fermé, V_CE ≈ 0,2 V).\n• Calcul de la résistance de base pour commuter une charge : I_B ≥ I_C / β × 2 (marge de saturation) ; R_B = (V_commande − 0,7) / I_B. Exemple : relais de 50 mA, β = 100, commande 5 V ⇒ I_B = 0,5 mA × 2 = 1 mA ⇒ R_B = (5 − 0,7)/0,001 = 4,3 kΩ → 4,7 kΩ (ou plus petit, 1 kΩ, par sécurité).\n• MOSFET : trois broches Grille, Drain, Source ; commandé en tension (pas de courant de grille en continu) ; très utilisé pour commuter de forts courants (R_DS(on) faible). Canal N : charge côté drain, source à la masse. Choisir un MOSFET « logic level » (ex. IRLZ44N) pour une commande 3,3 V/5 V.\n• IGBT : mélange MOSFET et bipolaire, pour la forte puissance (onduleurs, variateurs).\n• Charge inductive (relais, moteur) : mettre une diode de roue libre.', 'mot_entier': True}, {'titre': 'thyristor triac diac', 'cles': ['thyristor', 'thyristors', 'triac', 'diac', 'scr', 'amorcage d un thyristor', 'desamorcage du thyristor', 'gradateur triac', 'variateur de lumiere', 'angle d amorcage', 'gachette', 'anode cathode thyristor', 'thyristor commande', 'redressement commande', 'courant de maintien', 'bta16', 'bt136', 'optotriac', 'moc3021', 'relais statique', 'relais a semi conducteurs'], 'reponse': "Thyristor, triac et diac :\n• Thyristor (SCR) : diode commandée à 3 broches (anode, cathode, gâchette). Il conduit de l'anode vers la cathode après une impulsion sur la gâchette et continue à conduire jusqu'à ce que le courant tombe sous le courant de maintien I_H (blocage naturel en alternatif à chaque passage par zéro). Utilisé dans les redresseurs commandés.\n• Triac : deux thyristors têtes-bêches : conduit dans les deux sens, amorçage par la gâchette en alternatif. Utilisé dans les gradateurs (variateurs de lumière, régulation de chauffage) ; borne A1, A2, G.\n• Diac : composant à seuil (≈ 30 V) qui sert à déclencher le triac.\n• Gradateur : plus l'angle d'amorçage α est retardé, plus la tension efficace diminue : on fait varier la puissance.\n• Relais statique (SSR) : triac ou thyristors + optocoupleur ; commutation silencieuse, sans usure, sans contact mécanique.\n• Optotriac (MOC3021) : isole le microcontrôleur du secteur.", 'mot_entier': True}, {'titre': 'ne555 et oscillateurs', 'cles': ['ne555', '555', 'timer 555', 'temporisateur 555', 'montage astable', 'montage monostable', 'astable 555', 'monostable 555', 'clignotant a led', 'frequence du 555', 'periode du 555', 'calcul du 555', 'generateur de signaux', 'multivibrateur', 'oscillateur', 'oscillateur a quartz', 'generateur de frequence', 'schmitt trigger', 'comparateur', 'bascule de schmitt', 'comparateur lm393', 'lm358', 'lm741'], 'reponse': "NE555 (circuit intégré temporisateur) :\n• Monostable : une impulsion de déclenchement produit une impulsion de sortie de durée T = 1,1 × R × C. Exemple : R = 100 kΩ, C = 10 µF ⇒ T = 1,1 s (temporisation).\n• Astable : oscillateur carré continu avec deux résistances R1, R2 et C : f = 1,44 / ((R1 + 2·R2)·C). Rapport cyclique α = (R1 + R2)/(R1 + 2·R2). Exemple : R1 = 1 kΩ, R2 = 10 kΩ, C = 10 µF ⇒ f ≈ 1,44 / (21 000 × 10⁻⁵) ≈ 6,9 Hz (clignotant).\n• Alimentation de 4,5 à 15 V (selon le modèle) ; sortie jusqu'à 200 mA.\n• Comparateur : compare deux tensions (+ et −) ; sortie haute ou basse (LM393, LM311). Avec hystérésis (trigger de Schmitt) pour éviter les rebonds autour du seuil.\n• AOP (LM358, LM741) en montages linéaires : voir le cours amplificateur opérationnel.", 'mot_entier': True}, {'titre': 'logique combinatoire et boole', 'cles': ['algebre de boole', 'portes logiques', 'porte and', 'porte or', 'porte not', 'porte nand', 'porte nor', 'porte xor', 'table de verite', 'fonctions logiques', 'theoremes de de morgan', 'de morgan', 'simplification logique', 'tableau de karnaugh', 'karnaugh', 'logique combinatoire', 'equation logique', 'et ou non', 'nand universel', 'demi additionneur', 'additionneur complet', 'multiplexeur', 'decodeur', 'encodeur', 'comparateur binaire', 'circuit logique', 'cmos ttl', '74hc00'], 'reponse': 'Algèbre de Boole et portes logiques :\n• Variables à 2 états (0 ou 1). Opérations : ET (AND, a·b), OU (OR, a+b), NON (NOT, ā), NAND, NOR, OU exclusif XOR (a⊕b = 1 si a ≠ b).\n• Tables de vérité : ET = 1 seulement si a = b = 1 ; OU = 0 seulement si a = b = 0 ; XOR = 1 si les entrées sont différentes.\n• Lois de De Morgan : ̅(a·b) = ā + b̄ ; ̅(a + b) = ā · b̄.\n• Propriétés : a + 0 = a ; a·1 = a ; a + 1 = 1 ; a·0 = 0 ; a + a = a ; a + ā = 1 ; a·ā = 0 ; absorption : a + a·b = a.\n• NAND et NOR sont « universelles » : tout circuit logique peut être réalisé avec un seul type.\n• Simplification : par calcul ou par tableau de Karnaugh (regrouper les 1 voisins par puissances de 2).\n• Blocs : demi-additionneur (S = a⊕b, R = a·b), additionneur complet, multiplexeur (choisit une entrée), décodeur, comparateur.\n• Familles : TTL (5 V, série 74xx) et CMOS (3 à 15 V, 4000, 74HC).', 'mot_entier': True}, {'titre': 'logique sequentielle compteurs bascules', 'cles': ['bascule', 'bascules', 'bascule rs', 'bascule d', 'bascule jk', 'bascule t', 'logique sequentielle', 'compteur', 'compteur binaire', 'compteur asynchrone', 'compteur synchrone', 'registre a decalage', 'diviseur de frequence', 'flip flop', 'eeprom', 'flash', 'compteur modulo', 'decompteur', '74hc74', '74hc595', '74hc4017'], 'reponse': "Logique séquentielle (circuits à mémoire) :\n• Bascule : mémorise 1 bit. RS (Set/Reset), D (recopie l'entrée D sur front d'horloge), JK (RS sans état interdit ; J=K=1 → bascule), T (change d'état à chaque impulsion).\n• Compteur : suite de bascules ; compteur binaire modulo 2ⁿ (n bascules comptent de 0 à 2ⁿ − 1) ; la sortie de chaque étage divise la fréquence par 2 : un compteur de 4 bascules divise la fréquence d'horloge par 16.\n• Compteur asynchrone (chaque bascule commande la suivante) ou synchrone (horloge commune).\n• Registre à décalage : décale les bits à chaque impulsion (conversion série ↔ parallèle : 74HC595 pour piloter beaucoup de LED avec 3 broches d'un microcontrôleur).\n• Mémoires : RAM (volatile, rapide), ROM/EEPROM/Flash (non volatiles).\n• Horloge : signal carré (555, quartz) qui synchronise le système.\n• Systèmes numériques : conversion A/N (échantillonnage + quantification) et N/A.", 'mot_entier': True}, {'titre': 'numeration binaire hexadecimal', 'cles': ['binaire', 'systeme binaire', 'conversion binaire decimal', 'conversion decimal binaire', 'hexadecimal', 'systeme hexadecimal', 'conversion hexadecimal', 'octal', 'systemes de numeration', 'base 2', 'base 16', 'base 10', 'code bcd', 'code ascii', 'code gray', 'kilo octet', 'ko mo go', 'complement a 2', 'nombre signe', 'convertir en binaire', 'ecrire en binaire', 'combien de valeurs sur n bits'], 'reponse': 'Systèmes de numération :\n• Binaire (base 2) : chiffres 0 et 1. Hexadécimal (base 16) : 0-9 et A-F (A = 10, B = 11, C = 12, D = 13, E = 14, F = 15). Octal (base 8).\n• Binaire → décimal : on additionne les puissances de 2. 1011₂ = 8 + 0 + 2 + 1 = 11.\n• Décimal → binaire : divisions successives par 2, on lit les restes de bas en haut. 13 → 13/2 = 6 r1, 6/2 = 3 r0, 3/2 = 1 r1, 1/2 = 0 r1 ⇒ 1101₂.\n• Hexa ↔ binaire : un chiffre hexa = 4 bits. 0xB7 = 1011 0111. 0xFF = 255.\n• n bits permettent 2ⁿ valeurs (8 bits : 256 valeurs de 0 à 255 ; 10 bits : 1 024 ; 12 bits : 4 096).\n• Un octet = 8 bits ; 1 Ko = 1 024 octets (usage courant) ; 1 Mo = 1 024 Ko.\n• Nombres signés : complément à 2 (le bit de poids fort est le signe).\n• BCD : chaque chiffre décimal codé sur 4 bits. ASCII : code des caractères (A = 65, a = 97, 0 = 48).', 'mot_entier': True}, {'titre': 'arduino et microcontroleur', 'cles': ['arduino', 'carte arduino', 'arduino uno', 'arduino nano', 'microcontroleur', 'microcontrolleur', 'programmer un arduino', 'code arduino', 'setup et loop', 'pinmode', 'digitalwrite', 'digitalread', 'analogread', 'analogwrite', 'pwm arduino', 'arduino led', 'arduino capteur', 'arduino moteur', 'arduino servo', 'arduino relais', 'arduino lcd', 'arduino ide', 'atmega328', 'broches arduino', 'entrees sorties numeriques', 'entree analogique', 'resolution adc arduino', 'serial monitor', 'serial begin', 'delay arduino', 'millis', 'interruption arduino', 'breadboard', 'platine d essai'], 'reponse': "Arduino (ex. Uno : ATmega328, 5 V, 16 MHz) :\n• Programme = deux fonctions : setup() exécutée une fois (configuration) ; loop() répétée sans fin.\n• Broches numériques : pinMode(13, OUTPUT) ; digitalWrite(13, HIGH/LOW) ; digitalRead(2). Entrées analogiques : analogRead(A0) donne 0 à 1023 (10 bits) → tension = valeur × 5 / 1023. Sortie PWM (broches ~) : analogWrite(9, 0 à 255).\n• Exemple (LED clignotante) :\nvoid setup(){ pinMode(13, OUTPUT); }\nvoid loop(){ digitalWrite(13, HIGH); delay(500); digitalWrite(13, LOW); delay(500); }\n• Liaison série : Serial.begin(9600) ; Serial.println(valeur) pour afficher sur le moniteur série.\n• millis() permet de faire plusieurs tâches sans bloquer comme delay().\n• Courant maximum par broche : 20 mA (40 mA max absolu) : utiliser un transistor ou un relais pour les charges plus fortes ; ne jamais brancher un moteur directement.\n• Matériel de test : platine d'essai (breadboard), fils Dupont, résistances.", 'mot_entier': True}, {'titre': 'esp32 et connectivite', 'cles': ['esp32', 'carte esp32', 'esp32 wifi', 'esp32 bluetooth', 'programmer l esp32', 'esp32 arduino ide', 'broches esp32', 'gpio esp32', 'esp32 3v3', 'esp8266', 'nodemcu', 'wifi esp32', 'bluetooth esp32', 'serveur web esp32', 'esp32 capteur', 'esp32 relais', 'esp32 camera', 'esp32 cam', 'esp32 deep sleep', 'internet des objets', 'mqtt', 'blynk', 'thingspeak', 'telecommande wifi', 'domotique esp32', 'esp32 touch'], 'reponse': "ESP32 : microcontrôleur à 2 cœurs (240 MHz) avec Wi-Fi et Bluetooth intégrés, très utilisé pour l'IoT et la domotique.\n• Alimentation 3,3 V (logique 3,3 V : ne pas appliquer 5 V sur les broches !) ; courant par broche ≈ 12 mA conseillé (max 40 mA). Pour un relais/moteur : transistor ou module relais.\n• Programmation : Arduino IDE (ajouter la carte ESP32) ou MicroPython.\n• GPIO : sorties/entrées numériques, ADC 12 bits (0 à 4095, 18 canaux environ ; ADC2 limité avec le Wi-Fi), PWM sur presque toutes les broches, DAC (GPIO 25 et 26), tactile capacitif, I²C, SPI, UART.\n• Wi-Fi : WiFi.begin(ssid, mot_de_passe) ; serveur web WebServer pour piloter des sorties depuis le téléphone ; protocoles MQTT, HTTP.\n• Bluetooth (classique et BLE) pour commander depuis une application mobile.\n• Mode deep sleep : consommation très faible pour fonctionner sur batterie.\n• Broches à éviter au démarrage (strapping) : GPIO 0, 2, 12, 15… ; GPIO 34 à 39 : entrées seulement.\n• ESP32-CAM : module caméra avec carte SD.", 'mot_entier': True}, {'titre': 'capteurs', 'cles': ['capteur', 'capteurs', 'capteur de temperature', 'capteur de proximite', 'capteur inductif', 'capteur capacitif', 'capteur photoelectrique', 'capteur ultrasons', 'hc sr04', 'capteur de distance', 'capteur de pression', 'capteur de niveau', 'capteur de courant', 'capteur a effet hall', 'acs712', 'capteur dht11', 'dht22', 'ds18b20', 'pt100', 'thermocouple', 'capteur pir', 'detecteur de mouvement', 'capteur de fin de course', 'fin de course', 'capteur de debit', 'capteur de gaz', 'capteur d humidite', 'capteur de vitesse', 'codeur incremental', 'capteur analogique', 'capteur tout ou rien', 'sortie 4 20 ma', 'signal 4 20 ma', 'signal 0 10 v', 'npn ou pnp capteur'], 'reponse': "Capteurs :\n• Rôle : convertir une grandeur physique (température, distance, pression, courant, vitesse…) en signal électrique.\n• Tout ou rien (TOR) : fin de course (contact mécanique), détecteur inductif (métaux, portée 2 à 30 mm), capacitif (tout matériau, niveaux), photoélectrique (barrage, reflex, proximité), PIR (infrarouge passif, mouvement), ILS/reed (aimant).\n• Câblage TOR 3 fils : marron +, bleu −, noir signal ; sortie PNP (commute le +) ou NPN (commute le −) — respecter le type d'entrée de l'automate.\n• Analogiques : tension 0-10 V ou courant 4-20 mA (le 4 mA représente le zéro, ce qui permet de détecter un fil coupé).\n• Température : LM35 (10 mV/°C), DS18B20 (numérique 1-Wire), DHT11/DHT22 (température + humidité), Pt100 (résistance 100 Ω à 0 °C, précis), thermocouple (type K, plage large).\n• Distance : ultrasons HC-SR04 (distance = vitesse du son × temps / 2, ≈ 343 m/s) ; infrarouge.\n• Courant : ACS712 (effet Hall), tore, shunt. Vitesse/position : codeur incrémental, capteur de Hall.\n• Pression, niveau, débit : capteurs à jauges, flotteurs, électrodes.", 'mot_entier': True}, {'titre': 'alimentation et regulation de tension', 'cles': ['alimentation stabilisee', 'regulateur de tension', 'lm7805', 'lm317', 'regulateur lineaire', 'alimentation a decoupage', 'buck converter', 'convertisseur abaisseur', 'boost converter', 'convertisseur survolteur', 'chargeur', 'chargeur de telephone', 'alimentation 5v', 'alimentation 12v', 'adaptateur secteur', 'transformateur d alimentation', 'redressement filtrage regulation', 'diode zener regulation', 'alimentation de laboratoire', 'dissipation thermique', 'radiateur', 'dissipateur', 'chargeur de batterie', 'batterie lithium', 'batterie plomb', 'batterie 18650', 'bms', 'tension de batterie', 'charge d une batterie'], 'reponse': 'Alimentations et batteries :\n• Chaîne classique : transformateur → redresseur (pont de diodes) → filtrage (condensateur) → régulateur.\n• Régulateurs linéaires : 7805 (5 V), 7812 (12 V), LM317 (réglable) ; simples mais dissipent la différence de tension en chaleur : P = (U_e − U_s) × I. Exemple : 12 V → 5 V à 0,5 A ⇒ P = 3,5 W : radiateur obligatoire.\n• Alimentation à découpage (SMPS) : transistor qui commute à haute fréquence ; rendement 80 à 95 %, petite et légère (chargeurs de téléphone, PC). Convertisseurs buck (abaisseur), boost (élévateur), buck-boost.\n• Batteries : plomb (12 V, 2 V par élément, décharge max ≈ 50 %), lithium-ion (3,7 V nominal par élément, 4,2 V chargé, ne pas descendre sous 3 V), LiFePO₄ (3,2 V, très durable). Les batteries lithium exigent un BMS (protection contre surcharge, décharge profonde, court-circuit).\n• Énergie : W (Wh) = U × C (Ah). Exemple : 12 V 100 Ah ⇒ 1 200 Wh.\n• Sécurité : ne jamais court-circuiter une batterie, ne pas la percer, ne pas la charger à tension trop élevée.', 'mot_entier': True}, {'titre': 'oscilloscope et mesures electroniques', 'cles': ['utiliser un oscilloscope', 'lire un oscilloscope', 'calibre de l oscilloscope', 'sonde d oscilloscope', 'utiliser un multimetre', 'mesurer une tension', 'mesurer un courant', 'mesurer une resistance', 'test de continuite', 'test de diode', 'generateur de fonctions', 'mesurer la frequence', 'mesurer la periode', 'valeur efficace mesure', 'tension max et efficace', 'vcrete', 'peak to peak', 'crete a crete'], 'reponse': "Mesures en électronique et électricité :\n• Multimètre : en tension, en parallèle ; en courant, en série (couper le circuit) ; en résistance, hors tension et composant isolé. Test de continuité (bip), test de diode (sens passant ≈ 0,6 V pour le silicium). Choisir le bon calibre ; commencer par le plus grand. Pour le 230 V : catégorie CAT III/IV, cordons en bon état.\n• Pince ampèremétrique : mesure le courant sans couper le circuit (autour d'un seul conducteur, jamais autour du câble complet phase + neutre).\n• Mégohmmètre : mesure l'isolement (500 V ou 1 000 V) ; hors tension, déconnecter l'électronique ; moteur correct : plusieurs MΩ (en général > 1 MΩ, souvent bien plus).\n• Oscilloscope : affiche la tension en fonction du temps. Réglages : sensibilité verticale (V/div), base de temps (ms/div), couplage DC/AC, déclenchement (trigger). Lecture : période T = nombre de divisions × base de temps ; f = 1/T ; U_max = divisions × V/div ; U_eff = U_max/√2 pour une sinusoïde.\n• Une sonde ×10 divise la tension par 10 : compenser la sonde avec le signal carré de test.", 'mot_entier': True}, {'titre': 'soudure et circuit imprime', 'cles': ['soudure', 'souder', 'fer a souder', 'soudure a l etain', 'etain', 'flux de soudure', 'dessouder', 'tresse a dessouder', 'circuit imprime', 'pcb', 'realiser un circuit imprime', 'gravure perchlorure', 'kicad', 'fritzing', 'proteus', 'simulation electronique', 'schema electronique', 'cao electronique', 'breadboard soudure', 'cms', 'composants cms', 'soudure froide', 'bonne soudure', 'temperature du fer a souder'], 'reponse': "Soudure électronique et circuits imprimés :\n• Fer à souder : 300 à 350 °C pour l'étain au plomb ou sans plomb (selon l'alliage) ; panne propre et étamée ; fil d'étain avec flux incorporé.\n• Bonne soudure : on chauffe ensemble la patte et la pastille 1 à 2 s, on amène l'étain, on le laisse couler, puis on retire — résultat brillant, en forme de petit cône. Soudure froide : terne, granuleuse, mauvais contact.\n• Dessoudage : pompe à dessouder ou tresse.\n• Ordre de soudure : composants bas en premier (résistances, diodes), puis condensateurs, circuits intégrés (sur supports), connecteurs ; protéger les composants sensibles à la chaleur et à l'électricité statique.\n• Circuit imprimé (PCB) : schéma → routage (KiCad, EasyEDA, Proteus Ares) → fabrication (gravure au perchlorure ferrique ou commande en usine) → perçage → soudure.\n• Simulation : Proteus, LTspice, Tinkercad Circuits (gratuit en ligne).\n• Sécurité : ventiler (fumées de flux), ne jamais toucher la panne, poser le fer sur son support.", 'mot_entier': True}, {'titre': 'amplification et filtres', 'cles': ['amplificateur', 'amplification', 'gain en tension', 'gain en db', 'decibel', 'decibels', 'gain d un amplificateur', 'filtre passe bas', 'filtre passe haut', 'filtre passe bande', 'filtre rc', 'frequence de coupure', 'diagramme de bode', 'bande passante', 'fonction de transfert', 'amplificateur audio', 'ampli audio', 'impedance du haut parleur', 'ampli classe ab', 'classe d', 'distorsion', 'amplificateur de puissance', 'tda2030', 'lm386'], 'reponse': 'Amplification et filtres :\n• Gain en tension A_v = U_s / U_e. En décibels : G(dB) = 20·log₁₀(A_v) ; +20 dB = ×10 ; +6 dB ≈ ×2 ; −3 dB ≈ ×0,707. Gain en puissance : 10·log₁₀(P_s/P_e).\n• Filtre RC passe-bas : laisse passer les basses fréquences ; fréquence de coupure f_c = 1/(2π·R·C) (atténuation de 3 dB). Exemple : R = 1,6 kΩ, C = 100 nF ⇒ f_c ≈ 995 Hz. Passe-haut : condensateur en série, résistance à la masse. Passe-bande : combine les deux.\n• Diagramme de Bode : gain (dB) et phase en fonction de la fréquence (échelle logarithmique) ; pente −20 dB/décade pour un filtre du 1er ordre.\n• Bande passante : plage de fréquences où le gain est au plus 3 dB sous le maximum.\n• Amplificateurs audio : classes A, B, AB (courantes), D (à découpage, très bon rendement) ; circuits : LM386 (petit), TDA2030 (≈ 14 W). Impédance haut-parleur : 4 ou 8 Ω ; ne pas faire fonctionner sans charge.', 'mot_entier': True}, {'titre': 'cem et parasites', 'cles': ['cem', 'compatibilite electromagnetique', 'parasites', 'perturbations electromagnetiques', 'blindage', 'cable blinde', 'filtre cem', 'ferrite', 'decouplage', 'condensateur de decouplage', 'antiparasite', 'masse et terre', 'boucle de masse', 'bruit electrique', 'esd', 'decharge electrostatique', 'protection esd', 'foudre', 'surtension'], 'reponse': "Compatibilité électromagnétique (CEM) :\n• Un équipement doit fonctionner sans perturber les autres ni être perturbé.\n• Sources : démarrages de moteurs, relais et contacteurs (arcs), variateurs et alimentations à découpage, foudre, décharges électrostatiques (ESD).\n• Remèdes : câbles blindés (blindage relié à la masse aux deux extrémités pour les hautes fréquences), câbles de puissance séparés des câbles de signal, torsadage des paires, filtres CEM sur l'alimentation, tores de ferrite, condensateurs de découplage (100 nF près des circuits intégrés), diodes de roue libre et réseaux RC sur les bobines, parafoudres.\n• Masse : éviter les boucles de masse (une seule référence en un point) ; équipotentialité des masses.\n• ESD : bracelet antistatique, sacs antistatiques pour manipuler les composants sensibles (CMOS).", 'mot_entier': True}, {'titre': 'raspberry pi et linux embarque', 'cles': ['raspberry pi', 'raspberry', 'raspberry pi pico', 'raspberry pi gpio', 'python raspberry', 'linux embarque', 'serveur local raspberry', 'domotique raspberry', 'home assistant', 'nano ordinateur', 'carte sd raspberry', 'raspberry pi os', 'pico w', 'micropython'], 'reponse': "Raspberry Pi : nano-ordinateur sous Linux (Raspberry Pi OS) avec ports USB, HDMI, Ethernet/Wi-Fi et broches GPIO 3,3 V (40 broches). Programmable en Python (bibliothèques gpiozero, RPi.GPIO).\n• Usages : serveur domotique (Home Assistant), caméra de surveillance, station météo, centre multimédia, passerelle IoT, serveur de fichiers.\n• Différence avec Arduino/ESP32 : Raspberry Pi = ordinateur complet (système d'exploitation, multi-tâches, mais ne s'arrête pas brutalement : toujours l'éteindre proprement) ; Arduino/ESP32 = microcontrôleur (temps réel simple, faible consommation, démarrage instantané).\n• Raspberry Pi Pico : microcontrôleur (RP2040) programmable en MicroPython ou C/C++, peu coûteux.\n• Alimentation 5 V avec bonne alimentation (≥ 2,5 à 3 A pour les modèles récents) ; GPIO limité en courant : utiliser des modules relais ou des transistors.", 'mot_entier': True}, {'titre': 'automate programmable industriel', 'cles': ['automate', 'automate programmable', 'api', 'automate programmable industriel', 'plc', 'schneider', 'siemens s7', 's7 1200', 's7 300', 'tia portal', 'step 7', 'zelio', 'zelio logic', 'twido', 'modicon', 'logo siemens', 'entrees sorties de l automate', 'module d entrees sorties', 'cpu automate', 'cycle automate', 'cycle de scrutation', 'programmer un automate', 'langages de programmation api', 'iec 61131', 'cablage d un automate', 'alimentation de l automate'], 'reponse': "Automate programmable industriel (API / PLC) : calculateur robuste qui commande un procédé à partir de capteurs (entrées) et d'actionneurs (sorties), selon un programme.\n• Structure : alimentation, CPU (unité centrale), modules d'entrées (TOR 24 V DC ou analogiques 0-10 V / 4-20 mA), modules de sorties (relais, transistors, triacs), communication (Ethernet, Profibus, Modbus), console/PC de programmation.\n• Cycle : lecture des entrées → exécution du programme → écriture des sorties (cycle de quelques ms).\n• Langages (norme CEI 61131-3) : Ladder/LD (schéma à contacts), FBD (blocs fonctions), ST (texte structuré), IL (liste d'instructions), SFC (Grafcet).\n• Marques courantes : Siemens (LOGO!, S7-1200, TIA Portal), Schneider (Zelio, Modicon, EcoStruxure), Allen-Bradley, Mitsubishi, Omron.\n• Câblage : entrée TOR = capteur/bouton alimentés en 24 V entre la source et l'entrée ; sortie = relais ou transistor qui commande bobine/contacteur ; prévoir fusibles et antiparasitage des charges inductives.\n• Avantage sur la logique câblée : modification sans recâbler, diagnostic, supervision.", 'mot_entier': True}, {'titre': 'langage ladder', 'cles': ['ladder', 'langage ladder', 'schema a contacts', 'programmation ladder', 'contact no nc', 'contact ouvert ferme', 'bobine ladder', 'bobine set reset', 'auto maintien', 'auto maintien ladder', 'temporisation ladder', 'ton tof tp', 'temporisateur ladder', 'compteur ctu ctd', 'comparaison ladder', 'exemple ladder', 'marche arret ladder', 'front montant', 'front descendant', 'memento', 'bit memoire'], 'reponse': "Langage Ladder (à contacts) :\n• Lecture de gauche à droite : à gauche les conditions (contacts), à droite la sortie (bobine). Contact NO (—| |—) passe si le bit = 1 ; NC (—|/|—) passe si le bit = 0 ; en série = ET ; en parallèle = OU.\n• Auto-maintien (marche/arrêt) : rung 1 : (Marche OU Sortie) ET NON Arrêt → Sortie. Le contact de la sortie en parallèle sur Marche maintient l'alimentation après relâchement du bouton ; l'arrêt (NF) coupe le rung. Convention de sécurité : câbler le bouton d'arrêt en contact NF (une coupure de fil arrête la machine).\n• Set/Reset (S/R) : mémorise une sortie jusqu'à ce qu'un reset l'efface.\n• Temporisations : TON (retard à la montée, ex. T#5s), TOF (retard à la retombée), TP (impulsion).\n• Compteurs : CTU (incrémente), CTD (décrémente) avec valeur de présélection PV.\n• Fronts : détection du passage 0 → 1 (front montant) pour ne compter qu'une fois.\n• Bonnes pratiques : commenter et nommer les variables, ne pas utiliser deux fois une même bobine.", 'mot_entier': True}, {'titre': 'grafcet', 'cles': ['grafcet', 'le grafcet', 'diagramme fonctionnel en sequence', 'etape grafcet', 'transition grafcet', 'reception grafcet', 'regles du grafcet', 'etape initiale', 'grafcet point de vue systeme', 'grafcet point de vue pc', 'divergence en ou', 'divergence en et', 'saut d etapes', 'reprise de sequence', 'action associee', 'grafcet exemple', 'cahier des charges grafcet', 'comment faire un grafcet', 'syntaxe du grafcet'], 'reponse': "GRAFCET (GRAphe Fonctionnel de Commande des Étapes et Transitions), norme IEC 60848 :\n• Éléments : étapes (carrés, numérotées ; étape initiale en double carré), transitions (traits horizontaux) avec réceptivités (conditions de franchissement, ex. a·b ou t/X5/3 s), liaisons orientées, actions associées aux étapes (rectangles à droite).\n• Règles d'évolution :\n 1) Situation initiale : étapes initiales actives.\n 2) Une transition est validée si toutes les étapes immédiatement précédentes sont actives.\n 3) Elle est franchie si elle est validée ET sa réceptivité est vraie.\n 4) Le franchissement désactive les étapes amont et active les étapes aval, simultanément.\n 5) Plusieurs transitions franchissables en même temps sont franchies simultanément.\n 6) Une étape activée et désactivée en même temps reste active.\n• Structures : séquence unique, divergence/convergence en OU (choix : sélection), en ET (parallélisme : double trait), saut d'étapes, reprise de séquence.\n• Points de vue : système (spécification), partie opérative, partie commande.\n• Exemple : chariot : étape 1 (repos) → bouton départ → étape 2 (avance moteur) → fin de course a → étape 3 (recul) → fin de course b → retour étape 1.", 'mot_entier': True}, {'titre': 'schemas electriques et symboles', 'cles': ['schema electrique', 'schemas electriques', 'symboles electriques', 'symbole electrique', 'normalisation des symboles', 'reperage des appareils', 'repere km', 'q1 f1', 'schema de puissance', 'schema de commande', 'schema developpe', 'schema de principe', 'schema de cablage', 'bornier', 'reperage des bornes', '13 14 contact no', '21 22 contact nf', 'a1 a2 bobine', 'contacteur symbole', 'bouton poussoir symbole', 'reperage des conducteurs', 'numerotation des fils', 'folio', 'folio schema', 'lire un schema electrique', 'dessin electrique', 'caneco', 'eplan', 'see electrical', 'qelectrotech'], 'reponse': "Schémas électriques :\n• Schéma de puissance (moteur, charge) et schéma de commande (boutons, bobines) séparés ; schéma développé = chaque appareil dessiné en plusieurs endroits selon sa fonction, repéré de façon unique.\n• Repères usuels : Q = sectionneur/disjoncteur, F = protection (fusible, relais thermique), KM = contacteur, KA = relais auxiliaire, KT = temporisateur, S = bouton/interrupteur, H = voyant, M = moteur, T = transformateur, X = bornier, B = capteur, Y = électrovanne.\n• Repérage des bornes : puissance 1-2, 3-4, 5-6 (L1-T1, L2-T2, L3-T3) ; bobine A1-A2 ; contacts auxiliaires : 13-14 (NO), 21-22 (NC), 31-32 (NC), 43-44 (NO)… (le 1er chiffre = numéro de contact, le 2e = fonction : 1-2 = NF, 3-4 = NO).\n• Conducteurs : repérage par numéros de fils aux deux extrémités ; couleurs : phases noir/marron/gris (ou rouge/jaune/bleu selon pays), neutre bleu clair, PE vert-jaune, commande 24 V DC bleu, commande 230 V AC rouge (selon pratique).\n• Logiciels : QElectroTech (gratuit), EPLAN, SEE Electrical, AutoCAD Electrical, Caneco.\n• Bonne pratique : tenir à jour le dossier électrique et le laisser dans l'armoire.", 'mot_entier': True}, {'titre': 'commande de moteurs contacteur', 'cles': ['commande d un moteur', 'marche arret moteur', 'demarrage direct', 'demarrage direct moteur', 'inversion de sens de rotation', 'inverseur de sens', 'inversion du sens de rotation du moteur triphase', 'verrouillage electrique', 'verrouillage mecanique', 'contacteur de puissance', 'contacteurs', 'bouton marche arret', 'auto maintien contacteur', 'schema marche arret', 'schema de commande moteur', 'va et vient moteur', 'marche par impulsions', 'arret d urgence', 'coup de poing arret d urgence', 'relais de securite', 'bouton d urgence', 'categorie de securite', 'safety'], 'reponse': "Commande de moteurs par contacteur :\n• Démarrage direct (1 sens) : sectionneur Q1 → fusibles/disjoncteur-moteur → contacteur KM1 → relais thermique F2 → moteur. Commande : S1 (arrêt, NF) en série avec S2 (marche, NO) en parallèle avec contact auxiliaire 13-14 de KM1 (auto-maintien), bobine KM1 (A1-A2), contact NF du relais thermique 95-96 en série.\n• Inversion de sens : deux contacteurs KM1 (sens 1) et KM2 (sens 2) qui permutent deux phases ; verrouillage électrique obligatoire (contact NF de KM2 dans la bobine de KM1 et inversement) pour éviter le court-circuit entre phases ; verrouillage mécanique sur les blocs si disponible ; temporisation courte souhaitable entre les deux sens.\n• Marche par impulsions (jog) : bouton qui alimente la bobine sans auto-maintien.\n• Arrêt d'urgence : bouton coup de poing rouge/jaune, à contact NF et verrouillage, relié à un relais de sécurité qui coupe l'alimentation des contacteurs (catégorie de sécurité selon l'analyse de risque).\n• Contacteur : choisir la catégorie d'emploi AC-3 (moteurs à cage, démarrage et arrêt moteur lancé) ou AC-4 (freinage à contre-courant) et le courant nominal ≥ courant moteur.\n• Pour un démarrage étoile-triangle ou rotorique : demande « démarrage des moteurs asynchrones ».", 'mot_entier': True}, {'titre': 'regulation et pid', 'cles': ['regulation', 'regulation pid', 'pid', 'correcteur pid', 'regulateur pid', 'action proportionnelle', 'action integrale', 'action derivee', 'boucle ouverte', 'boucle fermee', 'asservissement', 'regulation de temperature', 'regulation de vitesse', 'consigne mesure erreur', 'erreur statique', 'reponse indicielle', 'stabilite', 'depassement', 'temps de reponse', 'systeme du premier ordre', 'systeme du second ordre', 'ziegler nichols', 'reglage du pid', 'tout ou rien regulation', 'hysteresis regulation', 'thermostat'], 'reponse': "Régulation et PID :\n• Boucle ouverte : la commande ne dépend pas de la mesure. Boucle fermée (asservissement/régulation) : on compare la mesure à la consigne ; l'erreur e = consigne − mesure agit sur le correcteur, qui commande l'actionneur.\n• Régulation tout ou rien (thermostat) : simple, avec hystérésis pour éviter de commuter sans cesse.\n• Correcteur PID : u(t) = Kp·e + Ki·∫e dt + Kd·de/dt.\n  – P (proportionnel) : réagit à l'erreur ; trop fort = oscillations, trop faible = lent et erreur statique.\n  – I (intégral) : supprime l'erreur statique ; trop fort = dépassement et instabilité.\n  – D (dérivé) : anticipe, amortit ; sensible au bruit.\n• Réglage empirique (Ziegler-Nichols) : monter Kp jusqu'à l'oscillation entretenue (Ku, période Tu), puis Kp = 0,6 Ku, Ti = 0,5 Tu, Td = 0,125 Tu.\n• Qualités d'un système asservi : stabilité, précision (erreur statique), rapidité (temps de réponse à 5 %), dépassement.\n• Exemples : régulation de température d'un four, de niveau d'un réservoir, de vitesse d'un moteur avec variateur et codeur.", 'mot_entier': True}, {'titre': 'supervision scada et bus de terrain', 'cles': ['supervision', 'scada', 'ihm', 'hmi', 'ecran tactile', 'interface homme machine', 'bus de terrain', 'profibus', 'profinet', 'modbus', 'modbus rtu', 'modbus tcp', 'can bus', 'canopen', 'ethernet industriel', 'rs485', 'rs232', 'protocole de communication industriel', 'asi bus', 'as interface', 'io link', 'opc ua', 'reseau industriel', 'communication entre automates', 'passerelle modbus', 'iot industriel', 'industrie 4 0', 'digitalisation', 'capteur connecte', 'cloud industriel'], 'reponse': "Supervision et communication industrielle :\n• IHM (HMI) : écran tactile local pour piloter une machine. Supervision (SCADA) : logiciel sur PC qui affiche l'état de l'installation, les alarmes, les courbes, les historiques, et permet de commander à distance (WinCC, Vijeo Designer, Ignition, Wonderware).\n• Bus de terrain : relier capteurs, variateurs et automates avec peu de fils : Modbus RTU (RS-485, 9 600 à 115 200 bauds, très répandu), Modbus TCP (Ethernet), Profibus/Profinet (Siemens), CANopen, AS-Interface (capteurs TOR sur 2 fils), IO-Link.\n• RS-485 : liaison différentielle sur paire torsadée, jusqu'à 1 200 m, 32 appareils ; résistances de terminaison 120 Ω aux deux extrémités ; RS-232 : point à point, 15 m.\n• OPC UA : standard d'échange entre systèmes industriels.\n• Industrie 4.0 : capteurs connectés, données dans le cloud, maintenance prédictive, jumeaux numériques.\n• Cybersécurité : séparer le réseau industriel du réseau bureautique, mots de passe, mises à jour.", 'mot_entier': True}, {'titre': 'robotique et mecatronique', 'cles': ['robot', 'robotique', 'bras robotise', 'robot industriel', 'degres de liberte', 'actionneur', 'actionneurs', 'servomoteur', 'servo moteur', 'moteur pas a pas commande', 'driver a4988', 'moteur bldc', 'mecatronique', 'machine cnc', 'imprimante 3d', 'reducteur robot', 'effecteur', 'capteur proprioceptif', 'drone', 'quadricoptere', 'cobot', 'vision industrielle', 'robot mobile', 'ligne de production automatisee'], 'reponse': "Robotique et mécatronique :\n• Mécatronique = mécanique + électronique + informatique + automatique.\n• Robot : système articulé commandé par un contrôleur ; degrés de liberté = nombre de mouvements indépendants (un bras 6 axes a 6 ddl) ; effecteur (pince, outil, ventouse).\n• Actionneurs : moteur à courant continu (avec réducteur et codeur), servomoteur (commande par impulsion PWM de 1 à 2 ms toutes les 20 ms : 1,5 ms = position centrale), moteur pas à pas (déplacement par pas, par ex. 1,8° soit 200 pas/tour ; drivers A4988 ou DRV8825 avec microstepping), moteur brushless BLDC (rendement élevé, commande par variateur ESC : drones).\n• Machines à commande numérique (CNC), imprimantes 3D : axes pilotés en pas à pas, code G.\n• Cobots : robots collaboratifs sûrs à côté des humains. AGV : véhicules guidés automatiques.\n• Capteurs : codeurs, fins de course, caméras (vision), capteurs de force, lidars.\n• Sécurité : cages de protection, barrières immatérielles, arrêts d'urgence.", 'mot_entier': True}, {'titre': 'formules de maths essentielles', 'cles': ['formules de maths', 'formules mathematiques', 'mathematiques', 'maths', 'identites remarquables', 'theoreme de pythagore', 'pythagore', 'theoreme de thales', 'thales', 'trigonometrie', 'sinus cosinus tangente', 'sin cos tan', 'soh cah toa', 'cercle trigonometrique', 'aire du cercle', 'perimetre du cercle', 'aire d un triangle', 'aire d un rectangle', 'volume d un cylindre', 'volume d une sphere', 'volume d un cone', 'surface d une sphere', 'aires et volumes', 'calcul de pourcentage', 'pourcentage', 'regle de trois', 'proportionnalite', 'puissances de 10', 'ecriture scientifique', 'racine carree', 'equation du premier degre', 'moyenne arithmetique'], 'reponse': 'Maths essentielles :\n• Identités remarquables : (a+b)² = a² + 2ab + b² ; (a−b)² = a² − 2ab + b² ; a² − b² = (a−b)(a+b).\n• Pythagore (triangle rectangle) : a² + b² = c² (c = hypoténuse). Exemple : 3, 4 → c = 5.\n• Thalès : si (BC) // (DE), AB/AD = AC/AE = BC/DE.\n• Trigonométrie (triangle rectangle) : sin α = opposé/hypoténuse ; cos α = adjacent/hypoténuse ; tan α = opposé/adjacent. Rappel mnémotechnique : SOH-CAH-TOA. sin²α + cos²α = 1.\n• Cercle : périmètre 2πr ; aire πr². Triangle : aire = base × hauteur / 2. Rectangle : L × l. Trapèze : (B + b) × h / 2.\n• Volumes : parallélépipède L·l·h ; cylindre π r² h ; cône (1/3) π r² h ; sphère (4/3) π r³ ; surface de la sphère 4π r².\n• Pourcentage : p % de x = x × p/100 ; augmentation de p % : × (1 + p/100). Règle de trois : si a donne b, alors c donne b × c / a.\n• Puissances de 10 : 10ⁿ × 10ᵐ = 10ⁿ⁺ᵐ ; k = 10³, M = 10⁶, G = 10⁹, m = 10⁻³, µ = 10⁻⁶, n = 10⁻⁹, p = 10⁻¹².\n• Équation ax + b = 0 ⇒ x = −b/a.', 'mot_entier': True}, {'titre': 'equation du second degre et fonctions', 'cles': ['equation du second degre', 'second degre', 'discriminant', 'delta', 'racines d une equation', 'formule du discriminant', 'ax2 bx c', 'resoudre une equation du second degre', 'polynome', 'factorisation', 'calcul de derivee', 'derivee d une fonction', 'derivees usuelles', 'calcul integral', 'fonction lineaire', 'fonction affine', 'fonction exponentielle', 'fonction logarithme', 'ln et exp', 'logarithme decimal', 'tableau de variation', 'etudier une fonction'], 'reponse': "Fonctions et équations :\n• Équation du second degré ax² + bx + c = 0 : Δ = b² − 4ac. Si Δ > 0 : deux racines x = (−b ± √Δ)/(2a) ; Δ = 0 : une racine double x = −b/(2a) ; Δ < 0 : pas de racine réelle. Somme des racines = −b/a ; produit = c/a.\n• Fonction affine f(x) = ax + b (droite, pente a, ordonnée à l'origine b).\n• Dérivées usuelles : (xⁿ)' = n·xⁿ⁻¹ ; (eˣ)' = eˣ ; (ln x)' = 1/x ; (sin x)' = cos x ; (cos x)' = −sin x ; (u·v)' = u'v + uv' ; (u/v)' = (u'v − uv')/v² ; (constante)' = 0. Si f' > 0, f est croissante ; f' < 0, décroissante.\n• Primitives : ∫xⁿ dx = xⁿ⁺¹/(n+1) + C ; ∫eˣ dx = eˣ + C ; ∫(1/x) dx = ln|x| + C ; ∫sin x dx = −cos x + C.\n• Logarithmes : ln(ab) = ln a + ln b ; ln(aᵇ) = b ln a ; log₁₀(10ⁿ) = n. eˣ et ln sont réciproques.\n• Électricité : la décharge d'un condensateur u(t) = U₀·e^(−t/τ) utilise l'exponentielle.", 'mot_entier': True}, {'titre': 'nombres complexes en electricite', 'cles': ['nombre complexe', 'j au carre', 'j2 egal moins 1', 'calcul avec les complexes', 'conjugue d un complexe', 'formule d euler', 'operateur j', 'conversion polaire rectangulaire', 'phaseur', 'representation de fresnel complexe'], 'reponse': 'Nombres complexes (en électricité, j² = −1, car i désigne le courant) :\n• Forme algébrique : z = a + j·b ; module |z| = √(a² + b²) ; argument φ = arctan(b/a) (attention au quadrant).\n• Forme polaire : z = [|z| ; φ] = |z|·(cos φ + j·sin φ) = |z|·e^(jφ).\n• Produit : modules multipliés, arguments additionnés. Quotient : modules divisés, arguments soustraits.\n• Impédances complexes : résistance Z_R = R ; bobine Z_L = j·L·ω ; condensateur Z_C = 1/(j·C·ω) = −j/(C·ω).\n• Exemple : R = 30 Ω en série avec X_L = 40 Ω ⇒ Z = 30 + j40 ⇒ |Z| = 50 Ω, φ = arctan(40/30) = 53,1° (le courant est en retard de 53,1° sur la tension).\n• Grandeur sinusoïdale ↔ vecteur de Fresnel ↔ nombre complexe : permet de résoudre les circuits RLC comme en continu.\n• Dériver en complexe = multiplier par jω ; intégrer = diviser par jω.', 'mot_entier': True}, {'titre': 'statistiques et probabilites', 'cles': ['statistiques', 'ecart type', 'variance', 'frequence cumulee', 'diagramme en barres', 'histogramme', 'probabilite', 'probabilites', 'calcul de probabilite', 'loi binomiale', 'combinaison', 'permutation', 'arrangement', 'denombrement', 'factorielle', 'dispersion', 'etendue', 'quartile', 'statistique descriptive', 'tableau statistique', 'moyenne ponderee', 'moyenne generale'], 'reponse': 'Statistiques et probabilités :\n• Moyenne : x̄ = Σ xᵢ / n. Moyenne pondérée : Σ (coef × note) / Σ coefs. Exemple : 12 (coef 3) et 15 (coef 2) ⇒ (36 + 30)/5 = 13,2.\n• Médiane : valeur centrale après classement (la moitié des valeurs est en dessous). Mode : valeur la plus fréquente. Étendue = max − min.\n• Variance V = Σ (xᵢ − x̄)² / n ; écart-type σ = √V (mesure la dispersion autour de la moyenne).\n• Probabilité : P(A) = nombre de cas favorables / nombre de cas possibles, toujours entre 0 et 1 ; P(non A) = 1 − P(A) ; P(A ou B) = P(A) + P(B) − P(A et B).\n• Dénombrement : n! = n × (n−1) × … × 1 ; arrangements A(n,p) = n!/(n−p)! (ordre compte) ; combinaisons C(n,p) = n!/(p!(n−p)!) (ordre ne compte pas).\n• Exemple : lancer un dé : P(obtenir 6) = 1/6.\n• Loi binomiale : probabilité de k succès en n essais indépendants de probabilité p : C(n,k)·pᵏ·(1−p)ⁿ⁻ᵏ.', 'mot_entier': True}, {'titre': 'mecanique physique forces et mouvement', 'cles': ['mecanique', 'physique', 'lois de newton', 'newton', 'premiere loi de newton', 'deuxieme loi de newton', 'principe fondamental de la dynamique', 'masse et poids', 'g 9 81', 'travail d une force', 'energie cinetique', 'energie potentielle', 'energie mecanique', 'conservation de l energie', 'puissance mecanique', 'mouvement rectiligne uniforme', 'chute libre', 'moment d une force', 'equilibre', 'levier', 'frottement', 'pression hydrostatique', 'poussee d archimede', 'archimede', 'densite', 'masse volumique', 'unites du systeme international', 'systeme international', 'unites si', 'conversion d unites', 'kilometre heure en metre seconde', 'km h en m s'], 'reponse': "Mécanique et physique de base :\n• Unités SI : mètre (m), kilogramme (kg), seconde (s), ampère (A), kelvin (K), mole (mol), candela (cd). Conversion : v (m/s) = v (km/h) / 3,6.\n• Newton : 1) sans force ou à forces équilibrées, un corps garde son mouvement ; 2) ΣF = m·a ; 3) action = − réaction.\n• Poids : P = m·g (g ≈ 9,81 N/kg sur Terre). Une masse de 50 kg a un poids de 490 N.\n• Vitesse moyenne v = d / t ; MRU : x = v·t ; MRUA : v = v₀ + a·t, x = x₀ + v₀·t + ½·a·t² ; chute libre : v = g·t, h = ½ g t².\n• Travail W = F · d · cos α (joules) ; puissance P = W/t (watts) = F·v ; énergie cinétique E_c = ½ m v² ; énergie potentielle E_p = m·g·h.\n• Moment d'une force : M = F × d (N·m) ; équilibre : somme des moments = 0. Couple d'un moteur : C = P/ω avec ω = 2π·n/60.\n• Pression : p = F/S (pascal, 1 bar = 10⁵ Pa) ; pression dans un liquide : p = ρ·g·h ; poussée d'Archimède = poids du volume de fluide déplacé.\n• Masse volumique : ρ = m/V (eau : 1 000 kg/m³).", 'mot_entier': True}, {'titre': 'thermodynamique et chaleur', 'cles': ['thermodynamique', 'quantite de chaleur', 'capacite thermique', 'chaleur massique', 'q m c delta t', 'calorimetrie', 'changement d etat', 'chaleur latente', 'dilatation', 'conduction convection rayonnement', 'transfert thermique', 'resistance thermique', 'isolant thermique', 'conductivite thermique', 'kelvin et celsius', 'conversion celsius kelvin', 'celsius fahrenheit', 'gaz parfait', 'pv nrt', 'loi des gaz parfaits', 'cycle frigorifique', 'climatiseur fonctionnement', 'refrigerateur fonctionnement', 'pompe a chaleur', 'compresseur frigorifique', 'fluide frigorigene', 'climatisation', 'split', 'puissance frigorifique', 'puissance de chauffe', 'chauffe eau electrique'], 'reponse': "Chaleur et thermodynamique :\n• Température : T(K) = T(°C) + 273,15 ; T(°F) = T(°C) × 9/5 + 32.\n• Quantité de chaleur : Q = m · c · ΔT ; c de l'eau = 4 186 J/(kg·K). Exemple : chauffer 100 L d'eau de 25 à 55 °C ⇒ Q = 100 × 4 186 × 30 ≈ 12,6 MJ ≈ 3,5 kWh ; avec un chauffe-eau de 2 kW (rendement ~1) : environ 1 h 45 min.\n• Changement d'état : fusion de la glace L = 334 kJ/kg ; vaporisation de l'eau L ≈ 2 260 kJ/kg.\n• Transferts : conduction (solides), convection (fluides), rayonnement (sans milieu).\n• Gaz parfait : P·V = n·R·T (R = 8,314 J/(mol·K)).\n• Effet Joule : W = R·I²·t (la chaleur dégagée par un conducteur).\n• Machine frigorifique (climatiseur, réfrigérateur) : cycle compression → condensation (chaleur évacuée) → détente → évaporation (chaleur absorbée) ; fluide frigorigène (R32, R410A, R600a…). COP = chaleur utile / énergie électrique consommée (souvent 3 à 4).\n• Climatisation : puissance frigorifique en kW (1 kW ≈ 3 412 BTU/h) ; un split de 12 000 BTU ≈ 3,5 kW ; surface ≈ 20 à 30 m² selon isolation.", 'mot_entier': True}, {'titre': 'optique ondes et son', 'cles': ['optique', 'vitesse de la lumiere', 'reflexion', 'refraction', 'loi de snell descartes', 'lentille', 'lentille convergente', 'lentille divergente', 'miroir', 'fibre optique', 'laser', 'longueur d onde', 'frequence et longueur d onde', 'onde electromagnetique', 'spectre electromagnetique', 'ondes radio', 'vitesse du son', 'decibel son', 'ultrasons', 'infrasons', 'effet doppler', 'mhz ghz', 'radio fm', 'antenne', 'propagation', 'rayons x', 'rayonnement', 'rayonnement infrarouge', 'ultraviolet'], 'reponse': "Optique, ondes et son :\n• Vitesse de la lumière dans le vide c = 3 × 10⁸ m/s. Relation onde : λ = v / f (longueur d'onde = vitesse / fréquence).\n• Spectre électromagnétique (fréquence croissante) : ondes radio, micro-ondes (Wi-Fi 2,4 GHz, téléphone), infrarouge (télécommande), lumière visible (400-700 nm), ultraviolet, rayons X, gamma. Exemple : radio FM à 100 MHz ⇒ λ = 3·10⁸/10⁸ = 3 m.\n• Réflexion : angle d'incidence = angle de réflexion. Réfraction (Snell-Descartes) : n₁·sin i₁ = n₂·sin i₂ (n eau ≈ 1,33 ; verre ≈ 1,5).\n• Fibre optique : guide la lumière par réflexion totale ; très hauts débits, insensible aux parasites électriques.\n• Lentilles : convergente (loupe) ; relation de conjugaison 1/OA' − 1/OA = 1/f'.\n• Son : onde mécanique ; vitesse ≈ 343 m/s dans l'air à 20 °C ; audible de 20 Hz à 20 kHz ; niveau sonore en dB (60 dB conversation, 85 dB seuil de protection auditive prolongée, 120 dB douleur).\n• Effet Doppler : variation apparente de fréquence quand la source se déplace.", 'mot_entier': True}, {'titre': 'chimie de base', 'cles': ['chimie', 'tableau periodique', 'tableau de mendeleiev', 'noyau', 'proton neutron electron', 'numero atomique', 'masse molaire', 'quantite de matiere', 'nombre d avogadro', 'molecule', 'liaison chimique', 'liaison covalente', 'liaison ionique', 'reaction chimique', 'equation chimique', 'equilibrer une equation', 'echelle de ph', 'solution acide', 'solution basique', 'oxydoreduction', 'oxydation', 'reduction', 'corrosion', 'rouille', 'electrolyse', 'pile electrochimique', 'electrode', 'concentration', 'molarite', 'dilution', 'solution aqueuse', 'etat de la matiere', 'corps pur', 'non metal', 'alliage', 'laiton', 'bronze', 'acier', 'fonte', 'galvanisation', 'electrolyte'], 'reponse': "Chimie de base :\n• Atome : noyau (protons +, neutrons) et électrons (−) ; numéro atomique Z = nombre de protons ; nombre de masse A = protons + neutrons. Tableau périodique : éléments rangés par Z croissant ; colonnes = familles (alcalins, halogènes, gaz rares).\n• Mole : n = m / M (m en g, M masse molaire en g/mol) ; N = n × 6,02·10²³. Exemple : 36 g d'eau (M = 18 g/mol) ⇒ n = 2 mol.\n• Concentration : C = n/V (mol/L) ; dilution : C₁V₁ = C₂V₂.\n• pH = −log[H₃O⁺] : < 7 acide ; = 7 neutre ; > 7 basique (eau pure : 7 ; jus de citron ≈ 2 ; eau de javel ≈ 12).\n• Oxydoréduction : oxydation = perte d'électrons ; réduction = gain d'électrons (OIL RIG). Corrosion (rouille du fer) = oxydation en présence d'eau et d'oxygène ; protection : peinture, galvanisation (zinc), anode sacrificielle.\n• Électrolyse : réaction chimique provoquée par un courant (électrolyse de l'eau : 2H₂O → 2H₂ + O₂). Pile : transforme l'énergie chimique en électricité (voir pile Daniell).\n• Alliages : laiton (Cu + Zn), bronze (Cu + Sn), acier (Fe + C < 2 %), fonte (Fe + C > 2 %).", 'mot_entier': True}, {'titre': 'materiaux et resistance des materiaux', 'cles': ['materiaux', 'proprietes des materiaux', 'conducteur isolant semi conducteur', 'cuivre', 'aluminium', 'resistivite des materiaux', 'conductivite', 'r rho l s', 'calcul resistance d un conducteur', 'isolant electrique', 'rigidite dielectrique', 'polyethylene', 'caoutchouc', 'ceramique', 'semi conducteurs', 'silicium', 'germanium', 'dopage', 'supraconducteur', 'dilatation des materiaux', 'resistance des materiaux', 'contrainte', 'traction', 'compression', 'cisaillement', 'limite elastique', 'module de young', 'coefficient de securite', 'flexion'], 'reponse': "Matériaux :\n• Conducteurs : cuivre (ρ = 1,7·10⁻⁸ Ω·m), aluminium (2,8·10⁻⁸), argent (le meilleur), or (résiste à l'oxydation, contacts). Résistance d'un fil : R = ρ·L / S (L en m, S en m², ou ρ = 0,017 Ω·mm²/m avec S en mm²). Exemple : 100 m de cuivre 2,5 mm² ⇒ R = 0,017 × 100 / 2,5 = 0,68 Ω.\n• Isolants : PVC, polyéthylène réticulé (PR), caoutchouc, papier, mica, verre, céramique ; caractérisés par leur rigidité diélectrique (kV/mm).\n• Semi-conducteurs : silicium, germanium ; leur conductivité varie par dopage (type N : excès d'électrons ; type P : excès de trous) ; la jonction PN donne la diode.\n• Résistance des matériaux : contrainte σ = F/S (en Pa ou MPa) ; limite élastique = contrainte au-delà de laquelle la déformation reste ; coefficient de sécurité s = résistance / contrainte réelle (≥ 2 à 4 selon l'usage). Module de Young E : σ = E × déformation relative. Efforts : traction, compression, cisaillement, flexion, torsion.", 'mot_entier': True}, {'titre': 'dessin technique et cotation', 'cles': ['dessin technique', 'dessin industriel', 'cotation', 'cotes', 'projection orthogonale', 'vue de face vue de dessus', 'vue de gauche', 'coupe et section', 'echelle d un plan', 'lecture de plan', 'plan de masse', 'plan de situation', 'plan d architecte', 'normes de dessin', 'format a4 a3', 'cartouche', 'cartouche de dessin', 'traits normalises', 'trait fort', 'trait mixte', 'trait interrompu', 'perspective', 'perspective cavaliere', 'isometrique', 'tolerance dimensionnelle', 'ajustement', 'etat de surface', 'filetage', 'taraudage', 'vis et boulon', 'vis m8', 'pas du filetage', 'assemblage demontable', 'soudure assemblage', 'autocad', 'solidworks', 'freecad', 'sketchup', 'dessin assiste par ordinateur'], 'reponse': "Dessin technique :\n• Formats : A4 (210 × 297 mm), A3, A2, A1, A0 ; cartouche en bas à droite (titre, échelle, date, auteur, matière).\n• Projection orthogonale : vue de face, vue de dessus (sous la face), vue de gauche (à droite de la face) ; on ne garde que les vues nécessaires.\n• Traits : continu fort (contours visibles), interrompu fin (contours cachés), mixte fin (axes), continu fin (cotes, hachures).\n• Coupes : montrent l'intérieur ; hachures à 45°.\n• Échelle : 1:2 réduit de moitié ; 2:1 agrandi ; 1:1 grandeur nature. Longueur réelle = longueur dessinée × dénominateur/numérateur. Exemple : 3 cm au dessin à 1:50 ⇒ 1,5 m réels.\n• Cotation : ligne de cote, flèches, valeur en mm, ne jamais coter deux fois la même chose ; tolérances et ajustements (ex. H7/g6).\n• Filetage : M8 = diamètre 8 mm, pas normal 1,25 mm ; taraudage = filetage intérieur.\n• Logiciels de DAO/CAO : AutoCAD, SolidWorks, FreeCAD (libre), SketchUp, LibreCAD (2D libre).", 'mot_entier': True}, {'titre': 'metrologie et unites electriques', 'cles': ['metrologie', 'precision et exactitude', 'erreur de mesure', 'incertitude', 'erreur absolue', 'erreur relative', 'classe de precision', 'calibre et classe', 'calibre d un appareil', 'chiffres significatifs', 'multiples et sous multiples', 'prefixes', 'prefixes si', 'kilo mega giga', 'milli micro nano', 'unites electriques', 'ampere volt ohm watt', 'farad henry', 'coulomb', 'joule', 'hertz', 'conversion kwh en joule', 'conversion wh kwh', 'kilowattheure', 'calcul de la facture d electricite', 'energie electrique', 'cout de l energie', 'compteur electrique', 'puissance et energie', 'consommation d un appareil', 'energie consommee', 'tarif sbee'], 'reponse': "Unités et métrologie :\n• Unités : tension V (volt), courant A (ampère), résistance Ω (ohm), puissance W (watt), énergie J (joule) ou Wh/kWh, fréquence Hz, capacité F (farad), inductance H (henry), charge C (coulomb), flux magnétique Wb, induction magnétique T (tesla), conductance S (siemens).\n• Préfixes : tera T 10¹², giga G 10⁹, méga M 10⁶, kilo k 10³, milli m 10⁻³, micro µ 10⁻⁶, nano n 10⁻⁹, pico p 10⁻¹².\n• Énergie : E = P × t. 1 kWh = 1 000 W pendant 1 h = 3,6 MJ. Exemple : un fer à repasser de 1 200 W utilisé 2 h consomme 2,4 kWh ; si le kWh coûte 100 FCFA (valeur indicative, vérifier le tarif SBEE en vigueur) ⇒ 240 FCFA.\n• Erreur absolue = valeur mesurée − valeur vraie ; erreur relative = erreur absolue / valeur vraie. Classe de précision d'un appareil (ex. 1,5) : erreur maximale = classe × calibre / 100 ⇒ pour un voltmètre classe 1,5 de calibre 300 V : ± 4,5 V.\n• Bonne pratique : choisir un calibre proche de la valeur à mesurer (le tiers supérieur du cadran), répéter la mesure.", 'mot_entier': True}, {'titre': 'informatique de base', 'cles': ['informatique', 'c est quoi un ordinateur', 'composants d un ordinateur', 'processeur', 'ram et rom', 'memoire vive', 'disque dur', 'carte mere', 'carte graphique', 'systeme d exploitation', 'windows', 'linux', 'android', 'hardware', 'software', 'fichier et dossier', 'extension de fichier', 'virus informatique', 'antivirus', 'sauvegarde', 'stockage cloud', 'compression de fichiers', 'bureautique', 'word excel powerpoint', 'excel formules', 'formule excel', 'somme excel', 'tableur', 'raccourcis clavier', 'ctrl c ctrl v', 'raccourci windows', 'reparer un ordinateur lent', 'ordinateur lent'], 'reponse': 'Informatique de base :\n• Ordinateur = matériel (hardware) + logiciels (software). Composants : processeur (CPU, cerveau), RAM (mémoire vive, temporaire), stockage (disque dur HDD, SSD plus rapide), carte mère, alimentation, carte graphique (GPU), périphériques (clavier, écran, imprimante).\n• Système d\'exploitation : Windows, Linux, macOS, Android (téléphones), iOS. Il gère le matériel et les logiciels.\n• Unités : octet = 8 bits ; Ko, Mo, Go, To (×1 024 en usage courant).\n• Raccourcis utiles : Ctrl+C copier, Ctrl+X couper, Ctrl+V coller, Ctrl+Z annuler, Ctrl+S enregistrer, Ctrl+F rechercher, Alt+Tab changer de fenêtre.\n• Excel : =SOMME(A1:A10), =MOYENNE(A1:A10), =SI(A1>10;"admis";"refusé"), =RECHERCHEV(...), formules commencent par « = » ; références absolues $A$1.\n• Ordinateur lent : trop de programmes au démarrage, disque presque plein, peu de RAM, virus, chaleur (poussière) ; solutions : nettoyer, désinstaller, passer à un SSD, ajouter de la RAM, réinstaller.\n• Sauvegarde : règle 3-2-1 (3 copies, 2 supports, 1 hors site/cloud). Antivirus et mises à jour à jour.', 'mot_entier': True}, {'titre': 'internet et reseaux', 'cles': ['internet', 'reseau informatique', 'reseaux informatiques', 'c est quoi internet', 'adresse ip', 'ip v4', 'dns', 'http et https', 'https', 'navigateur', 'client serveur', 'wifi', 'wi fi', 'routeur', 'box internet', 'modem', 'switch', 'lan wan', 'reseau local', 'masque de sous reseau', 'passerelle', 'tcp ip', 'modele osi', 'cable ethernet', 'rj45', 'debit internet', '4g 5g', 'fibre optique internet', 'vpn', 'pare feu', 'firewall', 'reseau gsm', 'donnees mobiles', 'hotspot', 'partage de connexion'], 'reponse': "Internet et réseaux :\n• Internet = réseau mondial de réseaux. Le Web (pages, sites) est un service qui circule sur Internet, comme le mail.\n• Adresse IP : identifie un appareil. IPv4 : 4 nombres de 0 à 255 (ex. 192.168.1.10). Réseau local souvent 192.168.x.x (adresses privées). DNS : « annuaire » qui traduit un nom de site en adresse IP. HTTP/HTTPS : protocole du web (HTTPS = chiffré).\n• Matériel : routeur (relie le réseau local à Internet), switch (relie des appareils du réseau local), point d'accès Wi-Fi, câble Ethernet (RJ45, catégories 5e/6).\n• Masque de sous-réseau (ex. 255.255.255.0 = /24 → 254 appareils). DHCP donne automatiquement les adresses.\n• Modèle OSI en 7 couches ; TCP/IP en 4 couches.\n• Débit : en Mb/s (mégabits par seconde) ; 8 bits = 1 octet, donc 80 Mb/s ≈ 10 Mo/s max.\n• Wi-Fi : 2,4 GHz (portée plus grande) et 5 GHz (plus rapide, portée moindre).\n• Mobile : 3G, 4G (LTE), 5G.\n• Sécurité : mot de passe fort, WPA2/WPA3, pare-feu, VPN pour chiffrer la connexion, ne pas cliquer sur des liens suspects.", 'mot_entier': True}, {'titre': 'programmation et python', 'cles': ['programmation', 'programmer', 'apprendre a programmer', 'python', 'langage python', 'algorithme', 'variable', 'boucle for', 'boucle while', 'condition if', 'fonction python', 'liste python', 'dictionnaire python', 'print python', 'input python', 'syntaxe python', 'exemple de code python', 'code python', 'html', 'css', 'javascript', 'langage html', 'balises html', 'page web', 'site web', 'creer un site web', 'langage c', 'c plus plus', 'sql', 'base de donnees', 'requete sql', 'github', 'debogage', 'langage de programmation', 'algorithmique', 'pseudo code', 'organigramme', 'ordinogramme'], 'reponse': 'Programmation :\n• Algorithme : suite d\'instructions pour résoudre un problème ; on l\'écrit en pseudo-code ou en organigramme, puis en langage.\n• Python (facile et très répandu) :\nnom = input("Ton nom ? ")\nif len(nom) > 0:\n    print("Bonjour", nom)\nfor i in range(5):\n    print(i)   # boucle : 0 à 4\ndef carre(x):\n    return x * x\nmesures = [12, 15, 9]; moyenne = sum(mesures) / len(mesures)\n• Notions : variables, types (int, float, str, bool), conditions (if/elif/else), boucles (for, while), fonctions, listes, dictionnaires, fichiers, bibliothèques (math, random, tkinter pour les interfaces — comme Novi sur Pydroid).\n• Web : HTML (structure : <h1>, <p>, <img>, <a>), CSS (style, couleurs, mise en page), JavaScript (comportement). Un site = fichiers HTML/CSS/JS ; GitHub Pages permet de l\'héberger gratuitement (comme ce site Novi).\n• Bases de données : SQL (SELECT * FROM table WHERE age > 18).\n• Débogage : lire le message d\'erreur, afficher les variables (print), tester par petites étapes.\n• Git/GitHub : sauvegarder et partager le code avec historique.', 'mot_entier': True}, {'titre': 'intelligence artificielle et cybersecurite', 'cles': ['intelligence artificielle', 'c est quoi l ia', 'comment fonctionne une ia', 'machine learning', 'apprentissage automatique', 'deep learning', 'reseau de neurones', 'chatgpt', 'claude', 'gemini', 'modele de langage', 'ia generative', 'prompt', 'comment bien ecrire un prompt', 'cybersecurite', 'piratage', 'phishing', 'hameconnage', 'mot de passe fort', 'authentification a deux facteurs', 'arnaque en ligne', 'arnaque mobile money', 'faux sms', 'ingenierie sociale', 'ransomware', 'securite informatique', 'vie privee en ligne', 'reseaux sociaux securite'], 'reponse': "Intelligence artificielle et cybersécurité :\n• IA : programmes capables de réaliser des tâches qui demandent de l'« intelligence » (comprendre, traduire, reconnaître des images, discuter). Machine learning : la machine apprend à partir d'exemples. Deep learning : réseaux de neurones à plusieurs couches. LLM (grands modèles de langage) : prédisent la suite d'un texte après avoir appris sur d'énormes corpus ; ils peuvent se tromper, donc vérifier les informations importantes.\n• Un bon prompt : donner le contexte, la tâche précise, le format voulu, des exemples.\n• Novi IA fonctionne localement avec une base de fiches ; un « vrai cerveau » en ligne demanderait une clé d'API gardée sur un serveur sécurisé.\n• Cybersécurité de base : mot de passe long et différent pour chaque compte (gestionnaire de mots de passe) ; double authentification ; ne jamais donner son code de validation (SMS, Mobile Money) ni son code PIN ; méfiance envers les faux gains, les liens raccourcis, les « agents » qui demandent un transfert ; hameçonnage (phishing) = faux message pour voler des identifiants ; mises à jour à jour ; sauvegardes contre les rançongiciels.\n• Arnaque Mobile Money : personne de sérieux ne te demande ton code secret ; en cas de doute, appelle le service officiel de ton opérateur.", 'mot_entier': True}, {'titre': 'histoire de l afrique', 'cles': ['histoire de l afrique', 'afrique histoire', 'empire du ghana', 'empire du mali', 'empire songhai', 'mansa moussa', 'sundiata keita', 'soundiata keita', 'tombouctou', 'royaume ashanti', 'royaume du benin nigeria', 'royaume du kongo', 'empire zoulou', 'zimbabwe', 'aksoum', 'egypte antique', 'carthage', 'traite negriere', 'traite transatlantique', 'abolition de l esclavage', 'colonisation de l afrique', 'conference de berlin', 'partage de l afrique', 'resistance africaine', 'samory toure', 'toussaint louverture', 'decolonisation', 'independance des pays africains', 'annee de l afrique 1960', 'panafricanisme', 'nkrumah', 'kwame nkrumah', 'sekou toure', 'thomas sankara', 'nelson mandela', 'apartheid', 'union africaine', 'cedeao', 'uemoa', 'alliance des etats du sahel', 'zlecaf', 'zone de libre echange'], 'reponse': "Histoire de l'Afrique (repères) :\n• Antiquité : Égypte pharaonique, Carthage, royaume de Koush, Aksoum.\n• Empires d'Afrique de l'Ouest : Ghana (IVe-XIe siècle), Mali (XIIIe-XVe, fondé par Soundiata Keïta ; Mansa Moussa, pèlerinage en 1324, Tombouctou centre de savoir), Songhaï (XVe-XVIe, Gao ; Askia Mohamed).\n• Royaumes forestiers : Ashanti, Oyo, Bénin (Nigeria), Dahomey, Kongo ; au sud : Grand Zimbabwe, empire zoulou.\n• Traite négrière : traite transatlantique (XVIe-XIXe siècles), des millions d'Africains déportés ; abolition de l'esclavage en France en 1848 (première abolition 1794).\n• Conférence de Berlin (1884-1885) : règles du partage de l'Afrique entre puissances européennes. Résistances : Samory Touré, Béhanzin, Chaka, Menelik II (victoire d'Adoua en 1896).\n• Indépendances : Ghana en 1957 (Nkrumah), puis 1960 « année de l'Afrique » (17 pays dont le Dahomey), Algérie 1962, colonies portugaises 1975, Namibie 1990, fin de l'apartheid en Afrique du Sud en 1994 (Mandela président).\n• Organisations : Organisation de l'unité africaine (1963) devenue Union africaine (2002) ; CEDEAO (1975, Afrique de l'Ouest) ; UEMOA (1994, monnaie commune FCFA) ; ZLECAf (libre-échange continental, en vigueur depuis 2021).\n• Figures : Nkrumah, Sékou Touré, Patrice Lumumba, Thomas Sankara, Nelson Mandela, Cheikh Anta Diop.", 'mot_entier': True}, {'titre': 'histoire du monde', 'cles': ['histoire du monde', 'histoire mondiale', 'premiere guerre mondiale', 'deuxieme guerre mondiale', 'seconde guerre mondiale', '1914 1918', '1939 1945', 'guerre froide', 'chute du mur de berlin', 'revolution francaise', 'revolution industrielle', 'revolution russe', 'colonisation', 'nations unies', 'creation de l onu', 'declaration universelle des droits de l homme', 'union europeenne', 'brics', 'banque mondiale', 'unesco', 'unicef', 'hiroshima', 'bombe atomique', 'apollo 11', 'premier homme sur la lune', 'invention de l electricite', 'histoire de l electricite', 'volta', 'ampere', 'nikola tesla', 'thomas edison', 'joule histoire', 'histoire de l informatique', 'internet histoire', 'grandes inventions'], 'reponse': "Histoire du monde (repères) :\n• 1789 : Révolution française, Déclaration des droits de l'homme et du citoyen. XVIIIe-XIXe : révolution industrielle (machine à vapeur, électricité, chemin de fer).\n• 1914-1918 : Première Guerre mondiale. 1939-1945 : Seconde Guerre mondiale (Shoah, Hiroshima et Nagasaki en août 1945).\n• 1945 : création de l'ONU. 1948 : Déclaration universelle des droits de l'homme. Guerre froide (États-Unis/URSS) jusqu'en 1989-1991 (chute du mur de Berlin 1989, fin de l'URSS 1991).\n• Organisations : UE, OTAN, FMI, Banque mondiale, OMC, OMS, UNESCO, UNICEF ; groupes : G7, G20, BRICS.\n• 1969 : Apollo 11, premier pas sur la Lune (Neil Armstrong). 1991 : naissance du Web.\n• Histoire de l'électricité : Volta (pile, 1800), Ampère (électromagnétisme, 1820), Ohm (loi, 1827), Faraday (induction, 1831), Joule (effet, 1840), Maxwell (équations, 1860), Edison (lampe à incandescence, 1879, et premières centrales), Tesla et Westinghouse (courant alternatif, moteur asynchrone, 1880-90), Marconi (radio), Bardeen-Brattain-Shockley (transistor, 1947).\n• Unités électriques portant le nom de ces savants : volt, ampère, ohm, joule, watt, farad, henry, tesla, hertz, coulomb.", 'mot_entier': True}, {'titre': 'geographie du monde', 'cles': ['geographie du monde', 'capitales du monde', 'continents', 'les continents', 'oceans', 'les oceans', 'plus grand pays du monde', 'plus long fleuve', 'amazone', 'plus haute montagne', 'everest', 'kilimandjaro', 'sahara', 'desert du sahara', 'pays d afrique', 'combien de pays en afrique', 'capitales d afrique', 'capitale du togo', 'capitale du nigeria', 'capitale du ghana', 'capitale du niger', 'capitale du burkina faso', 'capitale de la cote d ivoire', 'capitale du senegal', 'capitale du mali', 'capitale de la france', 'capitale des etats unis', 'capitale de la chine', 'capitale du canada', 'capitale du maroc', 'population mondiale', 'fuseaux horaires', 'latitude et longitude', 'equateur', 'tropiques', 'hemisphere', 'points cardinaux', 'boussole'], 'reponse': "Géographie du monde :\n• 5 continents habités selon l'usage courant (ou 6/7 selon la convention) : Afrique, Amérique, Asie, Europe, Océanie (+ Antarctique). 5 océans : Atlantique, Pacifique, Indien, Arctique, Austral. La Terre : environ 8 milliards d'habitants.\n• Afrique : 54 pays reconnus par l'ONU ; plus grand pays : Algérie ; plus peuplé : Nigeria ; plus long fleuve : le Nil (≈ 6 650 km) ; plus haute montagne : le Kilimandjaro (5 895 m, Tanzanie) ; grand désert : le Sahara.\n• Monde : plus grand pays : Russie ; plus peuplés : Inde et Chine ; plus haute montagne : Everest (8 849 m environ) ; plus grand océan : Pacifique.\n• Capitales proches du Bénin : Lomé (Togo), Abuja (Nigeria), Accra (Ghana), Niamey (Niger), Ouagadougou (Burkina Faso), Yamoussoukro (Côte d'Ivoire, capitale politique ; Abidjan est la capitale économique), Dakar (Sénégal), Bamako (Mali). Autres : Paris (France), Washington (États-Unis), Pékin (Chine), Ottawa (Canada), Rabat (Maroc), Londres (Royaume-Uni), Brasília (Brésil).\n• Repérage : latitude (nord/sud de l'équateur, 0° à 90°) et longitude (est/ouest du méridien de Greenwich, 0° à 180°). Le Bénin est à environ 6° à 12° N, 1° à 4° E, donc dans le fuseau UTC+1.\n• Décalage horaire : 15° de longitude = 1 heure.", 'mot_entier': True}, {'titre': 'economie et gestion simple', 'cles': ['economie generale', 'offre et demande', 'pret bancaire', 'interet simple', 'interets composes', 'taux d interet', 'epargne', 'gerer son argent', 'gestion du budget', 'comptabilite', 'bilan comptable', 'compte de resultat', 'chiffre d affaires', 'cout de revient', 'prix de vente', 'marge commerciale', 'seuil de rentabilite', 'bon de commande', 'actif passif', 'amortissement', 'salaire brut net', 'mobile money', 'moov money', 'mtn momo', 'celtiis cash', 'transfert d argent'], 'reponse': "Économie et gestion simple :\n• PIB : valeur de tout ce qui est produit dans un pays sur une période. Inflation : hausse générale et durable des prix. Offre et demande : plus un produit est demandé et rare, plus son prix monte.\n• Calculs de base : prix de vente = coût de revient + marge ; marge = prix de vente − coût de revient ; taux de marge = marge / coût de revient ; chiffre d'affaires = quantité × prix ; bénéfice = CA − charges.\n• Seuil de rentabilité (nombre d'unités à vendre) = charges fixes / (prix de vente unitaire − coût variable unitaire).\n• Intérêts simples : I = C × t × n ; intérêts composés : C_n = C₀ × (1 + t)ⁿ. Exemple : 100 000 FCFA à 5 % pendant 3 ans ⇒ 115 763 FCFA (composé).\n• Budget : écrire revenus et dépenses ; règle utile 50 % besoins, 30 % envies, 20 % épargne (à adapter) ; garder une épargne de sécurité.\n• Devis (travaux d'électricité) : liste des fournitures (câbles, disjoncteurs, prises, tableau), main-d'œuvre, déplacement, marge, TVA si applicable, durée de validité ; facture après travaux.\n• Mobile Money : service de transfert et de paiement par téléphone (MTN MoMo, Moov Money, Celtiis Cash…) ; garde ton code secret et vérifie le numéro avant d'envoyer.", 'mot_entier': True}, {'titre': 'creer son entreprise au benin', 'cles': ['creer une entreprise au benin', 'creation d entreprise', 'creer son entreprise', 'entreprise individuelle', 'societe anonyme', 'statut juridique', 'identifiant fiscal unique', 'registre du commerce', 'guichet unique', 'apiex', 'apiex benin', 'formalites de creation', 'entreprise electrique', 'ouvrir un atelier', 'ouvrir une boutique', 'devenir entrepreneur', 'auto entrepreneur', 'entreprenariat', 'financement jeune entrepreneur', 'pret pour jeune', 'business plan', 'plan d affaires', 'etude de marche', 'ouvrir un cabinet', 's installer a son compte electricien', 'travailler a son compte'], 'reponse': "Créer son entreprise au Bénin (infos générales, à vérifier auprès des services officiels) :\n• Formes courantes : entreprise individuelle (la plus simple), SARL (société à responsabilité limitée, plusieurs associés ou un seul), SA.\n• Formalités : la création peut se faire via le guichet unique des formalités des entreprises (rattaché à l'APIEx à Cotonou) ; documents habituels : pièce d'identité, justificatif de domicile, statuts (pour les sociétés), localisation du siège. Obtention du RCCM (registre du commerce) et de l'IFU (identifiant fiscal unique).\n• Après création : déclarations fiscales (impôts, TVA si applicable), comptes bancaires, éventuellement affiliation à la CNSS pour les employés.\n• Pour un électricien/électrotechnicien : définir tes services (installation, dépannage, maintenance, solaire), acheter l'outillage minimum (multimètre, pince, perceuse, outils isolés, EPI), faire un devis clair, prévoir une assurance si possible, tenir un cahier de clients et de factures.\n• Business plan : marché visé, services et prix, coûts fixes, financement, prévisions sur 12 mois.\n• Financement : épargne, tontine, microfinance, programmes d'appui à l'entrepreneuriat des jeunes (se renseigner auprès des structures publiques et ONG).\n• Les règles changent : confirme sur apiex.bj ou auprès du guichet unique.", 'mot_entier': True}, {'titre': 'emploi cv et lettre de motivation', 'cles': ['cv', 'curriculum vitae', 'faire un cv', 'modele de cv', 'cv electricien', 'cv technicien', 'lettre de motivation', 'modele de lettre de motivation', 'ecrire une lettre de motivation', 'lettre de candidature', 'candidature spontanee', 'entretien d embauche', 'entretien d embauche questions', 'reussir un entretien', 'chercher un emploi', 'trouver un emploi', 'trouver un stage', 'rapport de stage', 'plan du rapport de stage', 'lettre de demande de stage', 'lettre de demande d emploi', 'demande d emploi', 'lettre de demission', 'demande de stage', 'competences electrotechnicien', 'qualites professionnelles'], 'reponse': "CV, lettre et entretien :\n• CV (1 page) : en-tête (nom, téléphone, e-mail, ville) ; titre (ex. « Technicien en électrotechnique – F3 ») ; profil en 2 lignes ; expériences et stages (du plus récent au plus ancien, avec réalisations chiffrées) ; formation (diplômes, établissements, années) ; compétences (installation électrique, maintenance, lecture de schémas, câblage, solaire, automates, AutoCAD…) ; langues ; centres d'intérêt ; habilitations/permis. Photo facultative.\n• Lettre de motivation : ① qui tu es et quel poste ; ② pourquoi cette entreprise (se renseigner) ; ③ ce que tu apportes avec exemples précis ; ④ formule de politesse et disponibilité pour un entretien. Une page maximum, sans faute.\n• Rapport de stage : page de garde, remerciements, sommaire, introduction, présentation de l'entreprise, travaux effectués (avec schémas/photos), difficultés et solutions, apports personnels, conclusion, annexes.\n• Entretien : arriver à l'heure, tenue propre, se présenter en 1 minute, connaître l'entreprise, donner des exemples concrets (« j'ai dépanné un moteur qui… »), poser une ou deux questions, rester honnête.\n• Pistes d'emploi : entreprises d'installation et de maintenance, SBEE et CEB, industries, hôtels et hôpitaux (maintenance), énergie solaire, enseignement technique, travail à son compte.", 'mot_entier': True}, {'titre': 'orientation et etudes', 'cles': ['orientation', 'orientation scolaire', 'choisir mon orientation', 'apres le bac f3', 'apres le bep', 'apres le dt', 'dt diplome de technicien', 'licence professionnelle', 'licence pro', 'master', 'ingenieur', 'ecole d ingenieur', 'etudier a l etranger', 'bourse d etudes', 'campus france', 'chine bourse', 'bourse du gouvernement', 'bourse excellence', 'test d entree', 'concours', 'concours de la fonction publique', 'fonction publique', 'concours enseignants', 'ecoles techniques', 'cycle d ingenieur', 'etudes d electrotechnique', 'etudes d electronique', 'etudes en maintenance industrielle', 'formation en maintenance', 'formation en electricite', 'apprendre l electricite', 'formation en ligne gratuite', 'coursera', 'youtube pour apprendre', 'cours en ligne'], 'reponse': "Orientation et études (pistes générales, à confirmer auprès des établissements) :\n• Après un bac F3 ou un DT (Diplôme de Technicien) en électrotechnique : BTS ou licence professionnelle (électrotechnique, maintenance des systèmes industriels, génie électrique, énergies renouvelables, automatisme), puis licence/master ou école d'ingénieurs (poursuite possible par concours ou sur dossier).\n• Au Bénin : universités publiques (UAC, UP, UNSTIM) et instituts techniques ; écoles supérieures privées ; centres de formation professionnelle ; apprentissage chez un artisan (formation duale).\n• Bourses : bourses d'excellence et bourses de gouvernements étrangers (annoncées par le ministère et les ambassades) ; programmes d'échange ; dates limites à respecter, dossier complet, relevés de notes traduits si besoin.\n• Concours : préparer annales, culture générale, maths/physique, rédaction.\n• Apprendre en autonomie : cours en ligne gratuits (Khan Academy, OpenClassrooms, YouTube, MOOC), tutoriels Arduino/PLC, simulateurs (Tinkercad, Proteus, LTspice), pratique sur chantier.\n• Conseils : se spécialiser (maintenance industrielle, solaire, automates), obtenir des certifications (habilitation électrique, soudure, API), apprendre l'anglais technique et la bureautique, construire un portfolio de projets.", 'mot_entier': True}, {'titre': 'sante et premiers secours', 'cles': ['premiers secours', 'secourisme', 'gestes de premiers secours', 'massage cardiaque', 'reanimation cardio pulmonaire', 'position laterale de securite', 'brulure', 'que faire en cas de brulure', 'saignement', 'hemorragie', 'plaie', 'fracture', 'entorse', 'malaise', 'insolation', 'coup de chaleur', 'deshydratation', 'reidratation', 'rehydratation orale', 'paludisme', 'symptomes du paludisme', 'diarrhee', 'cholera', 'typhoide', 'moustiquaire', 'se laver les mains', 'eau potable', 'covid', 'piqure de serpent', 'morsure de serpent', 'numero d urgence benin', 'urgence medicale', 'pompiers benin', 'centre de sante'], 'reponse': "Santé de base et premiers secours (informations générales, ne remplacent pas un médecin) :\n• Urgence : prévenir les secours ; au Bénin, les numéros de secours courants incluent le 118 (pompiers/protection civile) et le 112 (urgences) — vérifie les numéros en vigueur dans ta région.\n• Arrêt cardiaque (personne inconsciente qui ne respire pas) : appeler les secours, commencer le massage cardiaque (paumes au centre de la poitrine, 100 à 120 compressions/min, enfoncement d'environ 5 cm chez l'adulte).\n• Inconsciente mais qui respire : position latérale de sécurité.\n• Saignement : compresser fermement avec un linge propre. Brûlure : refroidir à l'eau tiède courante 10 à 15 minutes, ne pas percer les cloques, pas de pommade ni de produit traditionnel sur une brûlure grave.\n• Électrisation : couper le courant avant de toucher la victime.\n• Paludisme : fièvre, frissons, maux de tête, courbatures ; en zone d'endémie, faire un test rapide (TDR) au centre de santé et suivre le traitement prescrit ; prévention : moustiquaire imprégnée, supprimer les eaux stagnantes. Ne pas s'auto-médiquer sans avis, surtout les enfants et les femmes enceintes.\n• Diarrhée : boire beaucoup, solution de réhydratation orale (SRO) ; consulter si sang, fièvre élevée ou chez un enfant.\n• Hygiène : se laver les mains au savon, eau potable (filtrée/traitée), aliments bien cuits.\n• Morsure de serpent : garder le calme, immobiliser le membre, aller immédiatement au centre de santé ; pas de garrot serré ni d'incisions.", 'mot_entier': True}, {'titre': 'droit et demarches administratives au benin', 'cles': ['droit du travail', 'code du travail', 'contrat de travail', 'cdi cdd', 'licenciement', 'salaire minimum', 'smig benin', 'conges payes', 'cnss benin', 'securite sociale', 'retraite', 'acte de naissance', 'extrait d acte de naissance', 'carte d identite', 'carte d identite nationale', 'certificat de personnalite', 'passeport', 'passeport benin', 'permis de conduire', 'demande de permis', 'carte grise', 'casier judiciaire', 'certificat de nationalite', 'certificat de residence', 'legalisation', 'legaliser un document', 'acte de mariage', 'heritage', 'succession', 'titre foncier', 'parcelle', 'attestation d heritage', 'gendarmerie', 'droits de l homme', 'droits et devoirs', 'constitution'], 'reponse': "Droit et démarches courantes au Bénin (informations générales — les procédures et frais évoluent ; confirme auprès de la mairie, de l'ANIP ou du ministère concerné) :\n• Pièces d'état civil : acte de naissance (déclaré à la mairie/centre d'état civil), extrait à demander à la mairie du lieu de naissance, acte de mariage, acte de décès.\n• Identité : l'ANIP (Agence nationale d'identification des personnes) gère le RAVIP et délivre le numéro CIP (certificat d'identification personnelle) et la carte d'identité biométrique.\n• Passeport : demande via les services de la Police/de l'immigration (pré-inscription en ligne et rendez-vous, selon la procédure en vigueur).\n• Casier judiciaire : demandé au tribunal de première instance ; certificat de nationalité : tribunal.\n• Travail : le Code du travail encadre les contrats (CDI, CDD), la durée du travail, les congés, le licenciement ; l'employeur doit déclarer ses salariés à la CNSS (sécurité sociale : retraite, prestations familiales, accidents du travail). Un contrat écrit protège les deux parties.\n• Terrains : un acte (convention, titre) doit être sécurisé ; vérifier la propriété auprès de l'ANDF (Agence nationale du domaine et du foncier) avant tout achat et utiliser un notaire.\n• Conseils : garder des copies de tous les papiers, demander un reçu, se méfier des intermédiaires qui promettent d'aller vite contre de l'argent. Pour un cas précis, consulte un avocat ou un huissier.", 'mot_entier': True}, {'titre': 'methodes de travail et d etude', 'cles': ['methode de travail', 'comment reviser', 'reviser efficacement', 'apprendre vite', 'techniques de memorisation', 'fiche de revision', 'organiser son temps', 'gestion du temps', 'planning de revision', 'motivation', 'stress des examens', 'gerer le stress', 'preparer un examen', 'reussir au bac', 'reussir ses etudes', 'prendre des notes', 'lire efficacement', 'comprendre un cours', 'methode feynman', 'technique pomodoro', 'pomodoro', 'sommeil et etude', 'discipline', 'objectifs smart', 'fixer des objectifs', 'confiance en soi', 'prise de parole', 'parler en public', 'expose oral', 'presentation orale'], 'reponse': "Méthodes de travail et d'étude :\n• Réviser efficacement : relire en actif (se poser des questions), faire des fiches courtes, refaire les exercices sans regarder la correction, espacer les révisions (répétition espacée : J+1, J+3, J+7, J+21).\n• Pomodoro : 25 min de travail concentré, 5 min de pause, 4 cycles puis pause longue.\n• Méthode Feynman : expliquer le sujet à quelqu'un de simple ; les trous dans l'explication montrent ce qu'il faut revoir.\n• Planning : liste de priorités, créneaux fixes, matières difficiles aux heures où l'on est en forme ; le sommeil (7 à 8 h) consolide la mémoire.\n• Examen : lire tout le sujet, repérer les questions faciles, gérer le temps, justifier les calculs avec unités, relire.\n• Stress : respiration lente, préparation en avance, activité physique ; en parler à un proche.\n• Objectifs SMART : Spécifiques, Mesurables, Atteignables, Réalistes, Temporels (ex. « finir les 5 exercices de triphasé avant vendredi »).\n• Prise de parole : plan clair (introduction, 2-3 idées, conclusion), parler lentement, regarder le public, s'entraîner à voix haute.\n• Pour tes projets (comme Novi), avancer par petits livrables testés plutôt que tout d'un coup.", 'mot_entier': True}, {'titre': 'vocabulaire electrique en anglais', 'cles': ['vocabulaire electrique en anglais', 'electricite en anglais', 'termes electriques en anglais', 'mots anglais electricite', 'electrical terms', 'electrical vocabulary', 'english for electricians', 'anglais technique', 'anglais technique electricite', 'resistor en anglais', 'courant en anglais', 'tension en anglais', 'disjoncteur en anglais', 'cable en anglais', 'circuit breaker', 'current voltage resistance', 'how to say voltage in french', 'comment dit on tension en anglais', 'comment dit on resistance en anglais', 'traduction electricite anglais', 'dictionnaire electrique anglais francais', 'glossaire anglais electricite'], 'reponse': "Vocabulaire électrique anglais ↔ français :\n• Current (I) = courant ; voltage / tension (U, V) = tension ; resistance = résistance ; resistor = résistor ; power = puissance ; energy = énergie ; frequency = fréquence ; ohm / ampere / volt / watt.\n• Circuit breaker = disjoncteur ; fuse = fusible ; switch = interrupteur ; socket / outlet = prise ; plug = fiche ; bulb / lamp = lampe ; wire / cable = fil / câble ; terminal = borne ; busbar = jeu de barres ; earth (UK) / ground (US) = terre ; neutral = neutre ; live / phase = phase ; load = charge ; source = source ; leakage current = courant de fuite ; residual current device (RCD) = différentiel ; surge protector = parafoudre ; distribution board = tableau de distribution.\n• Motor = moteur ; generator = génératrice ; alternator = alternateur ; transformer = transformateur ; coil / winding = bobinage ; stator, rotor ; shaft = arbre ; bearing = roulement ; belt = courroie ; gear = engrenage ; relay = relais ; contactor = contacteur ; overload relay = relais thermique ; starter = démarreur ; drive / inverter = variateur ; PLC = automate ; sensor = capteur ; actuator = actionneur ; limit switch = fin de course ; push button = bouton-poussoir ; emergency stop = arrêt d'urgence.\n• Series = série ; parallel = parallèle ; single-phase / three-phase = monophasé / triphasé ; direct current (DC) = courant continu ; alternating current (AC) = courant alternatif ; short circuit = court-circuit ; overload = surcharge ; insulation = isolation ; earthing = mise à la terre.\n• Solar panel = panneau solaire ; charge controller = régulateur ; battery = batterie ; inverter = onduleur.\n• Maintenance : preventive / corrective maintenance, breakdown = panne, repair = réparer, troubleshooting = dépannage, spare part = pièce de rechange, lockout-tagout = consignation, toolbox = boîte à outils, screwdriver = tournevis, pliers = pince, multimeter = multimètre, wrench / spanner = clé.", 'mot_entier': True}, {'titre': 'grammaire anglaise de base', 'cles': ['grammaire anglaise', 'grammaire anglais', 'apprendre l anglais', 'apprendre anglais', 'cours d anglais', 'anglais debutant', 'anglais pour debutant', 'present simple', 'present continu', 'present continuous', 'passe simple anglais', 'past simple', 'futur anglais', 'will et going to', 'present perfect', 'temps en anglais', 'verbes irreguliers', 'verbes irreguliers anglais', 'liste verbes irreguliers', 'to be', 'to have', 'etre et avoir en anglais', 'pronoms anglais', 'articles a an the', 'a ou an', 'pluriel anglais', 'comparatif anglais', 'superlatif', 'modaux', 'can could must should', 'il y a en anglais', 'there is there are', 'question en anglais', 'forme negative anglais', 'do does did', 'prepositions anglais', 'in on at', 'how to say', 'english', 'anglais'], 'reponse': "Anglais — bases de grammaire :\n• To be : I am, you are, he/she/it is, we/you/they are. To have : I have, he has.\n• Pronoms : I, you, he, she, it, we, you, they ; possessifs : my, your, his, her, its, our, their.\n• Articles : a (devant un son consonne : a car), an (son voyelle : an engineer), the.\n• Présent simple (habitudes, vérités) : I work, he works (s à la 3e personne) ; négation : I do not (don't) work, he does not (doesn't) work ; question : Do you work ? Does he work ?\n• Présent continu (action en cours) : I am working. Passé simple : worked (réguliers : + ed) ; irréguliers : go → went, have → had, make → made, see → saw, do → did, write → wrote, build → built, break → broke, take → took ; négation : did not work ; question : Did you work ?\n• Futur : will + verbe (décision/prévision) : I will call you ; be going to (projet) : I am going to repair the motor.\n• Present perfect : have/has + participe passé : I have finished.\n• Modaux : can (pouvoir), must (devoir), should (conseil), may/might (possibilité), would. Exemple : You must switch off the power.\n• There is / there are = il y a. Comparatif : bigger than, more powerful than ; superlatif : the biggest, the most important.\n• Prépositions : in (dans/en), on (sur), at (à : at 5 pm, at the factory). Pluriel : + s (cars), -es (boxes), irréguliers : man → men, child → children.", 'mot_entier': True}, {'titre': 'phrases utiles en anglais', 'cles': ['phrases utiles en anglais', 'conversation en anglais', 'se presenter en anglais', 'comment se presenter en anglais', 'salutations en anglais', 'bonjour en anglais', 'merci en anglais', 'comment dire bonjour en anglais', 'comment dire merci en anglais', 'au revoir en anglais', 'how are you', 'my name is', 'i am an electrician', 'je suis electricien en anglais', 'je suis technicien en anglais', 'phrases pour voyager en anglais', 'demander son chemin en anglais', 'au restaurant en anglais', 'nombres en anglais', 'chiffres en anglais', 'compter en anglais', 'jours de la semaine en anglais', 'mois en anglais', 'couleurs en anglais', 'heure en anglais', 'what time is it', 'please and thank you', 'excuse me', 'sorry', 'can you help me', 'i don t understand', 'speak slowly', 'anglais courant'], 'reponse': "Phrases utiles en anglais :\n• Salutations : Hello / Hi (bonjour), Good morning, Good afternoon, Good evening, Goodbye / See you (au revoir), Please (s'il vous plaît), Thank you (merci), You're welcome (de rien), Excuse me / Sorry (pardon), Yes / No.\n• Se présenter : My name is Edison. I am from Benin. I am an electrician / an electrical technician. I work in industrial maintenance. Nice to meet you. How are you ? — I'm fine, thank you. And you ?\n• Comprendre : I don't understand. Can you repeat, please ? Can you speak slowly ? How do you say … in English ? Can you help me ?\n• Direction : Where is the station ? Turn left / right, go straight. How much is it ? Where can I find …?\n• Au travail : Switch off the power. Wear your safety helmet. The motor is overheating. The circuit breaker has tripped. I need a screwdriver. The machine is out of order.\n• Nombres : 1 one, 2 two, 3 three, 4 four, 5 five, 6 six, 7 seven, 8 eight, 9 nine, 10 ten, 20 twenty, 100 a hundred, 1 000 a thousand.\n• Jours : Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday. Mois : January, February, March, April, May, June, July, August, September, October, November, December.\n• Couleurs : red, blue, green, yellow, black, white, brown, grey.\n• Heure : It is half past three (3 h 30) ; a quarter to five (4 h 45).", 'mot_entier': True}, {'titre': 'anglais professionnel email et cv', 'cles': ['anglais professionnel', 'email en anglais', 'ecrire un email en anglais', 'e mail professionnel en anglais', 'lettre en anglais', 'cv en anglais', 'resume en anglais', 'job interview', 'entretien en anglais', 'interview questions', 'formules de politesse en anglais', 'dear sir or madam', 'best regards', 'yours sincerely', 'kind regards', 'candidature en anglais', 'cover letter', 'lettre de motivation en anglais', 'demande de stage en anglais', 'description de poste en anglais', 'competences en anglais', 'skills in english', 'work experience', 'references available', 'safety instructions en anglais', 'rapport en anglais', 'manual', 'notice d utilisation en anglais', 'lire une notice en anglais', 'datasheet', 'fiche technique en anglais'], 'reponse': "Anglais professionnel :\n• E-mail : Subject : Application for a technician position. Dear Sir or Madam, / Dear Mr Smith, … I am writing to apply for … I have three years of experience in electrical maintenance. Please find attached my CV. I would be glad to attend an interview. Thank you for your time. Best regards / Yours sincerely, Edison …\n• CV (résumé) : Personal details, Professional summary, Work experience, Education (Bachelor/BTS/Technician Diploma in Electrical Engineering), Skills (electrical installation, troubleshooting, PLC programming, solar systems), Languages (French: native, English: intermediate), References available on request.\n• Entretien : Tell me about yourself. What are your strengths ? Why do you want this job ? — I am a hard-working, reliable technician. I can read electrical diagrams and troubleshoot motors.\n• Lire une notice ou une datasheet : Rated voltage = tension nominale ; rated current = courant nominal ; operating temperature range = plage de température de fonctionnement ; dimensions ; warning = avertissement ; caution = attention ; danger = danger ; do not open = ne pas ouvrir ; installation instructions = instructions d'installation ; wiring diagram = schéma de câblage ; pinout = brochage ; maximum ratings = valeurs maximales.\n• Sécurité : Danger — high voltage ! Switch off before maintenance. Wear PPE (personal protective equipment).", 'mot_entier': True}, {'titre': 'conjugaison francaise', 'cles': ['conjugaison', 'conjugaison du verbe', 'temps de l indicatif', 'present de l indicatif', 'passe compose', 'passe simple', 'futur simple', 'participe passe', 'accord du participe passe', 'auxiliaire etre ou avoir', 'verbes du 1er groupe', 'verbes du 2e groupe', 'verbes du 3e groupe', 'verbes irreguliers francais', 'conjuguer etre', 'conjuguer avoir', 'conjuguer aller', 'conjuguer faire', 'terminaisons verbales', 'er ou e', 'infinitif ou participe', 'confusion er e'], 'reponse': "Conjugaison française :\n• Groupes : 1er groupe : -er (parler) ; 2e groupe : -ir/-issant (finir) ; 3e groupe : tous les autres (aller, faire, prendre, venir, pouvoir, vouloir…).\n• Présent : parler → je parle, tu parles, il parle, nous parlons, vous parlez, ils parlent ; finir → je finis… nous finissons ; être → je suis, tu es, il est, nous sommes, vous êtes, ils sont ; avoir → j'ai, tu as, il a, nous avons, vous avez, ils ont ; aller → je vais, tu vas, il va, nous allons, vous allez, ils vont ; faire → je fais, vous faites, ils font.\n• Passé composé = auxiliaire (être/avoir) au présent + participe passé : j'ai fini, elle est partie ; avec « avoir », le participe s'accorde avec le COD placé avant : les pièces que j'ai changées. Avec « être », il s'accorde avec le sujet.\n• Imparfait (je parlais), futur simple (je parlerai), conditionnel (je parlerais), impératif (parle ! parlons ! parlez !), subjonctif (il faut que je parle).\n• Astuce -er / -é : remplacer par « vendre/vendu » : si « vendre » passe, c'est l'infinitif en -er ; si « vendu » passe, c'est -é (participe).\n• Verbes en -yer : je nettoie ; verbes en -cer/-ger : nous commençons, nous mangeons.", 'mot_entier': True}, {'titre': 'orthographe et homophones', 'cles': ['orthographe', 'faute d orthographe', 'homophones', 'a ou a', 'a et a', 'et ou est', 'ce ou se', 'ces ou ses', 'c est ou s est', 'ou ou ou', 'son ou sont', 'on ou ont', 'mon ou m ont', 'leur ou leurs', 'quand quant et qu en', 'ni ou n y', 'ma ou m a', 'ta ou t a', 'la ou l a ou la', 'peu ou peut', 'tout ou tous', 'quel quelle quelles qu elle', 'accord de l adjectif', 'pluriel des noms', 'pluriel en s x', 'feminin des noms', 'accord sujet verbe', 'point virgule', 'orthographe electricite', 'ecriture des nombres', 'ecrire correctement'], 'reponse': "Orthographe — homophones courants :\n• a / à : « il a » (avoir : on peut mettre « avait ») ; « à » préposition (à Cotonou).\n• et / est : « est » = être (on peut mettre « était ») ; « et » = addition.\n• ou / où : « ou » = ou bien ; « où » = lieu.\n• son / sont : « sont » = être (étaient) ; « son » = possessif (son moteur).\n• on / ont : « ont » = avoir (avaient) ; « on » = pronom.\n• ce / se : « ce » = démonstratif (ce moteur) ; « se » = pronom réfléchi (il se repose).\n• ces / ses / c'est / s'est / sais / sait, leur / leurs, quel(le) / qu'elle, ma / m'a, ta / t'a, la / là / l'a, peu / peut, quand / quant / qu'en.\n• Pluriel : + s (moteurs) ; mots en -al → -aux (signal → signaux) ; -eau, -eu → +x (tableau → tableaux) ; exceptions : travail → travaux ; bleu → bleus.\n• Accords : sujet-verbe (« les ouvriers travaillent »), adjectif avec le nom (« des câbles isolés »).\n• Écriture des nombres : vingt et cent prennent un s au pluriel seulement quand rien ne les suit (quatre-vingts, quatre-vingt-trois ; deux cents, deux cent un).\n• Majuscules : début de phrase et noms propres ; ponctuation : virgule pour une pause, point-virgule, point.", 'mot_entier': True}, {'titre': 'yoruba de base', 'cles': ['yoruba', 'le yoruba', 'apprendre le yoruba', 'bonjour en yoruba', 'merci en yoruba', 'comment dit on bonjour en yoruba', 'yoruba salutations', 'chiffres en yoruba', 'nombres en yoruba', 'compter en yoruba', 'e kaaro', 'e ka sun', 'e kaaro o', 'e ku ise', 'o dabo', 'mo dupe', 'je t aime en yoruba', 'phrases en yoruba', 'expressions yoruba', 'yoruba au benin', 'ketou yoruba', 'langue yoruba'], 'reponse': "Yoruba (parlé dans l'est du Bénin — Kétou, Savè, Pobè, Sakété — et au Nigeria) : quelques expressions courantes (le yoruba est une langue à tons, l'écriture exacte avec accents est importante ; demande à un locuteur pour confirmer) :\n• Bonjour (matin) : E kàárọ̀ ; (après-midi) : E kàásán ; (soir) : E kùròlé ; bonne nuit : E ṣùúrù / O dàárọ̀.\n• Comment vas-tu ? : Báwo ni ?  — Je vais bien : Mo wà dáadáa.\n• Merci : Ẹ ṣé / Mo dúpẹ́ ; s'il vous plaît : Jọ̀wọ́ ; oui : Bẹ́ẹ̀ ni ; non : Rárá.\n• Au revoir : O dàbọ̀ ; courage au travail : Ẹ kú iṣẹ́.\n• Nombres : 1 ọ̀kan, 2 èjì, 3 ẹ̀ta, 4 ẹ̀rin, 5 àrún, 6 ẹ̀fà, 7 èje, 8 ẹ̀jọ, 9 ẹ̀sán, 10 ẹ̀wá.\n• Le suffixe « ẹ » est un marqueur de respect (pluriel de politesse).", 'mot_entier': True}, {'titre': 'outillage de l electricien', 'cles': ['outillage', 'outillage de l electricien', 'outils de l electricien', 'boite a outils electricien', 'outils pour electricien', 'tournevis isole', 'pince a denuder', 'pince coupante', 'pince a sertir', 'cosse', 'sertissage', 'tire fil', 'aiguille de tirage', 'perceuse', 'perforateur', 'niveau a bulle', 'metre ruban', 'detecteur de tension', 'testeur de phase', 'tournevis testeur', 'visseuse', 'gaine icta', 'conduit irl', 'moulure', 'goulotte', 'chemin de cable', 'collier rilsan', 'domino', 'borne a ressort', 'boite de derivation', 'boite d encastrement', 'que acheter pour commencer electricien', 'materiel de base electricien', 'materiel d electricien'], 'reponse': "Outillage et matériel de l'électricien :\n• Mesure et contrôle : multimètre, pince ampèremétrique, détecteur/testeur de tension (VAT), testeur de phase (tournevis testeur : seulement indicatif, ne pas s'y fier pour la sécurité), télluromètre, mégohmmètre, mètre ruban, niveau à bulle.\n• Outils manuels (isolés 1 000 V, norme VDE) : tournevis plats et cruciformes, pince universelle, pince coupante, pince à dénuder, pince à sertir pour cosses, couteau d'électricien, clés plates/à molette.\n• Outils électroportatifs : perceuse-visseuse, perforateur, meuleuse, scie cloche, rainureuse (à utiliser avec EPI et protection contre la poussière).\n• Passage des câbles : tire-fil (aiguille de tirage), gaines ICTA, conduits IRL, goulottes, moulures, chemins de câbles, colliers.\n• Connexion : dominos, bornes à ressort (type WAGO), cosses serties, boîtes de dérivation et d'encastrement.\n• EPI : casque, lunettes, gants isolants, chaussures de sécurité, vêtements adaptés.\n• Pour débuter : un bon multimètre, un testeur de tension fiable, des outils isolés, une perceuse, un jeu de cosses et de bornes, puis compléter selon les chantiers.", 'mot_entier': True}, {'titre': 'energies renouvelables', 'cles': ['energies renouvelables', 'energie renouvelable', 'energie eolienne', 'eolienne', 'aerogenerateur', 'energie hydraulique', 'barrage hydroelectrique', 'hydroelectricite', 'biomasse', 'biogaz', 'biocarburant', 'energie geothermique', 'energie marine', 'energie fossile', 'energies fossiles', 'energie propre', 'transition energetique', 'hydrogene', 'pile a combustible', 'energie verte', 'changement climatique', 'effet de serre', 'empreinte carbone', 'rechauffement climatique', 'efficacite energetique', 'economies d energie', 'reduire sa facture d electricite', 'ampoule led economie', 'classe energetique', 'isolation thermique'], 'reponse': "Énergies renouvelables et efficacité énergétique :\n• Renouvelables : solaire (photovoltaïque et thermique), éolien (aérogénérateurs : P ≈ ½·ρ·S·v³·Cp, la puissance varie comme le cube de la vitesse du vent ; Cp ≤ 0,59 limite de Betz), hydraulique (barrages, au fil de l'eau : P = ρ·g·Q·H·η), biomasse et biogaz (déchets agricoles, bois), géothermie, énergies marines.\n• Fossiles (non renouvelables) : pétrole, gaz, charbon ; émettent du CO₂ (effet de serre → réchauffement climatique).\n• Au Bénin : fort potentiel solaire (4,5 à 6 kWh/m²/jour environ), hydraulique sur l'Ouémé et le Mono (petites centrales), biomasse ; déploiement de centrales et de kits solaires.\n• Hydrogène : stockage/transport d'énergie, pile à combustible (H₂ + O₂ → électricité + eau).\n• Efficacité énergétique : lampes LED, appareils de classe A, variateurs sur moteurs, bonne isolation, climatiseurs inverter, coupure des veilles, entretien régulier.\n• Économiser : un appareil de 100 W allumé inutilement 10 h/jour consomme 1 kWh/jour, soit 30 kWh par mois.", 'mot_entier': True}, {'titre': 'soudage et travaux d atelier', 'cles': ['soudage', 'soudure a l arc', 'poste a souder', 'soudage a l arc', 'electrode enrobee', 'soudage mig mag', 'soudage tig', 'soudure oxyacetylenique', 'chalumeau', 'brasage', 'brasure', 'decoupe plasma', 'meulage', 'limage', 'percage', 'taraudage atelier', 'filetage atelier', 'tournage', 'fraisage', 'tour et fraiseuse', 'usinage', 'travaux d atelier', 'metallerie', 'atelier mecanique', 'ajustage', 'tracage', 'pied a coulisse', 'palmer', 'micrometre', 'jauge', 'calibre a coulisse', 'lecture du pied a coulisse', 'vernier'], 'reponse': "Soudage et travaux d'atelier :\n• Soudage à l'arc (électrode enrobée, MMA) : un arc électrique fond l'électrode et le métal ; réglage de l'intensité selon le diamètre (ex. 2,5 mm → 70-100 A) ; MIG/MAG : fil continu et gaz de protection ; TIG : électrode de tungstène, soudures fines et propres. Brasage : assemblage avec un métal d'apport moins fusible que les pièces.\n• Sécurité : masque de soudeur, gants, tablier, vêtements non synthétiques, ventilation (fumées), extincteur proche, aucun produit inflammable, poste mis à la terre, câbles en bon état ; ne jamais regarder l'arc sans protection.\n• Usinage : tournage (pièce qui tourne, outil fixe), fraisage (outil qui tourne), perçage, limage, taraudage.\n• Mesure : pied à coulisse (précision 0,1 ou 0,05 mm), palmer/micromètre (0,01 mm), comparateur, jauge. Lecture du pied à coulisse : partie entière lue sur la règle avant le zéro du vernier, décimale lue là où un trait du vernier coïncide avec un trait de la règle.\n• Rôle pour l'électrotechnicien : fabrication de supports, coffrets, mise en place de chemins de câbles et d'armoires.", 'mot_entier': True}, {'titre': 'redaction et expression ecrite', 'cles': ['redaction', 'expression ecrite', 'comment rediger', 'ecrire une lettre', 'lettre formelle', 'lettre administrative', 'lettre officielle', 'lettre informelle', 'demande ecrite', 'formule de politesse', 'objet de la lettre', 'resume de texte', 'faire un resume', 'contraction de texte', 'commentaire de texte', 'dissertation', 'plan de dissertation', 'argumentation', 'paragraphe argumentatif', 'introduction conclusion', 'rediger un rapport', 'compte rendu', 'compte rendu de reunion', 'note de service', 'plan d un expose', 'texte argumentatif', 'connecteurs logiques', 'mots de liaison', 'vocabulaire soutenu'], 'reponse': "Rédaction et expression écrite :\n• Lettre administrative : en haut à gauche expéditeur (nom, adresse, contact) ; à droite lieu et date ; destinataire ; « Objet : … » ; « Monsieur le Directeur, » ; corps (contexte → demande → justification) ; formule de politesse (« Je vous prie d'agréer, Monsieur, l'expression de ma haute considération. ») ; signature ; pièces jointes.\n• Résumé/contraction : lire deux fois, repérer les idées essentielles, reformuler avec ses propres mots (pas de copie), respecter la limite de mots (en général au dixième du texte), ne rien ajouter.\n• Dissertation/argumentation : introduction (amorce, problématique, annonce du plan) ; développement en 2 ou 3 parties avec arguments + exemples ; conclusion (bilan + ouverture). Chaque paragraphe : idée → argument → exemple.\n• Compte rendu de réunion : date, lieu, participants, ordre du jour, décisions, actions (qui, quoi, quand).\n• Connecteurs : d'abord, ensuite, enfin ; de plus, en outre ; mais, cependant, pourtant ; donc, c'est pourquoi, par conséquent ; car, parce que ; en conclusion.\n• Rapport technique : titre, objectif, matériel, méthode, résultats (tableaux, schémas), analyse, conclusion.", 'mot_entier': True}]
CONNAISSANCES_GENERALES.extend(FICHES_V33)


def chercher_connaissance_photovoltaique(texte):
    """Retourne une fiche photovoltaique pertinente, sans Internet."""
    t = mots_vides(texte)
    if not t:
        return None

    meilleur_score = 0
    meilleure_fiche = None

    for fiche in CONNAISSANCES_PHOTOVOLTAIQUE + CONNAISSANCES_GENERALES:
        score = 0
        for cle in fiche["cles"]:
            cle_norm = mots_vides(cle)
            if cle_norm and ((" " + cle_norm + " ") in (" " + t + " ") if fiche.get("mot_entier") else cle_norm in t):
                # Les expressions longues et precises ont plus de poids.
                score = max(score, 2 + len(cle_norm.split()))
        if score > meilleur_score:
            meilleur_score = score
            meilleure_fiche = fiche

    if meilleure_fiche and meilleur_score >= 3:
        return meilleure_fiche["reponse"]
    return None


def chercher_connaissance(texte):
    # La base solaire locale est consultée en premier, meme sans Internet.
    solaire = chercher_connaissance_photovoltaique(texte)
    if solaire:
        return solaire

    t = normaliser(texte)

    # Recherche des mots-clés les plus précis d'abord.
    for cle, valeur in CONNAISSANCES.items():
        mots = cle.split()
        if all(re.search(r"\b" + re.escape(mot) + r"\b", t) for mot in mots):
            return valeur

    # Recherche dans la mémoire personnalisée
    for cle, valeur in memoire["connaissances"].items():
        if re.search(r"\b" + re.escape(cle) + r"\b", t):
            return valeur

    return None


# ---------------- CALCULS ÉLECTROTECHNIQUES ----------------

UNIT_ALIASES = {
    # tension
    "v": ("V", 1.0), "volt": ("V", 1.0), "volts": ("V", 1.0),
    "kv": ("V", 1000.0), "kilovolt": ("V", 1000.0), "kilovolts": ("V", 1000.0),
    "mv": ("V", 0.001), "millivolt": ("V", 0.001), "millivolts": ("V", 0.001),
    # courant
    "a": ("A", 1.0), "amp": ("A", 1.0), "ampere": ("A", 1.0), "amperes": ("A", 1.0),
    "ma": ("A", 0.001), "milliampere": ("A", 0.001), "milliamperes": ("A", 0.001),
    # résistance
    "ohm": ("Ω", 1.0), "ohms": ("Ω", 1.0), "ω": ("Ω", 1.0), "Ω": ("Ω", 1.0),
    "kohm": ("Ω", 1000.0), "kω": ("Ω", 1000.0), "kΩ": ("Ω", 1000.0),
    # puissance
    "w": ("W", 1.0), "watt": ("W", 1.0), "watts": ("W", 1.0),
    "kw": ("W", 1000.0), "kilowatt": ("W", 1000.0), "kilowatts": ("W", 1000.0),
    # énergie
    "wh": ("Wh", 1.0), "kwh": ("Wh", 1000.0),
    # temps
    "s": ("s", 1.0), "sec": ("s", 1.0), "seconde": ("s", 1.0), "secondes": ("s", 1.0),
    "min": ("s", 60.0), "minute": ("s", 60.0), "minutes": ("s", 60.0),
    "h": ("h", 1.0), "heure": ("h", 1.0), "heures": ("h", 1.0),
    # longueur / section pour câble
    "m": ("m", 1.0), "metre": ("m", 1.0), "metres": ("m", 1.0), "mètre": ("m", 1.0), "mètres": ("m", 1.0),
    "mm2": ("mm2", 1.0), "mm²": ("mm2", 1.0),
}


def _nombre_texte(x):
    return float(str(x).replace(",", "."))


def extraire_valeur_unite(texte, unite_cible=None):
    """Extrait une valeur suivie d'une unité. Retourne (valeur_normalisée, unité) ou None."""
    t = sans_accents(texte.lower()).replace("²", "2")
    pattern = r"(?<![a-zA-Z0-9])([-+]?\d+(?:[.,]\d+)?)\s*(kv|mv|kohm|kω|kΩ|kw|kwh|ma|mm2|mm²|wh|w|v|a|ohm|ω|Ω|h|min|s|m|ampere?s?|volt?s?|watt?s?|heure?s?|minute?s?|seconde?s?)\b"
    matches = re.findall(pattern, t, flags=re.I)
    for nombre, unite in matches:
        cle = unite.lower()
        if cle not in UNIT_ALIASES:
            continue
        unite_norm, facteur = UNIT_ALIASES[cle]
        valeur = _nombre_texte(nombre) * facteur
        if unite_cible is None or unite_norm == unite_cible:
            return valeur, unite_norm
    return None


def extraire_toutes_valeurs(texte):
    """Extrait les couples valeur/unité pour produire des messages d'erreur précis."""
    t = sans_accents(texte.lower()).replace("²", "2")
    pattern = r"(?<![a-zA-Z0-9])([-+]?\d+(?:[.,]\d+)?)\s*(kv|mv|kohm|kω|kΩ|kw|kwh|ma|mm2|wh|w|v|a|ohm|ω|Ω|h|min|s|m|ampere?s?|volt?s?|watt?s?|heure?s?|minute?s?|seconde?s?)\b"
    result = []
    for nombre, unite in re.findall(pattern, t, flags=re.I):
        cle = unite.lower()
        if cle in UNIT_ALIASES:
            u, facteur = UNIT_ALIASES[cle]
            result.append((_nombre_texte(nombre) * facteur, u, nombre, unite))
    return result


def valeur_par_unite(texte, unite):
    vals = extraire_toutes_valeurs(texte)
    for valeur, u, _, _ in vals:
        if u == unite:
            return valeur
    return None


def contient_mot(t, mots):
    return any(re.search(r"\b" + re.escape(m) + r"\b", t) for m in mots)


def format_unite(valeur, unite):
    """Affichage pratique avec une unité adaptée."""
    if unite == "V":
        if abs(valeur) >= 1000:
            return f"{format_nombre(valeur / 1000)} kV"
        if 0 < abs(valeur) < 1:
            return f"{format_nombre(valeur * 1000)} mV"
        return f"{format_nombre(valeur)} V"
    if unite == "A":
        if abs(valeur) >= 1:
            return f"{format_nombre(valeur)} A"
        return f"{format_nombre(valeur * 1000)} mA"
    if unite == "Ω":
        if abs(valeur) >= 1000:
            return f"{format_nombre(valeur / 1000)} kΩ"
        return f"{format_nombre(valeur)} Ω"
    if unite == "W":
        if abs(valeur) >= 1000:
            return f"{format_nombre(valeur / 1000)} kW"
        return f"{format_nombre(valeur)} W"
    if unite == "Wh":
        if abs(valeur) >= 1000:
            return f"{format_nombre(valeur / 1000)} kWh"
        return f"{format_nombre(valeur)} Wh"
    if unite == "s":
        return f"{format_nombre(valeur)} s"
    if unite == "h":
        return f"{format_nombre(valeur)} h"
    return f"{format_nombre(valeur)} {unite}"


def calcul_electrique(question):
    """Calcul local ; si l'IA est active, elle prend le relais quand il manque des données."""
    r = _calcul_electrique_local(question)
    if r and ia_disponible():
        calcul_fait = ("Formule" in r or "positives" in r or "cos φ doit" in r)
        if not calcul_fait:
            return None
    return r


def _calcul_electrique_local(question):
    """Calculs U, I, R, P, E et chute de tension avec contrôle des unités."""
    t = mots_vides(question)

    # Ne s'active que si la question ressemble à un calcul électrique.
    sujets = ["puissance", "tension", "courant", "intensite", "intensite", "resistance", "energie", "chute de tension"]
    if not contient_un(t, sujets):
        return None

    vals = extraire_toutes_valeurs(question)
    if not vals:
        return (
            "Je peux faire ce calcul, mais il me manque les valeurs avec leurs unités.\n"
            "Exemples : « puissance avec 230 V et 5 A » ou « résistance avec 24 V et 2 A »."
        )

    # Refus clair des unités inconnues détectables comme nombre + texte.
    nombres = re.findall(r"[-+]?\d+(?:[.,]\d+)?\s*([a-zA-ZΩ²]+)", question)
    unites_connues = {k.lower() for k in UNIT_ALIASES}
    for u in nombres:
        if u.lower() not in unites_connues:
            return f"Unité « {u} » non reconnue. Utilise V, A, Ω, W, kW, Wh, kWh, h, min ou s."

    # Valeurs principales
    U = valeur_par_unite(question, "V")
    I = valeur_par_unite(question, "A")
    R = valeur_par_unite(question, "Ω")
    P = valeur_par_unite(question, "W")
    E = valeur_par_unite(question, "Wh")
    h = valeur_par_unite(question, "h")
    secondes = valeur_par_unite(question, "s")
    minutes = valeur_par_unite(question, "s")

    if any(x is not None and x < 0 for x in [U, I, R, P, E, h, secondes, minutes]):
        return "Les valeurs électriques doivent être positives ou nulles. Une tension, un courant, une résistance, une puissance ou une énergie négative n'est pas acceptée ici."

    # cos phi optionnel
    cosphi = None
    qcos = question.lower().replace(",", ".")
    mcos = re.search(r"cos(?:inus)?(?:\s*(?:phi|φ))?\s*(?:=|de|a)?\s*([-+]?\d+(?:\.\d+)?)", qcos)
    if mcos:
        cosphi = _nombre_texte(mcos.group(1))
        if not 0 < cosphi <= 1:
            return "Le cos φ doit être strictement supérieur à 0 et inférieur ou égal à 1. Exemple : cos φ = 0,8."

    # PUISSANCE
    if contient_mot(t, ["puissance"]):
        if U is not None and I is not None:
            Pcalc = U * I * (cosphi if cosphi is not None else 1.0)
            mode = "avec cos φ" if cosphi is not None else "en courant continu / charge résistive"
            return f"Puissance calculée : {format_unite(Pcalc, 'W')}\nFormule : P = U × I" + (" × cos φ" if cosphi is not None else "") + f"\n({mode})."
        if P is not None:
            return "Tu m'as donné une puissance, mais pour vérifier/calculer il me faut aussi une tension et un courant."
        return "Pour calculer la puissance, donne une tension en V et un courant en A. Exemple : « puissance avec 230 V et 5 A »."

    # CHUTE DE TENSION (avant « tension » pour éviter qu'elle soit capturée comme U = RI)
    if contient_un(t, ["chute de tension"]):
        if R is not None and I is not None:
            return f"Chute de tension : {format_unite(R * I, 'V')}\nFormule : ΔU = R × I."
        return "Pour calculer la chute de tension avec la méthode simple, donne la résistance du conducteur en Ω et le courant en A. Exemple : « chute de tension avec 0,8 Ω et 10 A »."

    # TENSION
    if contient_mot(t, ["tension"]):
        if R is not None and I is not None:
            return f"Tension calculée : {format_unite(R * I, 'V')}\nFormule : U = R × I."
        if P is not None and I is not None and I != 0:
            return f"Tension calculée : {format_unite(P / I, 'V')}\nFormule : U = P / I."
        return "Pour calculer la tension, donne soit R en Ω et I en A, soit P en W et I en A."

    # COURANT
    if contient_mot(t, ["courant", "intensite"]):
        if U is not None and R is not None and R != 0:
            return f"Courant calculé : {format_unite(U / R, 'A')}\nFormule : I = U / R."
        if P is not None and U is not None and U != 0:
            return f"Courant calculé : {format_unite(P / U, 'A')}\nFormule : I = P / U."
        return "Pour calculer le courant, donne soit U en V et R en Ω, soit P en W et U en V."

    # RESISTANCE
    if contient_mot(t, ["resistance"]):
        if U is not None and I is not None and I != 0:
            return f"Résistance calculée : {format_unite(U / I, 'Ω')}\nFormule : R = U / I."
        if P is not None and I is not None and I != 0:
            return f"Résistance calculée : {format_unite(P / (I * I), 'Ω')}\nFormule : R = P / I²."
        if P is not None and U is not None and P != 0:
            return f"Résistance calculée : {format_unite((U * U) / P, 'Ω')}\nFormule : R = U² / P."
        return "Pour calculer la résistance, donne U en V et I en A (ou P et I / P et U)."

    # ENERGIE
    if contient_mot(t, ["energie"]):
        # Si P est donné en W et durée en h -> Wh. Si durée en min/s -> conversion.
        duree_h = h
        if duree_h is None and secondes is not None:
            duree_h = secondes / 3600.0
        if duree_h is None:
            return "Pour calculer l'énergie, donne une puissance en W ou kW et une durée en h, min ou s. Exemple : « énergie de 2 kW pendant 3 h »."
        if P is None:
            return "Il me manque la puissance. Exemple : « énergie de 1500 W pendant 2 h »."
        return f"Énergie calculée : {format_unite(P * duree_h, 'Wh')}\nFormule : E = P × t."

    return None

# ---------------- COÛTS ----------------

def parser_ligne_article(ligne):
    """
    Formats acceptés :
    - fil 10m 1000
    - fil;10;1000
    - 1 prise 300
    """
    ligne = ligne.strip()

    m = re.match(r"^(.*?)[\s;]+(\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)\s*[fF]?$", ligne)
    if not m:
        return None

    nom = m.group(1).strip(" ;:-")
    quantite = float(m.group(2).replace(",", "."))
    prix = float(m.group(3).replace(",", "."))

    if not nom or quantite <= 0 or prix < 0:
        return None

    return nom, quantite, prix


def calculer_liste_cout(lignes):
    total = 0
    details = []

    for ligne in lignes:
        article = parser_ligne_article(ligne)
        if article is None:
            continue

        nom, qte, prix = article
        montant = qte * prix
        total += montant
        details.append((nom, qte, prix, montant))

    return total, details


# ---------------- COMMANDES ----------------

AIDE = """
Commandes Novi V6

  aide
      Affiche cette aide.

  retiens que nom = valeur
      Exemple : retiens que métier = électrotechnicien

  mémoire
      Affiche les informations mémorisées.

  oublie nom
      Supprime une information.

  apprends : question = réponse
      Enseigne une connaissance à Novi. Exemple :
      apprends : c'est quoi un fusible = Un fusible protège un circuit en fondant.

  calc 25*4+10
      Effectue un calcul sécurisé.

  coût
      Lance le calcul de coût. Exemple de lignes :
      fil méplat 10 1000
      prise 2 300
      ampoule 3 1000
      puis : fin

  heure
      Affiche la date et l'heure du téléphone.

  quitter
      Ferme Novi.

Tu peux aussi poser directement une question d'électrotechnique.
"""


def afficher_memoire():
    infos = memoire["informations"]
    if not infos:
        return "Ma mémoire personnelle est vide."

    lignes = ["Voici ce que je retiens :"]
    for cle, valeur in infos.items():
        lignes.append(f"- {cle} : {valeur}")
    return "\n".join(lignes)


def commande_cout():
    print("\nEDISON — CALCUL DE COÛT")
    print("Format : article quantité prix_unitaire")
    print("Exemple : prise 2 300")
    print("Tape 'fin' pour terminer.\n")

    lignes = []
    while True:
        ligne = input("Article > ").strip()
        if normaliser(ligne) == "fin":
            break
        if ligne:
            lignes.append(ligne)

    total, details = calculer_liste_cout(lignes)

    if not details:
        return "Je n'ai pas reconnu les lignes. Exemple : prise 2 300"

    print("\nDÉTAIL")
    for nom, qte, prix, montant in details:
        print(f"- {nom}: {format_nombre(qte)} × {format_nombre(prix)} F = {format_nombre(montant)} F")

    return f"TOTAL = {format_nombre(total)} F CFA"



# ---------------- COMPRÉHENSION DU FRANÇAIS NATUREL ----------------

def sans_accents(texte):
    texte = unicodedata.normalize("NFD", texte)
    return "".join(c for c in texte if unicodedata.category(c) != "Mn")


def mots_vides(texte):
    """Normalise le texte pour faciliter la détection d'intentions."""
    t = sans_accents(texte.lower())
    t = re.sub(r"[!?;:,'\"“”«»()\[\]]", " ", t)
    t = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", t)   # « sais-tu » -> « sais tu »
    t = re.sub(r"\s+", " ", t).strip()
    return t


def contient_un(t, expressions):
    return any(expr in t for expr in expressions)


def extraire_expression_math(question):
    """
    Extrait une opération d'une phrase naturelle.
    Exemples :
      'combien font 25 fois 17 ?'
      'calcule 230 / 10'
      'j'aimerais savoir combien font 120 + 35'
    """
    t = mots_vides(question)

    # Transformations linguistiques -> opérateurs.
    remplacements = [
        (r"\bfois\b", "*"),
        (r"\bmultiplie(?:r|z)?\b", "*"),
        (r"\bmultiplie\b", "*"),
        (r"\bdivise(?:r|z)?\b", "/"),
        (r"\bdivisé par\b", "/"),
        (r"\bdivisee par\b", "/"),
        (r"\bplus\b", "+"),
        (r"\bmoins\b", "-"),
        (r"\bretire\b", "-"),
        (r"\bsoustrais\b", "-"),
        (r"\bmodulo\b", "%"),
    ]

    expr = t
    for motif, remplacement in remplacements:
        expr = re.sub(motif, remplacement, expr)

    # Retirer les formulations autour du calcul.
    expr = re.sub(
        r"\b(combien font|combien fait|calcule|calculer|fais le calcul de|"
        r"donne moi le resultat de|quel est le resultat de|"
        r"peux tu calculer|peux tu me calculer|j aimerais savoir)\b",
        " ",
        expr
    )

    # Trop de mots restants = vraie question, pas un calcul.
    if len([w for w in expr.split() if re.search(r"[a-z]", w)]) > 1:
        return None

    # Ne garder qu'une expression mathématique raisonnablement sûre.
    expr = re.sub(r"[^0-9+\-*/%.() ]", " ", expr)
    expr = re.sub(r"\s+", " ", expr).strip()

    if re.fullmatch(r"[0-9+\-*/%.() ]+", expr) and any(c.isdigit() for c in expr) and re.search(r"[+\-*/%]", expr):
        return expr
    return None


CLES_PERSONNELLES = {"nom", "prenom", "metier", "profession", "age", "ville", "quartier", "pays",
                     "projet", "adresse", "numero", "email", "ecole", "entreprise", "societe"}


def extraire_retenue_naturelle(question):
    """
    Reconnaît plusieurs façons de demander une mémorisation.
    Exemples :
      'souviens-toi que mon métier est électrotechnicien'
      'garde en mémoire que mon projet est Edison'
      'retiens que j'habite à Bohicon'
      'n'oublie pas que...'
    """
    q = question.strip()
    if "?" in q:
        return None

    motifs = [
        r"(?:souviens[- ]toi|souviens toi)\s+(?:que\s+)?(.+?)\s*=\s*(.+)$",
        r"(?:retiens|retient|garde en memoire|garde en tête|n oublie pas)\s+(?:que\s+)?(.+?)\s*=\s*(.+)$",
        r"(?:souviens[- ]toi|souviens toi|retiens|garde en memoire|n oublie pas)\s+que\s+(.+?)\s+(?:est|c est|ce sont)\s+(.+)$",
        r"(?:mon|ma|mes)\s+(.+?)\s+(?:est|sont)\s+(.+)$",
    ]

    for motif in motifs:
        m = re.match(motif, q, re.I)
        if m:
            cle = m.group(1).strip(" .?!")
            valeur = m.group(2).strip(" .?!")
            if not (cle and valeur and len(cle.split()) <= 3 and len(valeur.split()) <= 8):
                continue
            if motif == motifs[3] and sans_accents(cle.lower()) not in CLES_PERSONNELLES:
                continue
            # "mon métier" -> "métier"
            cle = re.sub(r"^(mon|ma|mes)\s+", "", cle, flags=re.I)
            return cle, valeur
    return None


def reponse_salutation(t):
    mots = t.split()
    if len(mots) <= 5 and (any(m in mots for m in ("bonjour", "bonsoir", "salut", "coucou")) or "bonne journee" in t):
        if "comment vas tu" in t or "ca va" in t:
            return "Je vais bien et je suis prêt à travailler avec toi. Que veux-tu faire ?"
        debut = "Bonsoir" if "bonsoir" in mots else "Bonjour"
        return f"{debut} ! Je suis Novi. Comment vas-tu ? Dis-moi simplement ce dont tu as besoin, même avec une phrase normale."
    return None


def intention_memoire(question):
    t = mots_vides(question)

    if contient_un(t, [
        "que sais tu de moi",
        "qu est ce que tu sais de moi",
        "qu est ce que tu retiens de moi",
        "montre ma memoire",
        "affiche ma memoire",
        "tu te souviens de quoi"
    ]):
        return "AFFICHER"

    # "Est-ce que tu te souviens de mon métier ?"
    if contient_un(t, [
        "tu te souviens de mon metier",
        "tu te rappelles de mon metier",
        "quel est mon metier",
        "je suis quoi comme metier",
        "qu est ce que je fais comme metier"
    ]):
        return "METIER"

    if contient_un(t, [
        "oublie tout",
        "efface ma memoire",
        "supprime ma memoire"
    ]):
        return "OUBLIER_TOUT"

    if t.startswith("oublie ") or t.startswith("efface "):
        return "OUBLIER_UN"

    return None


def intention_electrotechnique(question):
    t = mots_vides(question)

    # Questions naturelles, pas seulement les mots-clés exacts.
    if contient_un(t, ["loi d ohm", "loi ohm", "comment calculer le courant avec la resistance"]):
        return "loi d ohm"

    if contient_un(t, ["puissance en triphase", "puissance triphase", "calculer la puissance triphase"]):
        return "puissance triphase"

    if contient_un(t, ["puissance en monophase", "puissance monophase", "calculer la puissance monophase"]):
        return "puissance monophase"

    if contient_un(t, ["energie electrique", "calculer l energie", "combien d energie"]):
        return "energie"

    if contient_un(t, ["comment fonctionne un api", "c est quoi un api", "qu est ce qu un api", "automate programmable"]):
        return "api"

    if contient_un(t, ["maintenance preventive", "maintenance corrective", "difference entre maintenance preventive et corrective"]):
        return "maintenance"

    if contient_un(t, ["comment diagnostiquer un moteur", "diagnostiquer un moteur", "probleme de moteur"]):
        return "moteur"

    if contient_un(t, ["a quoi sert un disjoncteur", "role du disjoncteur", "disjoncteur"]):
        return "disjoncteur"

    if contient_un(t, ["comment fonctionne un transformateur", "c est quoi un transformateur", "transformateur"]):
        return "transformateur"

    return None


def intention_cout(question):
    t = mots_vides(question)

    return contient_un(t, [
        "combien coute",
        "combien ca coute",
        "calcule le cout",
        "calcule moi le cout",
        "fais le devis",
        "fais moi le devis",
        "calcule le total",
        "quel est le total",
        "additionne mes achats",
        "additionne tout",
        "combien je dois payer"
    ])


CREATEUR = "Edison Ahodede, électrotechnicien"


def reponse_identite(t):
    """Qui est Novi, qui l'a créé."""
    if contient_un(t, ["qui t a cree", "qui t a fait", "qui t a programme", "qui t a concu",
                       "qui t a developpe", "ton createur", "qui est ton createur"]):
        return (f"Mon créateur est {CREATEUR}. Il m'a conçue pour aider à apprendre, "
                "calculer et résoudre des problèmes techniques.")
    if contient_un(t, ["qui es tu", "tu es qui", "presente toi", "comment tu t appelles",
                       "comment t appelles tu", "quel est ton nom", "tu t appelles comment"]):
        return (f"Je suis Novi, une assistante IA francophone créée par {CREATEUR}. "
                "J'aide en électrotechnique, énergies renouvelables, programmation et culture générale, "
                "et je peux retenir ce que tu m'apprends.")
    return None


def reponse_politesse(t):
    """Réponses aux politesses courantes, sans phrase exacte obligatoire."""
    import random
    n = len(t.split())
    if n <= 5 and re.search(r"\bmerci\b", t):
        return random.choice(["Pas de quoi ! 😊", "Avec plaisir ! ⚡", "De rien, c'est normal ! 🤝"])
    if n <= 4 and re.match(r"^(ok|okay|d accord|dac|entendu|bien recu|compris|c est bon|cool|parfait|nickel|top)\b", t):
        return random.choice(["D'accord ! 👍", "Entendu ! ✅", "Très bien ! 😊"])
    if n <= 6 and (re.search(r"\b(blague|humour|drole)\b", t) or "fais moi rire" in t):
        return random.choice(["Pourquoi les électriciens sont-ils toujours calmes ? Parce qu’ils ont de la résistance ! 😄", "Quel est le comble pour un électricien ? De ne pas être au courant ! ⚡😂", "Que dit un fil à un autre fil ? Ne me lâche pas, restons en contact ! 🔌😆"])
    if n <= 7 and "et toi" in t:
        return "Ça va très bien, merci. Je suis prête à t'aider. Que veux-tu apprendre aujourd'hui ?"
    if n <= 6 and contient_un(t, ["comment ca va", "comment vas tu", "comment tu vas",
                                  "tu vas bien", "comment allez vous"]):
        return "Ça va bien, merci ! Et toi, comment vas-tu ?"
    if t in ("ca va", "ca va bien", "je vais bien", "tres bien", "super", "ca va tres bien"):
        return "Content de l'entendre ! Que veux-tu apprendre aujourd'hui ?"
    if n <= 6 and contient_un(t, ["je suis triste", "ca ne va pas", "je vais mal", "pas bien"]):
        return "Je suis désolée que tu te sentes comme ça. Raconte-moi si tu veux, je t'écoute."
    if n <= 5 and contient_un(t, ["au revoir", "a bientot", "a demain", "bye", "ciao"]):
        return "Au revoir ! À bientôt."
    if n <= 4 and "bonne nuit" in t:
        return "Bonne nuit ! Repose-toi bien."
    if n <= 4 and contient_un(t, ["pardon", "desole", "excuse moi"]):
        return "Aucun souci, ce n'est pas grave."
    if t in ("de rien", "avec plaisir", "je t en prie"):
        return "Merci à toi !"
    return None


def commande_apprendre(question):
    """apprends : ta question = ma réponse  -> enrichit la mémoire de connaissances."""
    m = re.match(r"^\s*apprends?\s*(?:que\s*)?:?\s*(.+?)\s*(?:=|->|=>)\s*(.+)$", question, re.I | re.S)
    if not m:
        return None
    cle, valeur = mots_vides(m.group(1)), m.group(2).strip()
    if not cle or not valeur:
        return "Format : apprends : ta question = ma réponse"
    memoire["connaissances"][cle] = valeur
    sauvegarder_memoire(memoire)
    return f"C'est noté, j'ai appris « {m.group(1).strip()} »."


def chercher_apprentissage(question):
    """Cherche dans ce que l'utilisateur a enseigné à Novi."""
    t = mots_vides(question)
    mots_t = t.split()
    meilleur, score = None, 0
    for cle, valeur in memoire["connaissances"].items():
        c = mots_vides(cle)
        if not c:
            continue
        mots_c = [w for w in c.split() if len(w) > 3]
        if (" " + c + " ") in (" " + t + " ") or (mots_c and len(c.split()) <= 6 and all(w in mots_t for w in mots_c)):
            if len(c) > score:
                meilleur, score = valeur, len(c)
    return meilleur


def reponse_naturelle(question):
    """
    Moteur d'intention léger : il ne remplace pas un grand modèle de langage,
    mais il permet à Novi de comprendre de nombreuses formulations françaises.
    """
    t = mots_vides(question)

    # 1. Salutations
    salutation = reponse_salutation(t)
    if salutation:
        return salutation

    # 1b. Ce que l'utilisateur a appris à Novi, identité, politesse
    appris = chercher_apprentissage(question)
    if appris:
        return appris
    ident = reponse_identite(t)
    if ident:
        return ident
    polie = reponse_politesse(t)
    if polie:
        return polie

    # 2. Mémoire
    mem_intent = intention_memoire(question)
    if mem_intent == "AFFICHER":
        return afficher_memoire()

    if mem_intent == "METIER":
        metier = (
            memoire["informations"].get("métier")
            or memoire["informations"].get("metier")
        )
        return f"Tu m'as dit que ton métier est {metier}." if metier else "Tu ne m'as pas encore indiqué ton métier."

    if mem_intent == "OUBLIER_TOUT":
        memoire["informations"].clear()
        sauvegarder_memoire(memoire)
        return "J'ai effacé les informations personnelles enregistrées dans ma mémoire."

    if mem_intent == "OUBLIER_UN":
        morceau = re.sub(r"^(oublie|efface)\s+", "", question, flags=re.I).strip()
        return (
            f"J'ai supprimé « {morceau} » de ma mémoire."
            if oublier(morceau)
            else f"Je ne trouve pas « {morceau} » dans ma mémoire."
        )

    retenue = extraire_retenue_naturelle(question)
    if retenue:
        cle, valeur = retenue
        retenir(cle, valeur)
        return f"D'accord, je vais retenir que {cle} = {valeur}."

    # Cas spécial très naturel : "Je m'appelle X"
    m = re.match(r"^\s*je\s+m['’]?appelle\s+(.+?)\s*[.!?]?$", question, re.I)
    if m:
        nom = m.group(1).strip()
        retenir("nom", nom)
        return f"Enchanté, {nom}. Je peux retenir ton nom pour les prochaines sessions."

    # Questions de connaissances solaires sans valeurs numeriques :
    # les traiter comme des demandes d'explication, pas comme des calculs incomplets.
    if not extraire_toutes_valeurs(question):
        connaissance_solaire = chercher_connaissance_photovoltaique(question)
        if connaissance_solaire:
            return connaissance_solaire

    # 4. Calculs électriques avec unités
    calcul_elec = calcul_electrique(question)
    if calcul_elec:
        return calcul_elec

    # 3. Calculs naturels
    expr = extraire_expression_math(question)
    if expr:
        try:
            resultat = calculer(expr)
            return f"Le résultat est {format_nombre(resultat)}."
        except Exception as e:
            return f"Je comprends que tu veux faire un calcul, mais je n'ai pas pu le terminer : {e}"

    # 4. Coût / devis
    if intention_cout(question):
        return (
            "Oui. Je peux calculer le total. Donne-moi simplement les articles "
            "comme ceci, une ligne par article :\n"
            "fil 10 1000 / prise 2 300 / ampoule 3 1000\n(article, quantité, prix unitaire, séparés par « / »)"
        )

    # 5. Électrotechnique
    intent = intention_electrotechnique(question)
    if intent and not ia_disponible():
        # On mappe vers les clés de la base V6.
        mapping = {
            "loi d ohm": "loi d ohm",
            "puissance triphase": "puissance triphasé",
            "puissance monophase": "puissance monophasé",
            "energie": "energie",
            "api": "api",
            "maintenance": "maintenance",
            "moteur": "moteur",
            "disjoncteur": "disjoncteur",
            "transformateur": "transformateur",
        }
        return CONNAISSANCES[mapping[intent]]

    # 6. Heure/date en langage naturel
    if contient_un(t, [
        "quelle heure est il",
        "tu peux me donner l heure",
        "donne moi l heure",
        "quelle est la date",
        "on est quel jour",
        "quelle date sommes nous"
    ]):
        return datetime.now().strftime("Nous sommes le %d/%m/%Y à %H:%M:%S.")

    # 7. Aide naturelle
    if len(t.split()) <= 8 and contient_un(t, [
        "que peux tu faire",
        "qu est ce que tu peux faire",
        "comment peux tu m aider",
        "comment tu peux m aider",
        "aide moi",
        "aide"
    ]):
        return (
            "Je peux déjà comprendre des phrases naturelles pour :\n"
            "- faire des calculs (« combien font 25 fois 17 ? »)\n"
            "- calculer puissance, tension, courant, résistance, énergie et chute de tension avec les unités\n"
            "- mémoriser des informations (« souviens-toi que mon projet est Edison »)\n"
            "- répondre à des questions d'électrotechnique\n"
            "- calculer des coûts et préparer un devis\n"
            "- expliquer énergies renouvelables, centrales, production de courant\n"
            "- t'apprendre les bases du code (Python, HTML) et la culture générale\n"
            "- retenir ce que tu m'apprends (apprends : question = réponse)\n"
            "- te donner la date et l'heure\n"
            "- écouter ta voix et répondre vocalement sur Android\n"
            "- répondre à presque toutes tes questions avec l'IA (tape : ia cle TA_CLE_API).\n\n"
            "Tu n'es donc plus obligé d'utiliser des commandes exactes."
        )

    if "merci" in t and len(t.split()) <= 6:
        return "Avec plaisir. On continue."

    # Questions solaires : reponse locale hors ligne, meme si la cle IA est absente.
    connaissance_solaire = chercher_connaissance_photovoltaique(question)
    if connaissance_solaire:
        return connaissance_solaire

    return None


# ---------------- INTELLIGENCE ARTIFICIELLE (API CLAUDE) ----------------

API_URL = "https://api.anthropic.com/v1/messages"
MODELE_IA = "claude-haiku-5-5"   # rapide et économique ; "claude-sonnet-5-5" = réponses plus poussées
CLE_FILE = "cle_api.txt"
MAX_TOKENS_IA = 700
DELAI_IA = 45

CONVERSATION = []   # échanges de la session en cours


class ErreurIA(Exception):
    pass


def lire_cle_api():
    cle = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if cle:
        return cle
    try:
        with open(CLE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def enregistrer_cle_api(cle):
    with open(CLE_FILE, "w", encoding="utf-8") as f:
        f.write(cle.strip())


def ia_disponible():
    return bool(lire_cle_api())


def prompt_systeme():
    infos = memoire["informations"]
    profil = "\n".join(f"- {k} : {v}" for k, v in infos.items()) or "(rien d'enregistré)"
    return (
        "Tu es Novi, une assistante IA francophone dans une application Android. "
        "Ton domaine principal est l'électrotechnique : installations électriques, moteurs, "
        "câblage, protections, automatismes, maintenance, énergie solaire, vidéosurveillance. "
        "Ton créateur est " + CREATEUR + ". Tu enseignes aussi les énergies renouvelables, les centrales et systèmes énergétiques, "
        "la programmation (Python, HTML), la culture générale, la politesse et les salutations.\n"
        "Notre objectif : devenir une vraie assistante IA francophone qui aide à apprendre l'électrotechnique, le solaire, les énergies renouvelables, le code et la culture générale, avec politesse et mémoire. "
        "Ton créateur est un électrotechnicien qui rêve de bâtir quelque chose de plus grand ; reste fidèle à ce but.\n"
        "Tu peux aussi aider sur d'autres sujets (rédaction, études, vie pratique).\n"
        "Règles : réponds en français simple, clair et court (petit écran) ; donne les formules "
        "et les étapes quand c'est utile ; pose une seule question si une information essentielle "
        "manque ; rappelle la sécurité (consignation, vérification d'absence de tension) seulement "
        "quand le risque est réel ; si tu n'es pas sûre, dis-le. "
        "Écris en texte brut : pas de tableaux, pas de Markdown (l'affichage ne l'interprète pas).\n"
        "Informations que l'utilisateur t'a demandé de retenir :\n" + profil
    )


def demander_ia(question):
    cle = lire_cle_api()
    if not cle:
        raise ErreurIA("Aucune clé API configurée.")

    CONVERSATION.append({"role": "user", "content": question})
    del CONVERSATION[:-24]
    messages = CONVERSATION[-12:]
    while messages and messages[0]["role"] != "user":
        messages = messages[1:]

    corps = json.dumps({
        "model": MODELE_IA,
        "max_tokens": MAX_TOKENS_IA,
        "system": prompt_systeme(),
        "messages": messages,
    }).encode("utf-8")

    requete = urllib.request.Request(
        API_URL, data=corps, method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": cle,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(requete, timeout=DELAI_IA) as r:
            data = json.loads(r.read().decode("utf-8"))
        texte = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        ).strip()
        if not texte:
            raise ErreurIA("L'IA a renvoyé une réponse vide.")
    except urllib.error.HTTPError as e:
        CONVERSATION.pop()
        if e.code == 401:
            raise ErreurIA("Clé API refusée. Refais : ia cle TA_CLE_API")
        if e.code == 429:
            raise ErreurIA("Trop de demandes ou crédit épuisé. Réessaie dans un instant.")
        if e.code >= 500:
            raise ErreurIA("Le service IA est surchargé. Réessaie dans un instant.")
        raise ErreurIA(f"Erreur du service IA (code {e.code}).")
    except (urllib.error.URLError, OSError):
        CONVERSATION.pop()
        raise ErreurIA("Pas de connexion internet : je réponds avec mes connaissances locales.")
    except ErreurIA:
        CONVERSATION.pop()
        raise
    except (ValueError, KeyError):
        CONVERSATION.pop()
        raise ErreurIA("Réponse illisible du service IA.")

    CONVERSATION.append({"role": "assistant", "content": texte})
    return texte


def commande_ia(question):
    """Commandes : ia cle XXX / ia statut / ia reset."""
    m = re.match(r"^\s*ia\s+cl[eé]\s+(\S+)\s*$", question, re.I)
    if m:
        cle = m.group(1).strip("\"'")
        enregistrer_cle_api(cle)
        avert = "" if cle.startswith("sk-ant-") else (
            "\nAttention : une clé commence normalement par sk-ant-. Vérifie avec : ia statut")
        return f"Clé enregistrée ({len(cle)} caractères). Teste avec une question." + avert
    t = normaliser(question)
    if t == "ia statut":
        cle = lire_cle_api()
        if not cle:
            return "IA désactivée (tape : ia cle TA_CLE_API)."
        masque = cle[:7] + "…" + cle[-4:]
        return (f"IA activée. Clé : {masque} ({len(cle)} caractères). "
                f"Modèle : {MODELE_IA}. Messages en contexte : {len(CONVERSATION)}.")
    if t in ("ia reset", "nouvelle conversation"):
        CONVERSATION.clear()
        return "Conversation IA réinitialisée."
    return None


# ---------------- LECTURE D'IMAGES (nécessite la clé API) ----------------
# Commande : image /sdcard/Download/exercice.jpg
# L'image est envoyée au service IA, qui recopie le texte (énoncé, nombres,
# unités) ou décrit un schéma. Chaque image consomme un peu de crédit API.
TYPES_IMAGE = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".gif": "image/gif", ".webp": "image/webp"}
TAILLE_IMAGE_MAX = 4_500_000
CONSIGNE_IMAGE = (
    "Recopie fidèlement tout le texte de cette image (énoncé, nombres, unités, formules), "
    "en français. S'il y a un schéma, décris-le en deux ou trois phrases. "
    "N'invente rien : écris [illisible] pour ce que tu ne peux pas lire.")


def corps_vision(chemin):
    """Prépare la requête (image en base64) ; lève ErreurIA si le fichier est inutilisable."""
    chemin = chemin.strip().strip("\"'")
    ext = os.path.splitext(chemin)[1].lower()
    if ext not in TYPES_IMAGE:
        raise ErreurIA("Format non pris en charge. Utilise jpg, png, gif ou webp.")
    try:
        with open(chemin, "rb") as f:
            donnees = f.read()
    except OSError:
        raise ErreurIA("Je ne trouve pas ce fichier : " + chemin)
    if len(donnees) > TAILLE_IMAGE_MAX:
        raise ErreurIA("Image trop lourde (maximum environ 4,5 Mo). Réduis-la d'abord.")
    return json.dumps({
        "model": MODELE_IA,
        "max_tokens": MAX_TOKENS_IA,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": TYPES_IMAGE[ext],
                                         "data": base64.b64encode(donnees).decode("ascii")}},
            {"type": "text", "text": CONSIGNE_IMAGE}]}],
    }).encode("utf-8")


def lire_image_ia(chemin):
    cle = lire_cle_api()
    if not cle:
        raise ErreurIA("La lecture d'image demande la clé API : ia cle TA_CLE_API")
    corps = corps_vision(chemin)
    requete = urllib.request.Request(
        API_URL, data=corps, method="POST",
        headers={"content-type": "application/json", "x-api-key": cle,
                 "anthropic-version": "2023-06-01"})
    try:
        with urllib.request.urlopen(requete, timeout=DELAI_IA) as r:
            data = json.loads(r.read().decode("utf-8"))
        texte = "".join(b.get("text", "") for b in data.get("content", [])
                        if b.get("type") == "text").strip()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ErreurIA("Clé API refusée. Refais : ia cle TA_CLE_API")
        if e.code == 429:
            raise ErreurIA("Trop de demandes ou crédit épuisé. Réessaie dans un instant.")
        raise ErreurIA(f"Erreur du service IA (code {e.code}).")
    except (urllib.error.URLError, OSError):
        raise ErreurIA("Pas de connexion internet : impossible de lire l'image maintenant.")
    except (ValueError, KeyError):
        raise ErreurIA("Réponse illisible du service IA.")
    if not texte:
        raise ErreurIA("Je n'ai rien pu lire sur cette image.")
    return texte


def commande_image(question):
    """Commande : image CHEMIN.jpg  (ou : photo CHEMIN.png)"""
    m = re.match(r"^\s*(?:image|photo)\s+(\S.*\.(?:jpe?g|png|gif|webp))\s*$", question, re.I)
    if not m:
        return None
    try:
        texte = lire_image_ia(m.group(1))
    except ErreurIA as e:
        return str(e)
    sortie = ("Texte lu sur l'image :\n" + texte +
              "\n\nVérifie surtout les nombres et les unités : une erreur de lecture donne un "
              "faux résultat.")
    calcul = resoudre_physique(texte)
    if calcul:
        sortie += "\n\nCalcul automatique (d'après le texte lu) :\n" + calcul
    return sortie


def devis_en_ligne(question):
    """Devis sur un seul message : fil 10 1000 / prise 2 300 / ampoule 3 1000"""
    parties = [p.strip() for p in re.split(r"[\n/]+", question) if p.strip()]
    if len(parties) < 2 or any(parser_ligne_article(p) is None for p in parties):
        return None
    total, details = calculer_liste_cout(parties)
    lignes = [f"- {n} : {format_nombre(q)} × {format_nombre(p)} F = {format_nombre(m)} F"
              for n, q, p, m in details]
    return "DÉTAIL\n" + "\n".join(lignes) + f"\n\nTOTAL = {format_nombre(total)} F CFA"


# ---------------- MODULE COMMERCIAL (facultatif) ----------------
# Novi présente les services de son concepteur UNIQUEMENT si l'utilisateur
# les demande, ou s'il accepte une proposition (une seule proposition par session).
# Il n'invente jamais d'information : tout ce qui est affiché vient de COM.
COM = {
    "whatsapp": "0192905062",
    "email": "electrotechnique058@gmail.com",
    "adresse": "Abomey, Bénin (adresse exacte à préciser)",
    "zone": "Abomey, Bohicon et leurs environs",
    "services": {
        "elec": "installations électriques",
        "cablage": "câblage électrique et tableaux électriques",
        "maint": "maintenance électrique et industrielle",
        "solaire": "installations solaires",
        "video": "installation de systèmes de vidéosurveillance",
        "eclairage": "éclairage et solutions d'économie d'énergie",
    },
}
_COM_ETAT = {"etat": None, "besoin": "", "propose": 0}
COM_AIDE = ("Pour que le technicien puisse estimer ton besoin, précise :\n"
            "1) le type de travail (installation, panne, câblage, solaire, caméras) ;\n"
            "2) le lieu (ville, quartier) ;\n"
            "3) le type de bâtiment (maison, boutique, atelier, usine) ;\n"
            "4) ce qui existe déjà (compteur, tableau, appareils à alimenter) ;\n"
            "5) le délai souhaité ;\n"
            "6) si possible, des photos.\n"
            "Envoie ensuite ces informations sur WhatsApp : " + COM["whatsapp"] + ".")


def _com_contact():
    return f"WhatsApp Messenger : {COM['whatsapp']}\nE-mail : {COM['email']}"


def _com_services(texte):
    t = _ph_normaliser(texte).replace("-", " ")
    s = COM["services"]
    L = []
    if re.search(r"solaire|panneau|onduleur|batterie", t):
        L.append(s["solaire"])
    if re.search(r"camera|surveillance|video", t):
        L.append(s["video"])
    if re.search(r"tableau|cablage|cabler", t):
        L.append(s["cablage"])
    if re.search(r"panne|maintenance|depann|repar", t):
        L.append(s["maint"])
    if re.search(r"eclairage|lampe|economie d energie", t):
        L.append(s["eclairage"])
    return L or [s["elec"], s["cablage"], s["maint"], s["solaire"], s["video"]]


def _com_presenter(besoin):
    return ("Mon concepteur propose notamment : " + ", ".join(_com_services(besoin)) + ".\n\n"
            f"Zone d'intervention indiquée : {COM['zone']}. Pour une autre localité, c'est à confirmer avec lui.\n"
            "Le tarif dépend du travail à réaliser : je ne peux pas donner de prix fixe.\n\n"
            "Pour décrire ton projet et obtenir une estimation :\n" + _com_contact() + "\n\n"
            "Souhaites-tu que je t'aide à préciser les travaux dont tu as besoin avant de le contacter ?")


def repondre_commerce(question):
    t = _ph_normaliser(question).replace("-", " ")
    t = re.sub(r"\s+", " ", t).strip()
    W = len(t.split())
    st = _COM_ETAT
    if st["etat"]:
        e = st["etat"]
        oui = W <= 6 and re.match(r"(oui|ouais|yes|d accord|dac|ok|okay|vas y|volontiers|je veux bien|bien sur|pourquoi pas|avec plaisir|s il te plait|svp)\b", t)
        non = W <= 8 and re.match(r"(non|nan|pas besoin|pas maintenant|plus tard|ca ira|pas la peine|je ne veux pas|laisse tomber|pas interesse)\b", t)
        if oui:
            st["etat"] = None
            if e == "offre":
                st["etat"] = "aide"
                return _com_presenter(st["besoin"])
            return COM_AIDE
        if non:
            st["etat"] = None
            return ("D'accord, pas de problème ! On continue. 😊" if e == "offre"
                    else "D'accord ! Je reste disponible si tu as d'autres questions.")
        if W <= 4 and re.match(r"(peut etre|hum+|bof|euh+|mmm+|je ne sais pas|je sais pas|pas sur)", t):
            return ("Je n'ai pas bien compris : veux-tu que je te présente les services de mon concepteur ? "
                    "Réponds par oui ou par non." if e == "offre" else
                    "Je n'ai pas bien compris : veux-tu que je t'aide à préciser tes travaux ? "
                    "Réponds par oui ou par non.")
        st["etat"] = None   # autre sujet : on repart normalement

    # Questions précises sur le concepteur : seulement ce qui est enregistré
    if re.search(r"\b(createur|concepteur|edison ahodede)\b", t):
        if re.search(r"diplome|qualification|certif", t):
            return ("Mon concepteur a une formation en électrotechnique. Ses diplômes et qualifications "
                    "précis restent à confirmer : je préfère ne pas les inventer.")
        if re.search(r"reference|clients? satisfaits|avis de clients", t):
            return "Aucune référence de client n'est fournie pour le moment, donc je ne peux en citer aucune."
        if re.search(r"travaux (deja )?(realises|faits|effectues)|realisations|a deja (fait|realise)", t):
            return ("Ses domaines de compétence sont : " + ", ".join(COM["services"].values()) + ". "
                    "Les travaux déjà réalisés restent à confirmer : je ne peux pas citer de chantiers précis.")
        if re.search(r"garantie|garanti", t):
            return ("Aucune garantie particulière n'est confirmée, et je ne peux promettre aucun résultat. "
                    f"Pour les conditions d'une prestation, contacte-le sur WhatsApp : {COM['whatsapp']}.")
        if re.search(r"disponib", t):
            return f"Sa disponibilité est à confirmer directement avec lui sur WhatsApp : {COM['whatsapp']}."
        if re.search(r"zone|intervient|se deplace|deplacement", t):
            return (f"La zone d'intervention indiquée est : {COM['zone']}. "
                    "Pour une autre localité, c'est à confirmer avec lui.")
        if re.search(r"adresse|habite|travaille|atelier|bureau", t):
            return f"Adresse indiquée : {COM['adresse']}."

    direct = re.search(
        r"(coordonnees|contact|numero|whatsapp|e ?mail|mail|joindre|contacter|telephone).{0,40}(createur|concepteur|technicien|edison ahodede)"
        r"|(createur|concepteur|technicien|edison ahodede).{0,40}(coordonnees|contact|numero|whatsapp|mail|joindre|telephone)"
        r"|\b(tes|vos) services\b|services( professionnels)? de (ton|votre) (createur|concepteur)"
        r"|(tarif|prix|devis|combien).{0,40}(createur|concepteur|tes services|ton travail)"
        r"|\b(tes|ton|vos|votre) (tarifs?|prix|devis)\b", t)
    if direct:
        if re.search(r"tarif|prix|devis|combien", t):
            return ("Le tarif dépend du type de travail à réaliser. Pour obtenir une estimation adaptée à ton "
                    f"besoin, tu peux prendre contact avec le technicien sur WhatsApp au {COM['whatsapp']} "
                    "et décrire ton projet.")
        return ("Voici les coordonnées professionnelles de mon concepteur :\n" + _com_contact() +
                f"\nAdresse : {COM['adresse']}\nZone d'intervention : {COM['zone']}\n\n"
                "Il propose notamment : " + ", ".join(COM["services"].values()) + ".\n"
                "Le tarif dépend du travail à réaliser.")

    # L'utilisateur cherche un professionnel : on DEMANDE d'abord son accord (une fois par session)
    cherche = re.search(
        r"\b(je cherche|je recherche|cherche|recherche|besoin d un|besoin d une|il me faut|trouver|recommande|connais tu|connaissez vous|qui peut|ou trouver|quelqu un pour)\b.{0,50}\b(electricien|technicien|installateur|professionnel)\b"
        r"|\b(qui peut|quelqu un pour|ou trouver)\b.{0,40}\b(installer|cabler|depanner|reparer)\b.{0,40}(electri|solaire|camera|surveillance|panneau|tableau)", t)
    savoir = re.search(r"\b(comment|explique|c est quoi|difference|schema|calcul|dimensionn|pourquoi)\b", t)
    if cherche and not savoir and not st["propose"]:
        st["propose"] = 1
        st["etat"] = "offre"
        st["besoin"] = question
        return ("Je peux également te présenter les services professionnels de mon concepteur dans ce domaine. "
                "Souhaites-tu les découvrir ?")
    return None


# ---------------- LYTEB : Lycée Technique et Professionnel de Bohicon ----------------
# Source : base de connaissances fournie par le créateur. Les informations non
# confirmées (date de création, fondateur, premier directeur) ne sont JAMAIS inventées.
LYTEB = {
 "ident": "Le Lycée Technique et Professionnel de Bohicon (LYTEB) est un établissement d'enseignement technique et professionnel situé dans la commune de Bohicon, département du Zou, au Bénin.\nSa mission : contribuer à la formation technique et professionnelle des jeunes, en leur permettant d'acquérir des connaissances théoriques et des compétences pratiques pour poursuivre leurs études ou entrer dans la vie professionnelle.\nJe peux aussi te parler de son histoire et de ses événements (2013, 2023, 2025, 2026). Attention : sa date de création n'a pas été confirmée.",
 "lieu": "Le Lycée Technique et Professionnel de Bohicon (LYTEB) est situé dans la commune de Bohicon, dans le département du Zou, au Bénin.",
 "role": "Le LYTEB participe à la formation technique et professionnelle des jeunes : sa vocation générale est de les aider à acquérir des compétences techniques et pratiques utiles pour poursuivre leurs études ou s'insérer dans l'emploi.\nLes objectifs généraux de l'enseignement technique et professionnel sont :\n• développer les compétences pratiques et techniques des apprenants ;\n• préparer les jeunes à différents métiers ;\n• favoriser la poursuite des études supérieures ;\n• contribuer à la disponibilité d'une main-d'œuvre qualifiée ;\n• participer au développement économique et industriel du Bénin.\nCes objectifs décrivent la vocation générale de ce type d'enseignement ; ce n'est pas une mesure chiffrée des résultats propres au LYTEB.",
 "creation": "La date exacte de création du LYTEB, l'année de son ouverture officielle, son fondateur et le nom de son premier directeur n'ont pas été confirmés dans les sources consultées. Je préfère ne pas les inventer.\nPour une réponse fiable, il faut consulter les archives du lycée, les anciens rapports de rentrée, les documents du ministère chargé de l'enseignement technique ou les textes officiels du Secrétariat général du Gouvernement.\nLes événements de 2013, 2023, 2025 et 2026 ne sont pas des dates de création.",
 "filiere": "Les filières et les diplômes proposés par le LYTEB peuvent changer d'une année à l'autre, et leurs dates d'ouverture n'ont pas été confirmées. Pour connaître l'offre d'une année scolaire précise, il faut consulter les documents officiels du lycée. Une information ancienne n'est pas forcément valable pour l'année en cours.",
 "2013": "En mars 2013, une visite ministérielle a eu lieu au Lycée Technique de Bohicon. La vétusté de certains équipements de travail a été évoquée, et des mesures pour améliorer les équipements des établissements techniques ont été annoncées.\nSource à consulter : une archive en ligne (gouvernement114.rssing.com). Il faut lire le document précis pour confirmer tous les détails.",
 "2023": "Le 31 mars 2023, le ministre chargé des Enseignements secondaire, technique et de la Formation professionnelle, Kouaro Yves Chabi, a visité le Lycée Technique de Bohicon. Cette visite s'inscrivait dans une tournée consacrée à l'enseignement et aux résultats scolaires dans le département du Zou.\nSource : site du ministère (enseignementsecondaire.gouv.bj, actualité n° 222).",
 "2025": "Le décret n° 2025-197 du 23 avril 2025 concerne les statuts-types des collèges et lycées publics au Bénin. C'est une évolution du cadre administratif des établissements publics d'enseignement.\nIl ne faut pas la confondre avec la date de création du LYTEB.",
 "2026": "Le décret n° 2026-071 du 4 mars 2026 est référencé comme portant approbation des statuts du Lycée Technique Professionnel de Bohicon. C'est une étape importante dans le cadre institutionnel de l'établissement.\nCela ne veut pas dire que le lycée a été créé en 2026 : sa date de création historique reste à confirmer.\nSource : sgg.gouv.bj (Secrétariat général du Gouvernement).",
 "decrets": "Le décret n° 2025-197 du 23 avril 2025 concerne les statuts-types des collèges et lycées publics au Bénin. C'est une évolution du cadre administratif des établissements publics d'enseignement.\nIl ne faut pas la confondre avec la date de création du LYTEB.\n\nLe décret n° 2026-071 du 4 mars 2026 est référencé comme portant approbation des statuts du Lycée Technique Professionnel de Bohicon. C'est une étape importante dans le cadre institutionnel de l'établissement.\nCela ne veut pas dire que le lycée a été créé en 2026 : sa date de création historique reste à confirmer.\nSource : sgg.gouv.bj (Secrétariat général du Gouvernement).",
 "histoire": "Histoire du LYTEB (ce qui est documenté) :\n• mars 2013 : visite ministérielle, vétusté de certains équipements évoquée ;\n• 31 mars 2023 : visite du ministre Kouaro Yves Chabi, dans le cadre d'une tournée dans le Zou ;\n• 23 avril 2025 : décret n° 2025-197 sur les statuts-types des lycées publics ;\n• 4 mars 2026 : décret n° 2026-071, référencé comme approuvant les statuts du LYTEB.\nSa date de création, son fondateur et son premier directeur n'ont pas été confirmés : je ne les invente pas. Demande-moi le détail d'une année si tu veux."
}


def repondre_lyteb(question):
    t = _ph_normaliser(question)   # sans accents ni majuscules
    if not re.search(r"\blyteb\b|lycee technique( et professionnel)?( de)? bohicon|lycee( de)? bohicon", t):
        return None
    if re.search(r"2013", t):
        return LYTEB["2013"]
    if re.search(r"2023|chabi", t):
        return LYTEB["2023"]
    if re.search(r"2025", t):
        return LYTEB["2025"]
    if re.search(r"2026", t):
        return LYTEB["2026"]
    if re.search(r"vetuste|equipement", t):
        return LYTEB["2013"]
    if re.search(r"decret|statut|administrat|evolution|institution", t):
        return LYTEB["decrets"]
    if re.search(r"cree|creation|fonde|fondateur|ouvert|ouverture|directeur|premier|premiere", t):
        return LYTEB["creation"]
    if re.search(r"filiere|diplome|formation|serie", t):
        return LYTEB["filiere"]
    if re.search(r"visite|ministre|evenement|histoire|passe|historique", t):
        return LYTEB["histoire"]
    if re.search(r"ou se trouve|ou est|situe|localis|adresse|ville|departement|region", t):
        return LYTEB["lieu"]
    if re.search(r"role|mission|vocation|importance|utilite|objectif|sert|forme", t):
        return LYTEB["role"]
    return LYTEB["ident"]


# ---------------- PHYSIQUE M1 : CINÉMATIQUE (calculs) ----------------
# Source : chapitre M1 du cours de physique (pct_terminal.pdf).
# Types de mouvement : rectiligne uniforme, rectiligne uniformément varié,
# circulaire uniforme. Les calculs sont affichés avec leurs étapes.

PH_UNITES = {"a": "m/s²", "v": "m/s", "v0": "m/s", "t": "s", "d": "m", "x": "m",
             "x0": "m", "R": "m", "w": "rad/s", "T": "s", "N": "Hz"}
PH_NOMS = {"a": "a", "v": "v", "v0": "v₀", "t": "t", "d": "d", "x": "x",
           "x0": "x₀", "R": "R", "w": "ω", "T": "T", "N": "N"}
PH_CLASSE = {"a": "a", "v": "v", "v0": "v", "t": "time", "T": "time", "d": "len",
             "x": "len", "x0": "len", "R": "len", "w": "w", "N": "N"}
PH_SYMBOLES = {"x1": "x", "x2": "x", "v1": "v", "v2": "v", "t1": "t", "t2": "t",
               "a": "a", "v": "v", "v0": "v0", "vo": "v0", "v_0": "v0", "t": "t",
               "x": "x", "x0": "x0", "xo": "x0", "x_0": "x0", "r": "R", "d": "d",
               "n": "N", "w": "w", "omega": "w", "ω": "w"}

PH_UNITES_RE = (r"(m\s*/\s*s\s*(?:\^\s*2|²|2)|m\s*[·.]\s*s\s*(?:\^?\s*-\s*2|⁻²)|m\s*s\s*-\s*2"
                r"|km\s*/\s*h|m\s*/\s*s|m\s*[·.]\s*s\s*(?:\^?\s*-\s*1|⁻¹)|rad\s*/\s*s"
                r"|tr\s*/\s*min|tours?\s*/\s*min|tr\s*/\s*s|hz|km|cm|mm|min|metres?|m"
                r"|heures?|h|secondes?|s)(?![a-z])")
PH_NOMBRE_RE = re.compile(r"(?<![a-zω_\d.])(-?\d+(?:\.\d+)?)\s*(?:" + PH_UNITES_RE + r")?")


def _ph_f(x):
    """Formate un nombre à la française (virgule, 4 chiffres significatifs)."""
    if x == 0:
        return "0"
    if abs(x) >= 1e5 or abs(x) < 1e-3:
        mant, exp = f"{x:.2e}".split("e")
        return f"{mant} × 10^{int(exp)}".replace(".", ",")
    return format(float(f"{x:.4g}"), "g").replace(".", ",")


def _ph_t_quadratique(k):
    """Résout d = ½·a·t² + v0·t pour t (plus petite racine positive)."""
    if k["a"] == 0:
        return k["d"] / k["v0"]
    delta = k["v0"] ** 2 + 2 * k["a"] * k["d"]
    if delta < 0:
        return None
    r = sorted(x for x in ((-k["v0"] + math.sqrt(delta)) / k["a"],
                           (-k["v0"] - math.sqrt(delta)) / k["a"]) if x >= 0)
    return r[0] if r else None


# Relations : (formule, variables, {variable: fonction qui la calcule})
PH_REL_U = [
    ("v = a·t + v₀", ["v", "a", "t", "v0"], {
        "v": lambda k: k["a"] * k["t"] + k["v0"],
        "a": lambda k: (k["v"] - k["v0"]) / k["t"],
        "t": lambda k: (k["v"] - k["v0"]) / k["a"],
        "v0": lambda k: k["v"] - k["a"] * k["t"]}),
    ("v² − v₀² = 2·a·d", ["v", "v0", "a", "d"], {
        "v": lambda k: math.sqrt(k["v0"] ** 2 + 2 * k["a"] * k["d"]),
        "v0": lambda k: math.sqrt(k["v"] ** 2 - 2 * k["a"] * k["d"]),
        "a": lambda k: (k["v"] ** 2 - k["v0"] ** 2) / (2 * k["d"]),
        "d": lambda k: (k["v"] ** 2 - k["v0"] ** 2) / (2 * k["a"])}),
    ("d = (v + v₀)/2 · t", ["d", "v", "v0", "t"], {
        "d": lambda k: (k["v"] + k["v0"]) / 2 * k["t"],
        "v": lambda k: 2 * k["d"] / k["t"] - k["v0"],
        "v0": lambda k: 2 * k["d"] / k["t"] - k["v"],
        "t": lambda k: 2 * k["d"] / (k["v"] + k["v0"])}),
    ("d = ½·a·t² + v₀·t", ["d", "a", "t", "v0"], {
        "d": lambda k: 0.5 * k["a"] * k["t"] ** 2 + k["v0"] * k["t"],
        "a": lambda k: 2 * (k["d"] - k["v0"] * k["t"]) / k["t"] ** 2,
        "v0": lambda k: (k["d"] - 0.5 * k["a"] * k["t"] ** 2) / k["t"],
        "t": _ph_t_quadratique}),
]
PH_REL_C = [
    ("ω = 2π / T", ["w", "T"], {"w": lambda k: 2 * math.pi / k["T"],
                                 "T": lambda k: 2 * math.pi / k["w"]}),
    ("N = 1 / T", ["N", "T"], {"N": lambda k: 1 / k["T"], "T": lambda k: 1 / k["N"]}),
    ("ω = 2π·N", ["w", "N"], {"w": lambda k: 2 * math.pi * k["N"],
                               "N": lambda k: k["w"] / (2 * math.pi)}),
    ("v = R·ω", ["v", "R", "w"], {"v": lambda k: k["R"] * k["w"],
                                   "R": lambda k: k["v"] / k["w"],
                                   "w": lambda k: k["v"] / k["R"]}),
    ("a = v² / R", ["a", "v", "R"], {"a": lambda k: k["v"] ** 2 / k["R"],
                                      "v": lambda k: math.sqrt(k["a"] * k["R"]),
                                      "R": lambda k: k["v"] ** 2 / k["a"]}),
    ("a = R·ω²", ["a", "R", "w"], {"a": lambda k: k["R"] * k["w"] ** 2,
                                    "R": lambda k: k["a"] / k["w"] ** 2,
                                    "w": lambda k: math.sqrt(k["a"] / k["R"])}),
    ("d (arc) = v·t", ["d", "v", "t"], {"d": lambda k: k["v"] * k["t"],
                                         "v": lambda k: k["d"] / k["t"],
                                         "t": lambda k: k["d"] / k["v"]}),
]


def _ph_normaliser(q):
    s = unicodedata.normalize("NFD", q.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("−", "-").replace("–", "-").replace("'", " ").replace("’", " ")
    s = s.replace(",", ".")
    # Nettoyage du format LaTeX : \text{ m}, \cdot, ^{-1}, x_1 ...
    s = re.sub(r"\\text\{\s*([^}]*)\}", r" \1 ", s)
    s = s.replace("\\cdot", "·")
    s = re.sub(r"\^\{\s*(-?\d+)\s*\}", r"^\1", s)
    s = re.sub(r"\\[a-z]+", " ", s)
    s = re.sub(r"([a-z])_\{?(\d)\}?", r"\1\2", s)
    return re.sub(r"\s+", " ", s)


def _ph_lire(s):
    """Lit les données chiffrées de l'énoncé : {variable: valeur en unités S.I.}."""
    K = {}
    prev = 0
    for m in PH_NOMBRE_RE.finditer(s):
        lab = s[max(prev, m.start() - 34):m.start()]
        prev = m.end()
        ul = re.sub(r"\s+", "", m.group(2) or "")
        val = float(m.group(1))
        kind = None
        if re.fullmatch(r"m/s(\^2|²|2)|m[·.]s(\^?-2|⁻²)|ms-2", ul):
            kind = "a"
        elif ul == "km/h":
            kind, val = "v", val / 3.6
        elif re.fullmatch(r"m/s|m[·.]s(\^?-1|⁻¹)", ul):
            kind = "v"
        elif ul == "rad/s":
            kind = "w"
        elif re.fullmatch(r"(tr|tours?)/min", ul):
            kind, val = "w", val * 2 * math.pi / 60
        elif ul == "tr/s":
            kind, val = "w", val * 2 * math.pi
        elif ul == "hz":
            kind = "N"
        elif re.fullmatch(r"km|cm|mm|m|metres?", ul):
            kind = "len"
            val *= 1000 if ul == "km" else 0.01 if ul == "cm" else 0.001 if ul == "mm" else 1
        elif re.fullmatch(r"min|h|heures?|s|secondes?", ul):
            kind = "time"
            val *= 60 if ul == "min" else 3600 if ul.startswith("h") else 1
        sm = re.search(r"([a-zω]+(?:_?\d)?)\s*=\s*\+?\s*$", lab)
        sym = PH_SYMBOLES.get(sm.group(1)) if sm else None
        sbx = re.search(r"([a-zω]+_[a-zω]+)\s*=\s*\+?\s*$", lab)
        if sbx and sbx.group(1) not in PH_SYMBOLES:
            continue
        cle = None
        if sym and (not kind or PH_CLASSE[sym] == kind):
            cle = sym
        elif kind == "a":
            cle = "a"
            if re.search(r"deceler|freinage|ralenti|retard", lab):
                val = -abs(val)
        elif kind == "v":
            if re.search(r"final|atteint|au bout", lab):
                cle = "v"
            elif re.search(r"initial|\bv0\b|v_0|\bvo\b|depart|roule|lance|part avec|anime", lab):
                cle = "v0"
            else:
                cle = "v"
        elif kind in ("w", "N"):
            cle = kind
        elif kind == "len":
            if re.search(r"rayon", lab):
                cle = "R"
            elif re.search(r"initial|\bx0\b|x_0|origine", lab):
                cle = "x0"
            elif re.search(r"abscisse|position", lab):
                cle = "x"
            else:
                cle = "d"
        elif kind == "time":
            if re.search(r"\bt0\b|t_0", lab):
                continue
            cle = "T" if re.search(r"periode", lab) else "t"
        if cle and cle not in K:
            K[cle] = val
    return K


def _ph_cible(s, K):
    """Devine la grandeur demandée (celle qui est nommée mais pas donnée)."""
    w = re.sub(r"vitesse angulaire|pulsation|omega|ω", "zzomegazz", s)
    m = re.search(r"\b(?:calcul\w*|determine[rz]?|trouve[rz]?|donne[rz]?|quel(?:le)?s?|combien|exprime[rz]?)\b", w)
    if not m and "?" not in w:
        return None
    seg = w[m.start():] if m else w
    candidats = [("w", r"zzomegazz"), ("a", r"acceleration|decelera"),
                 ("v", r"vitesse(?! initiale| de depart)"),
                 ("t", r"duree|combien de temps|temps|date|instant"),
                 ("d", r"distance|parcour|deplacement|longueur"),
                 ("x", r"abscisse|position"), ("R", r"rayon"), ("T", r"periode"),
                 ("N", r"frequence|nombre de tours")]
    trouves = []
    for cle, motif in candidats:
        mm = re.search(motif, seg)
        if cle not in K and mm:
            trouves.append((mm.start(), cle))
    return min(trouves)[1] if trouves else None


def _ph_poly(texte):
    s = re.sub(r"\s+", "", texte).replace("²", "^2").replace("*", "").replace("×", "")
    s = s.replace(",", ".").replace("−", "-")
    c = [0.0, 0.0, 0.0]
    ok = False
    for m in re.finditer(r"([+-]?)(\d*\.?\d*)(t(\^2)?)?", s):
        if m.group(0) == "" or m.group(2) == ".":
            continue
        if m.group(2) == "" and not m.group(3):
            continue
        v = 1.0 if m.group(2) == "" else float(m.group(2))
        d = (2 if m.group(4) else 1) if m.group(3) else 0
        c[d] += (-1 if m.group(1) == "-" else 1) * v
        ok = True
    return c if ok else None


def _ph_eval(c, t):
    return c[2] * t * t + c[1] * t + c[0]


def _ph_poly_txt(c):
    r = ""
    for d, u in ((2, "·t²"), (1, "·t"), (0, "")):
        v = c[d]
        if not v:
            continue
        r += (" − " if v < 0 else " + ") if r else ("−" if v < 0 else "")
        r += _ph_f(abs(v)) + u
    return r or "0"


def _ph_racines(c):
    c0, c1, c2 = c
    if abs(c2) < 1e-12:
        return [] if abs(c1) < 1e-12 else [-c0 / c1]
    delta = c1 * c1 - 4 * c2 * c0
    if delta < 0:
        return []
    return [(-c1 + math.sqrt(delta)) / (2 * c2), (-c1 - math.sqrt(delta)) / (2 * c2)]


def resoudre_physique(question):
    """Résout un petit exercice de cinématique (M1). Retourne None si ce n'est pas le cas."""
    s = _ph_normaliser(question)

    # 1) Rencontre de deux mobiles : « rencontre x1 = ... et x2 = ... »
    if "rencontre" in s and s.count("=") >= 2:
        reste = re.sub(r"^.*?rencontre[^x]*", "", s)
        morceaux = [p.split("=")[-1] for p in re.split(r"\s+et\s+|;", reste)]
        p = [_ph_poly(m) for m in morceaux]
        if len(p) >= 2 and p[0] and p[1]:
            d = [p[0][i] - p[1][i] for i in range(3)]
            r = sorted(t for t in _ph_racines(d) if t >= 0)
            if not r:
                return ("Les deux mobiles ne se rencontrent pas (l'équation x₁(t) = x₂(t) "
                        "n'a pas de solution positive).")
            sol = " ; ".join(f"t = {_ph_f(t)} s (x = {_ph_f(_ph_eval(p[0], t))} m)" for t in r)
            return (f"Rencontre : on écrit x₁(t) = x₂(t), soit {_ph_poly_txt(d)} = 0.\n"
                    f"Solution(s) positive(s) : {sol}.\n"
                    "Si plusieurs solutions existent, garde celle qui est dans l'intervalle "
                    "de temps de l'énoncé.")

    # 2) x = A cos ωt ; y = A sin ωt
    if re.search(r"cos\s*\(?\s*(ω|omega|w)\s*\*?\s*t", s) and "sin" in s:
        A = re.search(r"\ba\s*=\s*(\d+\.?\d*)\s*(cm|mm|m)\b", s)
        W = re.search(r"(?:ω|omega|\bw)\s*=\s*(\d+\.?\d*)", s)
        if A and W:
            a = float(A.group(1)) * (0.01 if A.group(2) == "cm" else 0.001 if A.group(2) == "mm" else 1)
            w = float(W.group(1))
            return (f"x = A·cos ωt et y = A·sin ωt donnent x² + y² = A² : la trajectoire est un "
                    f"cercle de rayon A = {_ph_f(a)} m (mouvement circulaire uniforme).\n"
                    f"Vitesse : vx = −Aω·sin ωt, vy = Aω·cos ωt, donc v = Aω = {_ph_f(a * w)} m/s (constante).\n"
                    f"Accélération : a = Aω² = {_ph_f(a * w * w)} m/s² (constante), "
                    "vecteur dirigé vers le centre (centripète).")

    # 3) Équation horaire polynomiale : vitesse et accélération par dérivation
    ix = re.search(r"\bx\s*(?:\(t\))?\s*=", s)
    if ix and re.search(r"vitesse|acceleration|deriv", s):
        corps = re.match(r"[0-9.+\-*×t^²\s]+", s[ix.end():])
        c = _ph_poly(corps.group(0)) if corps else None
        if c and (c[1] or c[2]):
            dc = [c[1], 2 * c[2], 0]
            r = (f"Équation horaire : x(t) = {_ph_poly_txt(c)}.\n"
                 f"Vitesse (dérivée) : v(t) = dx/dt = {_ph_poly_txt(dc)}.\n"
                 f"Accélération (dérivée seconde) : a = d²x/dt² = {_ph_f(2 * c[2])} (constante).")
            tv = re.search(r"(?:t|date|instant)\s*=\s*(-?\d+\.?\d*)", s)
            if tv:
                t = float(tv.group(1))
                r += (f"\nÀ t = {_ph_f(t)} s : x = {_ph_f(_ph_eval(c, t))} m ; "
                      f"v = {_ph_f(_ph_eval(dc, t))} m/s ; a = {_ph_f(2 * c[2])} m/s².")
            return r

    # Garde-fou : plusieurs questions ou plusieurs mobiles -> on demande de séparer.
    if (len(re.findall(r"\b\d\s*°|(?<![(\w])[a-d]\)", s)) >= 2
            or re.search(r"deuxieme mobile|second mobile|mobile m ?'|autre mobile", s)):
        return ("Cet exercice contient plusieurs questions et plusieurs mobiles : si je le traite "
                "d'un seul bloc, je risque de mélanger les données et de me tromper.\n"
                "Envoie-le question par question, en rappelant les données utiles. Exemple : "
                "« Un mobile part de x0 = -0,5 m avec v0 = -1 m/s et passe au point x1 = 5 m "
                "avec v1 = 4,7 m/s. Calcule l'accélération. »\n"
                "Pour une rencontre, donne les deux équations : "
                "« rencontre x1 = 0,96t² - t - 0,5 et x2 = 4t - 3 ».")

    # 4) Résolution générale par relations (MRU, MRUV, MCU)
    if len(s) > 600 or re.search(r"machine|alternateur|moteur|synchrone|rotor|inducteur|transformateur|bobine|\bpoles?\b", s):
        return None
    K = _ph_lire(s)
    if not K:
        return None
    cible = _ph_cible(s, K)
    if not cible:
        return None
    circ = (re.search(r"circulaire|cercle|rayon|\btours?\b|periode|centripete|angulaire|pulsation", s)
            or any(k in K for k in ("w", "R", "T", "N")) or cible in ("w", "R", "T", "N"))
    if circ:
        mode = "C"
    elif "a" in K or cible == "a" or re.search(r"uniformement|acceleration|freine|decelera|demarre", s):
        mode = "U"
    else:
        mode = "R"
    K0 = dict(K)
    notes, etapes = [], []
    if not circ:
        if mode == "R":
            K["a"] = 0.0
            if "v" in K and "v0" not in K:
                K["v0"] = K["v"]
        if re.search(r"part (du|de l) ?(repos|arret)|partant du repos|sans vitesse initiale|"
                     r"depart arrete|au repos|demarre", s) and "v0" not in K:
            K["v0"] = 0.0
        if re.search(r"s arrete|jusqu a l arret|s immobilise|arret complet", s) \
                and "v" not in K and cible != "v":
            K["v"] = 0.0
        if "x" in K and "x0" in K and "d" not in K:
            K["d"] = K["x"] - K["x0"]
    relations = PH_REL_C if circ else PH_REL_U
    but = "d" if cible == "x" else cible
    suppose = False
    for _ in range(12):
        if but in K:
            break
        progres = False
        for formule, variables, solveurs in relations:
            inconnues = [v for v in variables if v not in K]
            if len(inconnues) != 1:
                continue
            u = inconnues[0]
            try:
                val = solveurs[u](K)
            except (ZeroDivisionError, ValueError):
                continue
            if val is None or not math.isfinite(val):
                continue
            K[u] = val
            etapes.append((formule, u, val, [v for v in variables if v != u]))
            progres = True
            if but in K:
                break
        if not progres:
            if (not circ and "v0" not in K and not suppose
                    and re.search(r"lance|part|demarre|depart|repos|catapult|lache|chute", s)):
                K["v0"] = 0.0
                suppose = True
                notes.append("J'ai supposé un départ du repos (v₀ = 0). Dis-moi si ce n'est pas le cas.")
                continue
            break
    donnees = " ; ".join(f"{PH_NOMS[k]} = {_ph_f(v)} {PH_UNITES[k]}" for k, v in K0.items())
    if but not in K:
        if len(K0) < 2:
            return None
        return (f"Je n'ai pas assez de données pour calculer {PH_NOMS[cible]}.\n"
                f"Données lues : {donnees}.\n"
                "Précise par exemple l'accélération, la vitesse initiale, la durée ou la distance.")
    final = K[but]
    if cible == "x":
        x0 = K.get("x0", 0.0)
        if "x0" not in K:
            notes.append("J'ai pris l'origine x₀ = 0.")
        final = x0 + K["d"]
        etapes.append(("x = x₀ + d", "x", final, ["x0", "d"]))
    noms = {"C": "circulaire uniforme", "U": "rectiligne uniformément varié (a constante)",
            "R": "rectiligne uniforme (a = 0)"}
    sortie = f"Données : {donnees}\nMouvement : {noms[mode]}."
    for formule, u, val, autres in etapes:
        sub = " ; ".join(f"{PH_NOMS[z]} = {_ph_f(K[z])}" for z in autres if z in K)
        sortie += f"\n• {formule} → {PH_NOMS[u]} = {_ph_f(val)} {PH_UNITES[u]}"
        sortie += f" (avec {sub})" if sub else ""
    sortie += f"\nRéponse : {PH_NOMS[cible]} = {_ph_f(final)} {PH_UNITES[cible]}."
    if notes:
        sortie += "\n" + " ".join(notes)
    return sortie + "\nVérifie toujours les unités (S.I.)."



def traiter(question):
    appr = commande_apprendre(question)
    if appr is not None:
        return appr

    commerce = repondre_commerce(question)
    if commerce is not None:
        return commerce

    phys = resoudre_physique(question)
    if phys is not None:
        return phys

    lyteb = repondre_lyteb(question)
    if lyteb is not None:
        return lyteb

    cmd = commande_ia(question)
    if cmd is not None:
        return cmd

    img = commande_image(question)
    if img is not None:
        return img

    devis = devis_en_ligne(question)
    if devis:
        return devis

    # Nouvelle couche : français naturel.
    naturel = reponse_naturelle(question)
    if naturel is not None:
        return naturel

    # Compatibilité avec les anciennes commandes V6.
    t = normaliser(question)

    if t in ("quitter", "exit", "quit"):
        return "__QUIT__"

    if t == "mémoire" or t == "memoire":
        return afficher_memoire()

    if t.startswith("calc "):
        expr = question[5:].strip()
        try:
            resultat = calculer(expr)
            return f"{expr} = {format_nombre(resultat)}"
        except Exception as e:
            return f"Je ne peux pas effectuer ce calcul : {e}"

    m = re.match(r"^\s*retiens\s+que\s+(.+?)\s*=\s*(.+)\s*$", question, re.I)
    if m:
        cle, valeur = m.group(1), m.group(2)
        retenir(cle, valeur)
        return f"D'accord. Je retiens : {cle.strip()} = {valeur.strip()}."

    if t in ("heure", "date", "quelle heure est il", "quelle heure est-il"):
        return datetime.now().strftime("Nous sommes le %d/%m/%Y à %H:%M:%S.")

    # Ancienne recherche par mots-clés en dernier recours.
    if not ia_disponible():
        connaissance = chercher_connaissance(question)
        if connaissance:
            return connaissance

    if re.fullmatch(r"[0-9\s\+\-\*\/\%\(\)\.,×÷\^]+", question.strip()):
        try:
            resultat = calculer(question)
            return f"{question} = {format_nombre(resultat)}"
        except Exception:
            pass

    erreur = ""
    if ia_disponible():
        try:
            return demander_ia(question)
        except ErreurIA as e:
            erreur = str(e) + "\n\n"
            connaissance = chercher_connaissance(question)
            if connaissance:
                return erreur + connaissance

    return erreur + (
        "Je n'ai pas encore assez de connaissances pour répondre à cette demande. "
        "Essaie de me l'expliquer autrement"
        + ("." if ia_disponible() else ", ou active l'IA avec : ia cle TA_CLE_API.")
    )


# ---------------- INTERFACE GRAPHIQUE ANDROID / PYDROID 3 ----------------

try:
    import tkinter as tk
    from tkinter import scrolledtext, messagebox
except ImportError:
    tk = None


class EdisonGUI:
    """Interface légère, tactile et compatible avec Tkinter/Pydroid 3."""

    def __init__(self, root):
        self.root = root
        self.occupe = False
        self.file = queue.Queue()
        self.voice_file = queue.Queue()
        self.marque_attente = "end"
        self.voix_active = True
        initialiser_voix()
        self.root.title("Novi IA")
        self.root.geometry("420x720")
        self.root.minsize(320, 500)

        self.font_main = ("DejaVu Sans", 12)
        self.font_small = ("DejaVu Sans", 10)
        self.font_saisie = ("DejaVu Sans", 15)   # zone de saisie plus grande
        self.font_title = ("DejaVu Sans", 18, "bold")

        # Couleurs sobres pour une utilisation confortable sur téléphone.
        self.bg = "#101418"
        self.panel = "#1a2026"
        self.text = "#f2f4f5"
        self.muted = "#aeb7bf"
        self.accent = "#2f8cff"
        self.user_bg = "#26313b"
        self.ai_bg = "#182a22"

        self.root.configure(bg=self.bg)

        # En-tête
        header = tk.Frame(root, bg=self.panel, padx=12, pady=10)
        header.pack(fill="x")

        title = tk.Label(
            header,
            text="NOVI",
            font=self.font_title,
            bg=self.panel,
            fg=self.text
        )
        title.pack(side="left")

        subtitle = tk.Label(
            header,
            text="Assistant électrotechnique",
            font=self.font_small,
            bg=self.panel,
            fg=self.muted
        )
        subtitle.pack(side="left", padx=(10, 0), pady=(7, 0))

        # Zone de conversation
        self.chat = scrolledtext.ScrolledText(
            root,
            wrap=tk.WORD,
            font=self.font_main,
            bg=self.bg,
            fg=self.text,
            insertbackground=self.text,
            relief="flat",
            padx=10,
            pady=10
        )
        self.chat.configure(state="disabled")

        self.chat.tag_configure(
            "user",
            background=self.user_bg,
            foreground=self.text,
            lmargin1=8,
            lmargin2=8,
            rmargin=8,
            spacing1=5,
            spacing3=8
        )
        self.chat.tag_configure(
            "edison",
            background=self.ai_bg,
            foreground=self.text,
            lmargin1=8,
            lmargin2=8,
            rmargin=8,
            spacing1=5,
            spacing3=8
        )
        self.chat.tag_configure(
            "system",
            foreground=self.muted,
            font=self.font_small
        )

        # Suggestions rapides : boutons assez grands pour le tactile.
        quick = tk.Frame(root, bg=self.bg)

        for label, text_cmd in [
            ("Électricité", "Explique-moi la loi d'Ohm"),
            ("Calcul", "Combien font 25 fois 17 ?"),
            ("Mémoire", "Que sais-tu de moi ?"),
        ]:
            b = tk.Button(
                quick,
                text=label,
                command=lambda c=text_cmd: self.envoyer_texte(c),
                font=self.font_small,
                bg=self.panel,
                fg=self.text,
                activebackground=self.accent,
                activeforeground="white",
                relief="flat",
                padx=8,
                pady=8
            )
            b.pack(side="left", expand=True, fill="x", padx=2)

        # Commandes vocales
        voice = tk.Frame(root, bg=self.bg)

        self.voice_button = tk.Button(
            voice,
            text="🎤 Parler",
            command=self.demarrer_ecoute,
            font=self.font_small,
            bg=self.panel,
            fg=self.text,
            activebackground=self.accent,
            activeforeground="white",
            relief="flat",
            padx=10,
            pady=8
        )
        self.voice_button.pack(side="left", expand=True, fill="x", padx=2)

        self.voice_toggle = tk.Button(
            voice,
            text="🔊 Voix : ON",
            command=self.basculer_voix,
            font=self.font_small,
            bg=self.panel,
            fg=self.text,
            activebackground=self.accent,
            activeforeground="white",
            relief="flat",
            padx=10,
            pady=8
        )
        self.voice_toggle.pack(side="left", expand=True, fill="x", padx=2)

        self.appel_actif = False
        self.appel_file = queue.Queue()
        self.appel_button = tk.Button(
            voice,
            text="📞 Appel",
            command=self.basculer_appel,
            font=self.font_small,
            bg=self.panel,
            fg=self.text,
            activebackground=self.accent,
            activeforeground="white",
            relief="flat",
            padx=10,
            pady=8
        )
        self.appel_button.pack(side="left", expand=True, fill="x", padx=2)

        voice.pack(side="bottom", fill="x", padx=8, pady=3)

        # Zone de saisie
        bottom = tk.Frame(root, bg=self.panel, padx=8, pady=8)

        self.entry = tk.Entry(
            bottom,
            font=self.font_saisie,
            bg="#222a31",
            fg=self.text,
            insertbackground=self.text,
            relief="flat"
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=16, padx=(0, 6))
        self.entry.bind("<Return>", self.envoyer)

        send = tk.Button(
            bottom,
            text="OK",
            command=self.envoyer,
            font=("TkDefaultFont", 13, "bold"),
            bg=self.accent,
            fg="white",
            activebackground=self.accent,
            activeforeground="white",
            relief="flat",
            width=5,
            pady=5
        )
        send.pack(side="right")

        # Ordre d'empilement : saisie, commandes vocales, suggestions, puis conversation.
        bottom.pack(side="bottom", fill="x")
        voice.pack(side="bottom", fill="x", padx=8, pady=3)
        quick.pack(side="bottom", fill="x", padx=8, pady=3)
        self.chat.pack(side="top", fill="both", expand=True, padx=8, pady=(8, 4))

        # Message d'accueil.
        self.afficher(
            "Novi",
            "Bonjour ! Tu peux me parler normalement. "
            "Essaie par exemple : « Combien font 25 fois 17 ? » "
            "ou « Explique-moi comment diagnostiquer un moteur ». "
            "Tu peux aussi appuyer sur « 🎤 Parler » pour lui parler.",
            "edison"
        )

        if not ia_disponible():
            self.afficher(
                "Novi",
                "Mode IA désactivé. Pour l'activer, tape : ia cle TA_CLE_API",
                "system"
            )

        if initialiser_voix() is None:
            self.voice_button.configure(
                text="🎤 Voix indisponible",
                state="disabled"
            )
            self.voice_toggle.configure(
                text="🔇 Voix indisponible",
                state="disabled"
            )

        self.entry.focus_set()

    def afficher(self, auteur, texte, tag):
        self.chat.configure(state="normal")
        self.chat.insert("end", f"{auteur}\n", tag)
        self.chat.insert("end", f"{texte}\n\n", tag)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def basculer_voix(self):
        self.voix_active = not self.voix_active
        self.voice_toggle.configure(
            text="🔊 Voix : ON" if self.voix_active else "🔇 Voix : OFF"
        )
        if self.voix_active:
            parler("La voix de Novi est activée.")
        else:
            # On ne lit volontairement rien ici : le bouton sert justement à couper la voix.
            pass


    # ---------------- appel vocal en direct ----------------
    def basculer_appel(self):
        if self.appel_actif:
            self.appel_actif = False
            self.appel_button.configure(text="📞 Appel")
            self.afficher("Système", "Appel terminé (la fenêtre d'écoute en cours se fermera d'elle-même).", "system")
            return
        if initialiser_voix() is None:
            self.afficher("Système", "L'appel vocal demande la voix Android (SL4A / androidhelper), non disponible ici.", "system")
            return
        self.appel_actif = True
        self.appel_button.configure(text="📵 Raccrocher")
        self.afficher("Système", "Appel vocal démarré. Dis « au revoir » ou touche « Raccrocher » pour finir.", "system")
        threading.Thread(target=self._boucle_appel, daemon=True).start()
        self.root.after(100, self._verifier_appel)

    def _boucle_appel(self):
        echecs = 0
        parler("Je t'écoute.")
        attendre_fin_parole("Je t'écoute.")
        while self.appel_actif:
            texte, erreur = ecouter_voix()
            if not self.appel_actif:
                break
            if erreur or not texte:
                echecs += 1
                if echecs >= 3:
                    self.appel_file.put(("fin", "Appel terminé : je n'entends plus rien."))
                    return
                continue
            echecs = 0
            if RACCROCHER.match(texte):
                self.appel_file.put(("fin", "D'accord, à bientôt !"))
                parler("D'accord, à bientôt !")
                return
            self.appel_file.put(("toi", texte))
            try:
                reponse = traiter(texte)
            except Exception as exc:
                reponse = f"Une erreur est survenue. Détail technique : {exc}"
            if reponse == "__QUIT__":
                self.appel_file.put(("fin", "Appel terminé."))
                return
            self.appel_file.put(("novi", reponse))
            voix = texte_pour_la_voix(reponse)
            parler(voix)
            attendre_fin_parole(voix)
        self.appel_file.put(("fin", None))

    def _verifier_appel(self):
        try:
            while True:
                genre, texte = self.appel_file.get_nowait()
                if genre == "toi":
                    self.afficher("Toi", texte, "user")
                elif genre == "novi":
                    self.afficher("Novi", texte, "edison")
                elif genre == "fin":
                    self.appel_actif = False
                    self.appel_button.configure(text="📞 Appel")
                    if texte:
                        self.afficher("Système", texte, "system")
        except queue.Empty:
            pass
        if self.appel_actif or not self.appel_file.empty():
            self.root.after(100, self._verifier_appel)

    def demarrer_ecoute(self):
        if self.occupe:
            return

        # La reconnaissance vocale Android est bloquante : on la lance dans un thread.
        self.voice_button.configure(state="disabled", text="🎤 Écoute…")
        self.afficher("Système", "Novi écoute… parle maintenant.", "system")
        threading.Thread(target=self._ecoute_vocale, daemon=True).start()

    def _ecoute_vocale(self):
        texte, erreur = ecouter_voix()
        self.voice_file.put((texte, erreur))
        self.root.after(100, self._verifier_vocal)

    def _verifier_vocal(self):
        try:
            texte, erreur = self.voice_file.get_nowait()
        except queue.Empty:
            self.root.after(100, self._verifier_vocal)
            return

        self.voice_button.configure(state="normal", text="🎤 Parler")

        if erreur:
            self.afficher("Système", erreur, "system")
            return

        if texte:
            self.envoyer_texte(texte)

    def envoyer(self, event=None):
        texte = self.entry.get().strip()
        if texte:
            self.envoyer_texte(texte)
        return "break"

    def envoyer_texte(self, texte):
        if self.occupe:
            return
        self.entry.delete(0, "end")

        affiche = texte
        if re.match(r"^\s*ia\s+cl[eé]\s+", texte, re.I):
            affiche = "ia cle ••••••••"   # ne jamais afficher la clé
        self.afficher("Toi", affiche, "user")

        self.occupe = True
        self.chat.configure(state="normal")
        self.marque_attente = self.chat.index("end-1c")
        self.chat.insert("end", "Novi réfléchit…\n\n", "system")
        self.chat.configure(state="disabled")
        self.chat.see("end")

        threading.Thread(target=self._travail, args=(texte,), daemon=True).start()
        self.root.after(100, self._verifier)

    def _travail(self, texte):
        try:
            reponse = traiter(texte)
        except Exception as exc:
            reponse = f"Une erreur est survenue. Détail technique : {exc}"
        self.file.put(reponse)

    def _verifier(self):
        try:
            reponse = self.file.get_nowait()
        except queue.Empty:
            self.root.after(100, self._verifier)
            return

        self.occupe = False
        self.chat.configure(state="normal")
        self.chat.delete(self.marque_attente, "end")
        self.chat.configure(state="disabled")

        if reponse == "__QUIT__":
            self.root.destroy()
            return
        self.afficher("Novi", reponse, "edison")
        if self.voix_active:
            threading.Thread(target=parler, args=(reponse,), daemon=True).start()


def lancer_edison():
    if tk is None:
        raise RuntimeError(
            "Tkinter n'est pas disponible dans cet environnement Python. "
            "Sur Pydroid 3, installe/active le support Tkinter si nécessaire."
        )

    root = tk.Tk()
    EdisonGUI(root)
    root.mainloop()


if __name__ == "__main__":
    lancer_edison()
