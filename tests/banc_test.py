"""
banc_test.py — Banc de test du routage + calcul + CONTENU + TEMPS de l'assistant DAMIA.

Passe par poser_question() : MEME chemin que l'interface (modele -> routage -> outil).
Lit les cas depuis config/cas_tests.json (valeurs modifiables si la base change).
Mesure le temps de reponse de chaque question (critere de comparaison des modeles).
Verifie le CONTENU textuel attendu (champ "contient_attendu") : indispensable pour les
reponses du glossaire, dont le routage seul ne garantit pas la justesse.
Produit un rapport HTML exportable + un CSV.

Lancement (depuis la racine) :
    python tests/banc_test.py
"""
import sys, os, json, re, csv, datetime, time, unicodedata

# Racine du projet = dossier parent de tests/
RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)
sys.path.insert(0, os.path.join(RACINE, "src"))

from src.assistant import poser_question
from src.backends import get_backend

CHEMIN_CAS = os.path.join(RACINE, "config", "cas_tests.json")


def _norm_txt(s):
    """Minuscule, sans accents : rend la comparaison de contenu robuste aux accents."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower()


def _nombre_depuis_texte(texte):
    """Extrait la valeur monetaire de la reponse ('... : 66,84 M€.' -> 66840000)."""
    if not texte:
        return None
    partie = texte.split("<!--DAMIA_TABLE")[0]
    partie = partie.split("À noter")[0].split("A noter")[0]
    if " : " in partie:
        partie = partie.rsplit(" : ", 1)[1]
    partie = partie.replace("\u202f", " ").replace("\xa0", " ")
    m = re.search(r"([\d][\d\s]*(?:,\d+)?)\s*(Md|M|k)?\s*€", partie)
    if not m:
        return None
    brut = m.group(1).replace(" ", "").replace(",", ".")
    try:
        val = float(brut)
    except ValueError:
        return None
    mult = {"Md": 1e9, "M": 1e6, "k": 1e3}.get(m.group(2), 1)
    return val * mult


def _valeur_reponse(res):
    """Recupere la valeur calculee : d'abord le bloc table structure, sinon la phrase."""
    rep = res.get("reponse", "") or ""
    if "<!--DAMIA_TABLE:" in rep:
        try:
            deb = rep.index("<!--DAMIA_TABLE:") + len("<!--DAMIA_TABLE:")
            fin = rep.index("-->", deb)
            table = json.loads(rep[deb:fin])
            lignes = table.get("lignes") or []
            if lignes:
                return sum(float(l[1] or 0) for l in lignes)
        except (ValueError, json.JSONDecodeError):
            pass
    return _nombre_depuis_texte(rep)


def _params_ok(attendus, obtenus):
    """Routage souple : tous les params attendus doivent etre presents."""
    if not attendus:
        return True
    obtenus = obtenus or {}
    for cle, val in attendus.items():
        got = obtenus.get(cle)
        if got is None:
            return False
        if str(got).strip().lower() != str(val).strip().lower():
            return False
    return True


def _contenu_ok(attendus_txt, reponse):
    """Toutes les sous-chaines attendues doivent apparaitre dans la reponse (sans accents).
    Renvoie (ok, manquants)."""
    if not attendus_txt:
        return None, []
    rep_norm = _norm_txt(reponse)
    manquants = [s for s in attendus_txt if _norm_txt(s) not in rep_norm]
    return (not manquants), manquants


