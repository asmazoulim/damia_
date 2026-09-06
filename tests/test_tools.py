"""
tests/test_tools.py — Tests unitaires de la logique metier.

Ni LLM, ni serveur MCP : seulement tools.py et la base DuckDB. Ces tests
s'executent en quelques secondes et constituent le filet de securite pour toute
modification du routage.

Ce qu'ils protegent en priorite, ce sont les garanties FAIL-CLOSED — la promesse
« aucun chiffre invente ». Un test qui echoue ici signifie qu'une question hors
perimetre a recu un chiffre, ou qu'un filtre a ete ignore silencieusement : ce
sont les seules regressions vraiment graves de ce projet.

    python -m pytest tests/test_tools.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.config import CHEMIN_DB
from src import tools
from src.formatage import bloc_table, extraire_table, format_valeur, montant
from src.normalisation import (contient_expression, meilleur_match, radical,
                               sans_accent, tokens)

# Les tests qui frappent la base sont ignores si elle n'est pas presente
# (poste de dev sans donnees) ; ceux sur le routage tournent toujours.
besoin_base = pytest.mark.skipif(not Path(CHEMIN_DB).exists(),
                                 reason="base DuckDB absente (python src/data_load.py)")


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
class TestNormalisation:
    @pytest.mark.parametrize("entree,attendu", [
        ("Île-de-France", "ile de france"),
        ("PROVENCE-ALPES", "provence alpes"),
        ("l'optique", "l optique"),
        ("  Espaces   multiples ", "espaces multiples"),
        ("Médecine_Ville", "medecine ville"),
    ])
    def test_sans_accent(self, entree, attendu):
        assert sans_accent(entree) == attendu

    @pytest.mark.parametrize("mot,attendu", [
        ("dentaires", "dentaire"), ("verres", "verre"), ("montures", "monture"),
        ("os", "os"),        # trop court : la marque n'est pas coupee
        ("vis", "vis"),
        ("optique", "optique"),
    ])
    def test_radical(self, mot, attendu):
        assert radical(mot) == attendu

    def test_tokens_radicalise(self):
        assert tokens("Prothèses dentaires") == ["prothese", "dentaire"]


class TestContientExpression:
    """Le coeur du routage : ces cas encodent des bugs reellement rencontres."""

    @pytest.mark.parametrize("cle,texte", [
        ("dentaire", "depenses dentaires"),      # pluriel tolere
        ("dentaire", "dentaire"),
        ("dents", "mal aux dents"),
        ("soins dentaires", "les soins dentaires"),
        ("verre correcteur", "verres correcteurs"),
    ])
    def test_matche(self, cle, texte):
        assert contient_expression(cle, texte)

    @pytest.mark.parametrize("cle,texte", [
        ("dentaire", "dentifrice"),      # le faux positif que le code d'origine signalait
        ("prothese", "audioprothese"),   # renvoyait le dentaire pour l'audioprothese
        ("irm", "infirmier"),            # renvoyait l'imagerie pour les infirmiers
        ("radio", "radiographie"),
        ("optique", "synoptique"),       # sous-chaine, mais pas le meme mot
        ("dent", "president"),
    ])
    def test_ne_matche_pas(self, cle, texte):
        assert not contient_expression(cle, texte)

    def test_meilleur_match_prefere_le_plus_specifique(self):
        cles = ["prothese", "protheses auditives"]
        assert meilleur_match(cles, "protheses auditives") == "protheses auditives"

    def test_meilleur_match_sans_correspondance(self):
        assert meilleur_match(["optique", "dentaire"], "licorne") is None


# ---------------------------------------------------------------------------
# Routage des postes et decoupages
# ---------------------------------------------------------------------------
class TestRoutagePoste:
    @pytest.mark.parametrize("terme", ["optique", "dentaire", "pharmacie",
                                       "audioprothese", "hospitalisation"])
    def test_postes_connus_sont_reconnus(self, terme):
        _, _, reconnu = tools._clause_poste(terme)
        assert reconnu, f"« {terme} » devrait etre reconnu"

    @pytest.mark.parametrize("terme", ["licorne", "dentifrice", "poste inexistant xyz"])
    def test_postes_inconnus_sont_refuses(self, terme):
        _, _, reconnu = tools._clause_poste(terme)
        assert not reconnu, f"« {terme} » ne devrait PAS etre reconnu"

    @pytest.mark.parametrize("terme", ["mutuelle", "amc", "chirurgie esthetique"])
    def test_hors_perimetre_est_refuse(self, terme):
        _, _, reconnu = tools._clause_poste(terme)
        assert not reconnu

    @pytest.mark.parametrize("terme,poste_attendu", [
        ("audioprothese", "Audioprothèse"),
        ("protheses auditives", "Audioprothèse"),
        ("infirmier", "Paramédical"),
        ("soins infirmiers", "Paramédical"),
        ("radiographie", "Imagerie"),
    ])
    def test_non_regression_faux_positifs(self, terme, poste_attendu):
        """Ces termes etaient mal routes par le matching par sous-chaine :
        audioprothese -> dentaire, infirmier -> imagerie (via « irm »)."""
        assert tools._poste_dico(terme) == poste_attendu


class TestRoutageDecoupage:
    @pytest.mark.parametrize("terme,groupe", [
        ("verres", "Verres"),
        ("montures", "Montures"),
        ("lentilles", "Lentilles"),
        ("protheses dentaires", "Prothèses dentaires"),
        ("orthodontie", "Orthodontie"),
    ])
    def test_decoupages_reconnus(self, terme, groupe):
        assert tools._decoupage_match(terme)[0] == groupe

    def test_decoupage_ne_capture_pas_un_poste_plus_specifique(self):
        """« prothèses auditives » matche la cle generique « prothese » du
        decoupage dentaire ; le poste audioprothese doit l'emporter."""
        _, _, cible_txt, refus = tools._resoudre_cible(poste="protheses auditives")
        assert not refus
        assert "dentaire" not in cible_txt.lower()

    def test_promotion_decoupage_depuis_poste(self):
        """Le LLM place parfois un decoupage dans `poste` : il doit etre promu."""
        _, _, cible_txt, refus = tools._resoudre_cible(poste="montures")
        assert not refus and cible_txt == "montures"


# ---------------------------------------------------------------------------
# Traduction des dimensions
# ---------------------------------------------------------------------------
class TestDimensions:
    @pytest.mark.parametrize("dimension,valeur,attendu", [
        ("sexe", "homme", "1"),
        ("sexe", "femme", "2"),
        ("sexe", "1", "1"),                      # un code deja valide passe tel quel
        ("region", "ile de france", "11"),
        ("region", "Île-de-France", "11"),
        ("region", "11", "11"),
    ])
    def test_traduction(self, dimension, valeur, attendu):
        assert tools._vers_code(dimension, valeur) == attendu

    @pytest.mark.parametrize("dimension,valeur", [
        ("region", "atlantide"), ("region", "Californie"), ("sexe", "robot"),
    ])
    def test_valeurs_inconnues(self, dimension, valeur):
        assert tools._vers_code(dimension, valeur) is None

    def test_aller_retour_code_libelle(self):
        assert tools._vers_libelle("region", tools._vers_code("region", "occitanie"))


# ---------------------------------------------------------------------------
# Normalisation des parametres envoyes par le LLM
# ---------------------------------------------------------------------------
class TestNormalisationParametres:
    @pytest.mark.parametrize("entree", ["", "  ", "null", "none", "N/A", "aucun", "-"])
    def test_valeurs_vides_deviennent_none(self, entree):
        assert tools._vide(entree) is None

    @pytest.mark.parametrize("entree", ["optique", "dentaire", 2023])
    def test_valeurs_utiles_sont_conservees(self, entree):
        assert tools._vide(entree) == entree

    @pytest.mark.parametrize("entree,attendu", [
        (2023, 2023), ("2023", 2023), (" 2024 ", 2024),
        (None, None), ("", None),
        (["2022", "2023"], None),        # liste -> pas de filtre, tout le perimetre
        ("2022-2025", None),             # plage -> pas de filtre
        ("2022/2025", None),
        ("n'importe quoi", None),
    ])
    def test_normaliser_annee(self, entree, attendu):
        assert tools._normaliser_annee(entree) == attendu

    @pytest.mark.parametrize("entree,attendu", [
        ("montant_rembourse", "montant_rembourse"),
        ("montant rembourse", "montant_rembourse"),
        ("remboursements", "montant_rembourse"),
        ("depense_engagee", "depense_engagee"),
        ("depenses", "depense_engagee"),
        ("nombre_actes", "nombre_actes"),
        ("actes", "nombre_actes"),
        ("depassement", "depassement"),
        ("mesure inconnue xyz", "montant_rembourse"),   # defaut, jamais un plantage
    ])
    def test_normaliser_mesure(self, entree, attendu):
        assert tools._normaliser_mesure(entree) == attendu