def executer():
    data = json.load(open(CHEMIN_CAS, encoding="utf-8"))
    meta = data.get("_meta", {})
    tol_defaut = meta.get("tolerance_pct_defaut", 1.0)
    resultats = []

    for cas in data["cas"]:
        q = cas["question"]
        t0 = time.perf_counter()
        res = poser_question(q)
        duree = time.perf_counter() - t0

        outil = res.get("outil")
        params = res.get("parametres") or {}
        rep = res.get("reponse", "") or ""
        rep_bas = rep.lower()
        # Les outils informatifs (definition, meta) renvoient un texte legitime qui
        # peut contenir « hors perimetre » (ex. la definition de l'AMC) : on ne les
        # scanne PAS comme des refus. Le scan textuel ne vise que les refus implicites
        # des outils de donnees (ex. query_depenses qui ne trouve rien).
        OUTILS_INFORMATIFS = {"get_dictionnaire", "reponse_meta"}
        phrases_refus = ["ne reconnais pas", "je préfère ne pas",
                         "ne fait pas partie du périmètre", "hors du périmètre",
                         "hors périmètre", "aucune donnée disponible"]
        texte_refus = (outil not in OUTILS_INFORMATIFS
                       and any(p in rep_bas for p in phrases_refus))
        est_refus = bool(res.get("hors_perimetre")) or (outil is None) or texte_refus

        r = {"question": q, "categorie": cas.get("categorie", ""),
             "outil_obtenu": outil or "(aucun)", "reponse": rep[:200],
             "duree": duree,
             "routage_ok": None, "calcul_ok": None, "contenu_ok": None,
             "verdict": None}

        if cas.get("refus_attendu"):
            r["type"] = "Refus"
            r["routage_ok"] = bool(est_refus)
            r["calcul_ok"] = None
            r["verdict"] = "OK" if est_refus else "ÉCHEC"
        else:
            r["type"] = "Calcul"
            outils_ok = outil in cas.get("outils_acceptes", []) if cas.get("outils_acceptes") else True
            r["routage_ok"] = bool(outils_ok and _params_ok(cas.get("params_attendus"), params)
                                   and not est_refus)
            if cas.get("valeur_attendue") is not None:
                val = _valeur_reponse(res)
                if val is None:
                    r["calcul_ok"] = False
                else:
                    tol = cas.get("tolerance_pct", tol_defaut) / 100.0
                    attendu = cas["valeur_attendue"]
                    r["calcul_ok"] = abs(val - attendu) <= abs(attendu) * tol
                    r["valeur_obtenue"] = val
                    r["valeur_attendue"] = attendu

            # Verification du contenu textuel attendu (ex. definitions du glossaire)
            contenu_ok, manquants = _contenu_ok(cas.get("contient_attendu"), rep)
            r["contenu_ok"] = contenu_ok
            if manquants:
                r["contenu_manquant"] = manquants

            r["verdict"] = "OK" if (r["routage_ok"]
                                    and (r["calcul_ok"] is not False)
                                    and (r["contenu_ok"] is not False)) else "ÉCHEC"

        resultats.append(r)

    return meta, resultats


def _pct(n, d):
    return f"{(100*n/d):.0f}%" if d else "—"


def _stats_temps(resultats):
    """Temps total, moyen, min, max (en secondes) sur les cas mesures."""
    durees = [r["duree"] for r in resultats if r.get("duree") is not None]
    if not durees:
        return {"total": 0, "moyen": 0, "min": 0, "max": 0}
    return {"total": sum(durees), "moyen": sum(durees) / len(durees),
            "min": min(durees), "max": max(durees)}


def rapport_html(meta, resultats, modele):
    total = len(resultats)
    ok = sum(1 for r in resultats if r["verdict"] == "OK")
    rout = [r for r in resultats if r["routage_ok"] is not None]
    rout_ok = sum(1 for r in rout if r["routage_ok"])
    calc = [r for r in resultats if r["calcul_ok"] is not None]
    calc_ok = sum(1 for r in calc if r["calcul_ok"])
    cont = [r for r in resultats if r["contenu_ok"] is not None]
    cont_ok = sum(1 for r in cont if r["contenu_ok"])
    t = _stats_temps(resultats)
    date = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    V = "#472583"

    lignes = ""
    for r in resultats:
        coul = "#e7f6ec" if r["verdict"] == "OK" else "#fdeaea"
        badge = "#1e7e34" if r["verdict"] == "OK" else "#c0392b"
        rt = {True: "✓", False: "✗", None: "—"}[r["routage_ok"]]
        ct = {True: "✓", False: "✗", None: "—"}[r["calcul_ok"]]
        cot = {True: "✓", False: "✗", None: "—"}[r["contenu_ok"]]
        val = ""
        if "valeur_obtenue" in r:
            val = f"attendu {r['valeur_attendue']:,.0f} / obtenu {r['valeur_obtenue']:,.0f}".replace(",", " ")
        if r.get("contenu_manquant"):
            val = (val + " · " if val else "") + "manque : " + ", ".join(r["contenu_manquant"])
        dur = f"{r['duree']:.2f} s" if r.get("duree") is not None else "—"
        lignes += f"""<tr style="background:{coul}">
          <td>{r['categorie']}</td><td>{r['question']}</td>
          <td style="text-align:center">{r['outil_obtenu']}</td>
          <td style="text-align:center">{rt}</td><td style="text-align:center">{ct}</td>
          <td style="text-align:center">{cot}</td>
          <td style="text-align:center;font-size:12px">{dur}</td>
          <td style="font-size:11px;color:#555">{val}</td>
          <td style="text-align:center"><b style="color:{badge}">{r['verdict']}</b></td></tr>"""

    return f"""<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<title>Banc de test DAMIA — {date}</title>
<style>
 body{{font-family:Arial,sans-serif;margin:30px;color:#2a2a2a}}
 h1{{color:{V}}} .peri{{background:#f4f1f8;border-left:4px solid {V};padding:12px 16px;font-size:13px;margin:14px 0}}
 .kpis{{display:flex;gap:14px;margin:20px 0;flex-wrap:wrap}}
 .kpi{{flex:1;min-width:150px;background:{V};color:#fff;border-radius:10px;padding:16px;text-align:center}}
 .kpi.t{{background:#3c7d5a}}
 .kpi .v{{font-size:28px;font-weight:bold}} .kpi .l{{font-size:12px;color:#e2ddec}}
 table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}}
 th{{background:{V};color:#fff;padding:8px;text-align:left}} td{{padding:7px;border-bottom:1px solid #eee}}
</style></head><body>
<h1>Banc de test — Assistant DAMIA</h1>
<p style="color:#888">Généré le {date} · Modèle testé : <b>{modele}</b></p>
<div class="peri"><b>Périmètre :</b> {meta.get('perimetre','')}</div>
<div class="kpis">
  <div class="kpi"><div class="v">{_pct(ok,total)}</div><div class="l">Réussite globale ({ok}/{total})</div></div>
  <div class="kpi"><div class="v">{_pct(rout_ok,len(rout))}</div><div class="l">Routage correct ({rout_ok}/{len(rout)})</div></div>
  <div class="kpi"><div class="v">{_pct(calc_ok,len(calc))}</div><div class="l">Calcul correct ({calc_ok}/{len(calc)})</div></div>
  <div class="kpi"><div class="v">{_pct(cont_ok,len(cont))}</div><div class="l">Contenu correct ({cont_ok}/{len(cont)})</div></div>
  <div class="kpi t"><div class="v">{t['moyen']:.2f} s</div><div class="l">Temps moyen / question</div></div>
  <div class="kpi t"><div class="v">{t['total']:.1f} s</div><div class="l">Temps total ({t['min']:.2f}–{t['max']:.2f} s)</div></div>
</div>
<table><tr><th>Catégorie</th><th>Question</th><th>Outil</th><th>Routage</th><th>Calcul</th><th>Contenu</th><th>Temps</th><th>Valeurs</th><th>Verdict</th></tr>
{lignes}</table>
<p style="color:#aaa;font-size:11px;margin-top:20px">Routage = bon outil + bons paramètres · Calcul = valeur dans la tolérance · Contenu = sous-chaînes attendues présentes · Temps = latence de bout en bout · « — » = non applicable.</p>
</body></html>"""


if __name__ == "__main__":
    backend = get_backend()
    backend_type = os.environ.get("DAMIA_BACKEND", "ollama")
    modele_nom = getattr(backend, "modele", None) or getattr(backend, "model_id", None) or "?"
    modele = f"{backend_type} / {modele_nom}"
    print(f"Lancement du banc de test (modèle : {modele})...\n")
    meta, resultats = executer()

    total = len(resultats)
    ok = sum(1 for r in resultats if r["verdict"] == "OK")
    t = _stats_temps(resultats)
    for r in resultats:
        print(f"  [{r['verdict']:6s}] {r['duree']:5.2f}s  {r['question'][:50]}")
    print(f"\n=> {ok}/{total} réussis ({100*ok//total}%) | "
          f"temps moyen {t['moyen']:.2f}s, total {t['total']:.1f}s")

    html = rapport_html(meta, resultats, modele)
    horo = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    f_html = f"rapport_test_{horo}.html"
    open(f_html, "w", encoding="utf-8").write(html)

    f_csv = f"rapport_test_{horo}.csv"
    with open(f_csv, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp, delimiter=";")
        w.writerow(["Categorie", "Question", "Outil obtenu", "Routage OK",
                    "Calcul OK", "Contenu OK", "Duree (s)", "Verdict"])
        for r in resultats:
            w.writerow([r["categorie"], r["question"], r["outil_obtenu"],
                        r["routage_ok"], r["calcul_ok"], r["contenu_ok"],
                        f"{r['duree']:.2f}", r["verdict"]])

    print(f"\nRapport HTML : {f_html}\nRapport CSV  : {f_csv}")