# ---------------------------------------------------------------------------
# GARANTIES FAIL-CLOSED — le coeur du contrat
# ---------------------------------------------------------------------------
REFUS = ("ne reconnais pas", "ne fait pas partie du périmètre",
         "hors du périmètre", "aucune donnée disponible",
         "ne contient que la part assurance maladie")


def est_un_refus(reponse):
    bas = reponse.lower()
    return any(m in bas for m in REFUS)


@besoin_base
class TestFailClosed:
    def test_poste_inconnu_refuse(self):
        assert est_un_refus(tools.query_depenses(poste="licorne"))

    def test_poste_hors_perimetre_refuse(self):
        assert est_un_refus(tools.query_depenses(poste="chirurgie esthetique"))

    def test_amc_refuse(self):
        assert est_un_refus(tools.query_depenses(poste="mutuelle"))

    @pytest.mark.parametrize("annee", [2019, 2010, 2030, 1999])
    def test_annee_hors_perimetre_refuse(self, annee):
        rep = tools.query_depenses(annee=annee)
        assert est_un_refus(rep) and str(annee) in rep

    @pytest.mark.parametrize("champ,valeur", [
        ("region", "Atlantide"), ("region", "Californie"),
        ("sexe", "robot"), ("age", "centenaire martien"),
    ])
    def test_dimension_inconnue_refuse(self, champ, valeur):
        """Un filtre non reconnu ne doit JAMAIS etre ignore silencieusement :
        le chiffre renvoye serait juste, mais repondrait a une autre question."""
        rep = tools.query_depenses(**{champ: valeur})
        assert est_un_refus(rep) and valeur in rep

    def test_sous_categorie_inconnue_refuse(self):
        assert est_un_refus(tools.query_depenses(sous_categorie="n_importe_quoi"))

    def test_decoupage_inconnu_refuse(self):
        assert est_un_refus(tools.query_depenses(decoupage="zzz_inexistant"))

    def test_dimension_de_repartition_inconnue_refuse(self):
        assert est_un_refus(tools.repartition(dimension="planete"))

    def test_refus_ne_contient_aucun_chiffre_monetaire(self):
        """Un refus qui laisserait fuiter un montant viderait la garantie."""
        for rep in (tools.query_depenses(poste="licorne"),
                    tools.query_depenses(annee=2030),
                    tools.query_depenses(region="Atlantide")):
            assert "€" not in rep


# ---------------------------------------------------------------------------
# Calculs — coherence, pas valeurs figees (elles dependent du millesime des donnees)
# ---------------------------------------------------------------------------
@besoin_base
class TestCalculs:
    def test_total_est_positif(self):
        assert "€" in tools.query_depenses(annee=2023)

    def test_filtrer_reduit_le_total(self):
        """Un sous-ensemble ne peut pas depasser le total : garde-fou contre une
        jointure qui dupliquerait des lignes."""
        total = tools._valeur_brute(tools.MESURES["montant_rembourse"], None, 2023)
        optique = tools._valeur_brute(tools.MESURES["montant_rembourse"], "optique", 2023)
        assert 0 < optique < total

    def test_taux_couverture_est_un_pourcentage_plausible(self):
        rep = tools.taux_couverture("dentaire", 2023)
        valeur = float(rep.rsplit(":", 1)[1].strip().rstrip("%"))
        assert 0 < valeur <= 100

    def test_compare_periods_exige_deux_annees(self):
        rep = tools.compare_periods(poste="dentaire", annee1=2022)
        assert "deux années" in rep

    def test_compare_periods_calcule_une_evolution(self):
        assert "Évolution" in tools.compare_periods("dentaire", 2022, 2024)

    def test_top_postes_borne_n(self):
        """n est fourni par le LLM : il doit etre borne, pas fait confiance."""
        assert tools.top_postes(n=999) and tools.top_postes(n=-5)
        assert tools.top_postes(n="abc")     # non numerique -> defaut

    @pytest.mark.parametrize("dimension", ["poste", "region", "age", "sexe"])
    def test_repartition_sur_chaque_dimension(self, dimension):
        _, table = extraire_table(tools.repartition(dimension=dimension, annee=2023))
        assert table and table["lignes"]

    def test_evolution_couvre_le_perimetre(self):
        _, table = extraire_table(tools.evolution_serie(poste="pharmacie"))
        debut, fin = tools._annees_couvertes()
        annees = [l[0] for l in table["lignes"]]
        assert annees == sorted(annees) and debut <= annees[0] and annees[-1] <= fin


# ---------------------------------------------------------------------------
# Glossaire et referentiel (aucun acces base)
# ---------------------------------------------------------------------------
class TestGlossaire:
    @pytest.mark.parametrize("terme", ["AMC", "AMO", "C2S", "ticket modérateur"])
    def test_termes_connus(self, terme):
        assert "non trouvé" not in tools.get_dictionnaire(terme)

    def test_terme_inconnu(self):
        assert "non trouvé" in tools.get_dictionnaire("zzzz_inexistant")

    def test_terme_vide(self):
        assert "vide" in tools.get_dictionnaire("").lower()

    def test_insensible_a_la_casse_et_aux_accents(self):
        assert tools.get_dictionnaire("amc") == tools.get_dictionnaire("AMC")

    def test_lister_variables_sans_argument(self):
        assert "Catégories" in tools.lister_variables()

    def test_decrire_variable_inconnue(self):
        assert "non documentée" in tools.decrire_variable("XYZ_INEXISTANT")

    def test_list_valeurs_annees(self):
        debut, fin = tools._annees_couvertes()
        assert str(debut) in tools.list_valeurs("annee")

    def test_list_valeurs_dimension_inconnue(self):
        assert "inconnue" in tools.list_valeurs("licorne")


# ---------------------------------------------------------------------------
# Formatage et transport du tableau structure
# ---------------------------------------------------------------------------
class TestFormatage:
    @pytest.mark.parametrize("valeur,mesure,attendu", [
        (1_500_000_000, "montant_rembourse", "1,50 Md€"),
        (2_500_000, "montant_rembourse", "2,50 M€"),
        (1234, "nombre_actes", "1 234 actes"),
        (0, "montant_rembourse", "0,00 €"),
        (None, "montant_rembourse", "0,00 €"),
    ])
    def test_montant(self, valeur, mesure, attendu):
        assert montant(valeur, mesure) == attendu

    def test_format_valeur_compact(self):
        assert format_valeur(1_500_000_000) == "1,50 Md€"
        assert format_valeur(1234, "actes") == "1 234"

    def test_aller_retour_bloc_table(self):
        lignes = [["Pharmacie", 1.5], ["Optique", 0.5]]
        texte = "Titre : a | b" + bloc_table(["Poste", "Montant"], lignes, "Titre", "€")
        clair, table = extraire_table(texte)
        assert table["lignes"] == lignes and table["titre"] == "Titre"
        assert "DAMIA_TABLE" not in clair

    def test_extraire_table_sans_table(self):
        assert extraire_table("Reponse simple.") == ("Reponse simple.", None)

    def test_extraire_table_coupe_enumeration(self):
        texte = "Titre : a | b" + bloc_table(["A", "B"], [["a", 1]], "Titre", "€")
        assert extraire_table(texte, couper_enumeration=True)[0] == "Titre"

    def test_extraire_table_json_corrompu(self):
        """Un bloc illisible ne doit pas faire planter l'interface."""
        assert extraire_table("Texte\n\n<!--DAMIA_TABLE:{cassé-->")[1] is None
