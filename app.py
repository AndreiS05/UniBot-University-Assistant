from flask import Flask, request, jsonify, render_template, g
from transformers import AutoTokenizer, AutoModelForQuestionAnswering
import torch
import sqlite3
import os
import sys
import re
import uuid
import unicodedata


try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

app = Flask(__name__)

model_name = "deepset/xlm-roberta-base-squad2"
tokenizer = None
model = None


def _incarca_model():
    """Incarca o singura data modelul extractiv de QA (Reader)."""
    global tokenizer, model
    if model is not None:
        return
    print("Se incarca modelul AI multilingual...")
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForQuestionAnswering.from_pretrained(model_name)
    print("Modelul a fost incarcat cu succes!")


# Logica non-neuronala (retriever, formulare, informatii, istoric) nu are nevoie de model;
# in teste se poate sari peste incarcarea lui (~1.1 GB) prin UNIBOT_SKIP_MODEL=1.
if os.environ.get('UNIBOT_SKIP_MODEL') != '1':
    _incarca_model()

def _normalize(text):
    """Scoate diacriticele, trece la litere mici, pastreaza doar litere/cifre/spatii."""
    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9 ]', ' ', text)


# Cuvinte de legatura ignorate la potrivire (nu ajuta la identificarea materiei)
_STOP = {
    'care', 'este', 'sunt', 'are', 'cu', 'la', 'de', 'pe', 'in', 'un', 'si', 'sau',
    'materia', 'materie', 'disciplina', 'cursul', 'curs', 'despre', 'cate', 'cat',
    'cati', 'cum', 'unde', 'cand', 'cine', 'pentru', 'preda', 'promoveaza', 'promovez',
    'credite', 'anul', 'semestrul', 'vreau', 'stiu', 'poti', 'spune', 'trebuie',
    # Cuvinte generice de atribut: nu sunt distinctive pentru numele unei materii, deci nu
    # trebuie sa conteze la potrivire (altfel "cate ore?" potriveste materii care au aceste
    # cuvinte in nume, ex. "Stagiu de practica (4 sapt x 6 ore/zi)").
    'ore', 'ora', 'orele', 'seminar', 'seminarul', 'laborator', 'laboratorul',
    'prezenta', 'prezente', 'prezentele', 'titular', 'titularul', 'profesor', 'profesorul',
    'zi', 'zile', 'sapt', 'saptamana', 'saptamani', 'evaluare', 'examen', 'fel',
}


# Numerale romane folosite in numele materiilor (Programare I/II/III, Sisteme de operare II...)
_ROMAN = {'i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x'}

# Cuvinte romanesti scurte, uzuale, care pot coincide cu o abreviere (ex. "CE", "CA").
# Sunt ignorate ca posibile abrevieri DOAR cand tot mesajul e scris cu majuscule (Caps Lock),
# caz in care nu mai putem folosi majuscula ca semnal ca un token e abreviere.
_CUVINTE_SCURTE_RO = {
    'ce', 'ca', 'va', 'ar', 'pe', 'se', 'de', 'la', 'in', 'cu', 'un', 'si', 'sa', 'ma', 'te',
    'el', 'ea', 'nu', 'da', 'mi', 'ii', 'sau', 'are', 'ale', 'lui', 'ei', 'o', 'a',
    'cine', 'care', 'cand', 'cum', 'cat', 'cate', 'cati', 'unde', 'ce',
}


def _significant(word):
    """Un cuvant conteaza la potrivire daca e destul de lung SAU e numeral roman/cifra."""
    if word in _STOP:
        return False
    return len(word) >= 3 or word in _ROMAN or word.isdigit()


def _find_best_materie(user_message):
    """
    COMPONENTA RETRIEVER: alege din baza de date randul (materia) cel mai relevant
    pentru intrebare, folosind normalizarea diacriticelor + potrivire pe cuvintele
    din nume si pe abreviere (ex. "ASD", "SO II"). Intoarce un sqlite3.Row sau None.
    """
    if not os.path.exists('facultate.db'):
        return None

    try:
        conn = sqlite3.connect('facultate.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("""
            SELECT m.nume, m.abreviere, m.an, m.semestru, m.categorie, m.tip, m.credite,
                   m.ore_curs, m.ore_seminar, m.ore_laborator, m.titular, m.promovare,
                   p.prezente_curs, p.prezente_seminar, p.prezente_laborator
            FROM materii m
            LEFT JOIN prezenta p ON p.materie_id = m.id
        """)
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"Eroare la DB: {e}")
        return None

    q_norm = _normalize(user_message)
    q_compact = q_norm.replace(' ', '')
    q_words = {w for w in q_norm.split() if _significant(w)}

    # Abrevierile scurte (2 litere, ex. "BD", "CE") nu pot fi potrivite pe textul normalizat,
    # fiindca dupa lowercase s-ar confunda cu cuvinte romanesti ("ce", "ca"...). Le detectam
    # dupa MAJUSCULE in mesajul brut: un token scris integral cu majuscule este un semnal ca
    # utilizatorul a scris o abreviere, nu un cuvant obisnuit.
    abbr_candidates = {t.lower() for t in re.findall(r'\b[A-Z][A-Z0-9]+\b', user_message)}
    # Exceptie: daca TOT mesajul e cu majuscule (Caps Lock), semnalul devine nesigur -> nu mai
    # tratam drept abrevieri cuvintele romanesti scurte uzuale (dar "BD", "ASD" etc. raman).
    if user_message and not any(c.islower() for c in user_message):
        abbr_candidates -= _CUVINTE_SCURTE_RO

    best, best_score = None, 0
    for row in rows:
        name_norm = _normalize(row['nume'])
        name_words = {w for w in name_norm.split() if _significant(w)}

        score = len(q_words & name_words)
        # Numele complet al materiei apare in intrebare -> potrivire foarte puternica
        if name_norm and name_norm in q_norm:
            score += 5
        # Abrevierea (ASD, SO II, BD...) apare in intrebare -> potrivire puternica.
        # "ASD I" -> token "asd" sau compact "asdi"; abrevierile scurte (BD, CE) -> prin
        # candidatii cu majuscule din mesajul brut.
        abbr_norm = _normalize(row['abreviere'])
        abbr_tokens = {t for t in abbr_norm.split() if len(t) >= 3}
        abbr_compact = abbr_norm.replace(' ', '')
        abbr_hit = (
            bool(abbr_tokens & q_words)
            or (len(abbr_compact) >= 4 and abbr_compact in q_compact)
            or (bool(abbr_compact) and abbr_compact in abbr_candidates)
        )
        if abbr_hit:
            score += 4

        if score > best_score:
            best_score, best = score, row

    return best if best_score > 0 else None


def _build_context(m):
    """Construieste un context bogat (text) pentru materia gasita, pentru Reader."""
    parts = [f"Disciplina {m['nume']}"]
    if m['abreviere']:
        parts.append(f" (abreviata {m['abreviere']})")
    if m['credite'] is not None:
        parts.append(f" are {m['credite']} credite ECTS.")
    else:
        parts.append(".")
    det = []
    if m['categorie']:
        det.append(f"disciplina {m['categorie']}")
    if m['tip']:
        det.append(m['tip'])
    if det:
        parts.append(f" Este o {' '.join(det)}.")
    if m['an'] and m['semestru']:
        parts.append(f" Se studiaza in anul {m['an']}, semestrul {m['semestru']}.")
    ore = []
    if m['ore_curs']:
        ore.append(f"{m['ore_curs']} ore de curs")
    if m['ore_seminar']:
        ore.append(f"{m['ore_seminar']} ore de seminar")
    if m['ore_laborator']:
        ore.append(f"{m['ore_laborator']} ore de laborator")
    if ore:
        parts.append(f" Are {', '.join(ore)} pe saptamana.")
    if m['titular']:
        parts.append(f" Titularul cursului este {m['titular']}.")
    if m['promovare']:
        parts.append(f" Promovare: {m['promovare']}.")
    pz = []
    if m['prezente_curs']:
        pz.append(f"la curs {m['prezente_curs']}")
    if m['prezente_seminar']:
        pz.append(f"la seminar {m['prezente_seminar']}")
    if m['prezente_laborator']:
        pz.append(f"la laborator {m['prezente_laborator']}")
    if pz:
        parts.append(" Prezența obligatorie: " + "; ".join(pz) + ".")
    return "".join(parts)


def get_context_from_db(user_message):
    """Wrapper: intoarce contextul materiei relevante, sau "" daca nu s-a gasit."""
    m = _find_best_materie(user_message)
    return _build_context(m) if m else ""


def _match_info(q_norm):
    """
    Verifica daca intrebarea e despre o informatie GENERALA (absolvirea facultatii,
    examenul de licenta, disciplinele complementare DCT...), care nu tine de o materie
    anume. Potrivire pe fraze-cheie: toate cuvintele frazei trebuie sa apara in intrebare.
    Intoarce raspunsul (text) sau None.
    """
    if not os.path.exists('facultate.db'):
        return None
    try:
        conn = sqlite3.connect('facultate.db')
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT cuvinte_cheie, raspuns FROM informatii"
        ).fetchall()
        conn.close()
    except Exception:
        return None

    for row in rows:
        for phrase in row['cuvinte_cheie'].split(','):
            words = [w for w in phrase.strip().split() if w]
            if words and all(w in q_norm for w in words):
                return row['raspuns']
    return None

def process_with_ai(query, context, max_answer_len=40, n_best=20, null_threshold=6.0):
    """
    COMPONENTA READER: Primeste doar paragraful relevant si extrage raspunsul.

    Post-procesare corecta pentru un model de tip SQuAD2 (extractiv):
      1. Restrangem cautarea DOAR la tokenii din context (mascam intrebarea,
         tokenii speciali si padding-ul). Altfel modelul pune scorul maxim pe
         tokenul <s> (pozitia 0 = "fara raspuns"), iar raspunsul iese gol.
      2. Cautam cea mai buna pereche (start, end) valida, cu lungime limitata.
      3. Comparam scorul span-ului cu scorul "no-answer" (start[0]+end[0]).
         Deoarece retriever-ul garanteaza deja ca in context exista materia
         relevanta, acceptam span-ul cu un prag tolerant: dam fallback DOAR
         cand modelul e MULT mai increzator ca nu exista raspuns
         (null_score - best_score > null_threshold).
    """
    if model is None or tokenizer is None:
        return None  # model neincarcat (ex. in teste) -> lasa apelantul sa dea alt mesaj

    inputs = tokenizer(query, context, return_tensors="pt",
                       truncation=True, max_length=512)

    with torch.no_grad():
        outputs = model(**inputs)

    start_logits = outputs.start_logits[0]
    end_logits = outputs.end_logits[0]

    # Masca: True doar pentru tokenii care apartin contextului (sequence id == 1)
    seq_ids = inputs.sequence_ids(0)
    context_mask = torch.tensor(
        [sid == 1 for sid in seq_ids], dtype=torch.bool
    )

    # Scorul "fara raspuns" (tokenul CLS de la pozitia 0)
    null_score = float(start_logits[0] + end_logits[0])

    # Aplicam masca: tokenii din afara contextului primesc -infinit
    neg_inf = torch.finfo(start_logits.dtype).min
    masked_start = start_logits.masked_fill(~context_mask, neg_inf)
    masked_end = end_logits.masked_fill(~context_mask, neg_inf)

    # Cautam cea mai buna pereche (start, end) valida printre top n_best candidati
    start_candidates = torch.topk(masked_start, min(n_best, masked_start.size(0))).indices.tolist()
    end_candidates = torch.topk(masked_end, min(n_best, masked_end.size(0))).indices.tolist()

    best_score = neg_inf
    best_start, best_end = None, None
    for s in start_candidates:
        for e in end_candidates:
            if e < s or (e - s + 1) > max_answer_len:
                continue
            score = float(start_logits[s] + end_logits[e])
            if score > best_score:
                best_score, best_start, best_end = score, s, e

    # Fallback doar cand modelul e MULT mai sigur ca nu exista raspuns.
    # Intoarce None ca sa lase apelantul (get_response) sa formuleze un mesaj util, adaptat
    # materiei gasite (in loc de un mesaj generic de eroare).
    if best_start is None or (null_score - best_score) > null_threshold:
        return None

    answer_tokens = inputs.input_ids[0, best_start:best_end + 1]
    answer = tokenizer.decode(answer_tokens, skip_special_tokens=True).strip()

    return answer if answer else None

@app.route('/')
def index():
    return render_template('index.html')

# Mesaj pentru intrebari din afara domeniului (care nu au legatura cu facultatea)
OUT_OF_DOMAIN = (
    "Îmi pare rău, momentan pot răspunde doar la întrebări despre Facultatea de Informatică "
    "(UVT) — de exemplu despre materii, credite, cadre didactice, mod de promovare, prezențe, "
    "admitere, taxe sau resurse. Încearcă să reformulezi întrebarea."
)

# Categorii de intrebari care nu tin de o materie anume, cu raspuns dedicat (in loc de un
# singur mesaj generic de out-of-domain).
_SALUT = {'buna', 'salut', 'salutare', 'hei', 'hey', 'servus', 'noroc', 'ceau', 'bonjour', 'hello'}
_MULTUMIRI = {'multumesc', 'multumiri', 'mersi', 'ms', 'thanks', 'thx'}
_ORAR_TOKENS = {'orar', 'orarul', 'sala', 'sali', 'salile'}
_ORAR_FRAZE = ('cand am curs', 'ce sala', 'in ce sala', 'unde am curs', 'ce ore am azi', 'la ce ora')
_CAZARE_TOKENS = {'cazare', 'camin', 'camine', 'caminul', 'caminele'}
_CAZARE_FRAZE = ('unde stau', 'unde locuiesc', 'unde ma cazez')


def _mesaj_special(q_norm):
    """
    Raspuns dedicat pentru intrebari care nu vizeaza o materie: salut, multumiri, orar/sali si
    cazare (ultimele doua sunt in afara scopului, intentionat). Intoarce textul potrivit sau
    None daca intrebarea nu se incadreaza (caz in care se foloseste mesajul generic OUT_OF_DOMAIN).
    """
    toks = set(q_norm.split())
    if toks & _SALUT:
        return ("Salut! Sunt UniBot, asistentul Facultății de Informatică (UVT). Te pot ajuta cu "
                "informații despre materii (credite, titular, mod de promovare, prezențe, ore), dar "
                "și despre admitere, taxe, sesiuni sau absolvire. Cu ce te pot ajuta?")
    if toks & _MULTUMIRI:
        return "Cu plăcere! Dacă mai ai întrebări despre facultate, sunt aici. 😊"
    if (toks & _ORAR_TOKENS) or any(f in q_norm for f in _ORAR_FRAZE):
        return ("Momentan nu ofer informații despre orar sau săli — acestea se schimbă de la an la "
                "an. Le găsești pe platforma de orar a facultății sau pe site-ul UVT "
                "(https://info.uvt.ro).")
    if (toks & _CAZARE_TOKENS) or any(f in q_norm for f in _CAZARE_FRAZE):
        return ("Nu am informații despre cazare sau cămine studențești. Pentru asta consultă "
                "site-ul UVT (https://uvt.ro) sau întreabă la infocentrul facultății.")
    return None

# Cuvinte care indica o intrebare de continuare ("dar cine O preda?", "cate credite are EA?")
_REFERINTE = (' o ', ' ea ', ' ei ', 'aceasta', 'acesta', 'acestei', 'acesteia', 'materia asta', 'aceeasi')
# Aceleasi pronume, ca tokeni intregi -> tratate ca si cuvinte in plus in bucla din _este_followup.
_REF_TOKENS = {'o', 'ea', 'ei', 'aceasta', 'acesta', 'acestei', 'acesteia', 'aceeasi'}

# Cuvinte de legatura admise intr-o intrebare de continuare (interogative, verbe auxiliare,
# conectori, prepozitii). Nu poarta informatie de continut.
_STOP_FOLLOWUP = {
    'cate', 'cat', 'cati', 'cata', 'care', 'ce', 'cine', 'cum', 'cand', 'unde',
    'are', 'au', 'ai', 'e', 'ii', 'este', 'sunt', 'se', 'imi', 'iti', 'mi', 'ti', 'ma', 'te', 'o',
    'trebuie', 'face', 'da', 'pot', 'poti', 'vreau', 'stii', 'spune', 'zi', 'zici',
    'dar', 'si', 'iar', 'atunci', 'apoi', 'deci', 'ok', 'bine',
    'la', 'de', 'pe', 'in', 'din', 'cu', 'un', 'al', 'ale', 'a', 'lui', 'ei', 'ea',
    'mai', 'multe', 'despre', 'acolo', 'cam', 'oare', 'totusi', 'pana',
}
# Radacini de atribut (potrivire pe subsir) si atribute exacte (potrivire pe token intreg,
# tinute separat ca sa nu se potriveasca accidental in cuvinte precum "franta" -> "an").
_ATRIBUT_STEM = ('credit', 'preda', 'promov', 'evalu', 'examen', 'ore', 'titular',
                 'profesor', 'prezent', 'semestr', 'obligator', 'option', 'facultativ',
                 'categori', 'nota', 'punctaj')
_ATRIBUT_EXACT = {'an', 'anul', 'ani', 'fel', 'curs', 'cursul', 'cursuri', 'cursurile',
                  'seminar', 'seminarul', 'seminarii', 'laborator', 'laboratorul', 'laboratoare'}

# Ultima materie despre care s-a discutat (pentru intrebari de continuare, in demo local)
_last_materie = None


def _este_followup(q_norm):
    """
    True daca intrebarea pare o continuare despre materia anterioara. Doua semnale:
      1. contine un pronume de referinta explicit ('o'/'ea'/'aceasta'), sau
      2. e o intrebare "goala" despre un atribut (credite/titular/prezente...), fara niciun
         cuvant de continut in plus.
    Daca ramane un token de continut (un posibil nume sau cod de materie, ex. "poo", "pizza"),
    inseamna ca utilizatorul a numit ceva ce retriever-ul nu a gasit: NU este o intrebare de
    continuare, ci una noua -> se raspunde ca fiind in afara domeniului, in loc sa se scurga
    materia anterioara. (Pronumele de referinta NU mai forteaza singur follow-up-ul: "vreau sa
    comand o pizza" contine "o", dar are si "pizza" -> intrebare noua.)
    """
    q = ' ' + q_norm + ' '
    has_ref = any(r in q for r in _REFERINTE)
    has_attr = False
    for t in q_norm.split():
        if t in _STOP_FOLLOWUP or t in _REF_TOKENS:
            continue
        if t in _ATRIBUT_EXACT or any(s in t for s in _ATRIBUT_STEM):
            has_attr = True
            continue
        return False  # token de continut (posibil nume de materie) -> intrebare noua
    return has_ref or has_attr


def _format_natural_answer(m, q_norm):
    """
    COMPONENTA DE FORMULARE A RASPUNSULUI: genereaza un raspuns natural (in propozitie)
    pentru materia gasita, in functie de ce anume intreaba utilizatorul (intentie).
    Intoarce None daca intentia nu e clara, caz in care se foloseste modelul extractiv (Reader).
    """
    nume = m['nume']
    nume_n = _normalize(nume)

    # Caz special: Limba straina (engleza) si Educatie fizica sunt obligatorii pentru
    # absolvire, cu regula de 4 din 6 semestre.
    e_limba = nume_n.startswith('limba strain')
    e_fizica = nume_n.startswith('educatie fizic')
    if (e_limba or e_fizica) and any(k in q_norm for k in ('obligator', 'trebuie', 'cate semestre', 'cate sem', 'cambridge')):
        grup = "Limba străină (engleza)" if e_limba else "Educația fizică"
        raspuns = (f"{grup} face parte dintre disciplinele obligatorii pentru absolvire: "
                   f"trebuie parcurse 4 din cele 6 semestre disponibile")
        if e_limba:
            raspuns += " (engleza este obligatorie doar dacă nu deții certificat Cambridge)"
        return raspuns + "."

    if 'prezent' in q_norm:
        pz = []
        if m['prezente_curs']:
            pz.append(f"la curs — {m['prezente_curs']}")
        if m['prezente_seminar']:
            pz.append(f"la seminar — {m['prezente_seminar']}")
        if m['prezente_laborator']:
            pz.append(f"la laborator — {m['prezente_laborator']}")
        if pz:
            return f"La disciplina {nume}, prezența obligatorie este: " + "; ".join(pz) + "."
        return f"Momentan nu am informația despre prezențele la disciplina {nume}."

    if any(k in q_norm for k in ('promov', 'evaluare', 'evalueaza', 'examen', 'cum se trece', 'cum se ia')) and m['promovare']:
        return f"La disciplina {nume}, promovarea se face astfel: {m['promovare']}"

    if any(k in q_norm for k in ('cine', 'preda', 'titular', 'profesor')):
        if m['titular']:
            return f"Disciplina {nume} este predată de {m['titular']}."
        return f"Momentan nu am informația despre titularul disciplinei {nume}."

    if 'credit' in q_norm and m['credite'] is not None:
        return f"Disciplina {nume} are {m['credite']} credite ECTS."

    if 'ore' in q_norm:
        ore = []
        if m['ore_curs']:
            ore.append(f"{m['ore_curs']} ore de curs")
        if m['ore_seminar']:
            ore.append(f"{m['ore_seminar']} ore de seminar")
        if m['ore_laborator']:
            ore.append(f"{m['ore_laborator']} ore de laborator")
        if ore:
            return f"Disciplina {nume} are {', '.join(ore)} pe săptămână."

    if any(k in q_norm for k in ('ce an', 'care an', 'anul', 'semestr', 'cand se studiaza', 'cand se face')) and m['an'] and m['semestru']:
        return f"Disciplina {nume} se studiază în anul {m['an']}, semestrul {m['semestru']}."

    if any(k in q_norm for k in ('ce fel', 'obligator', 'option', 'facultativ', 'categorie', 'ce disciplina')) and (m['categorie'] or m['tip']):
        cat_map = {'fundamentala': 'fundamentală', 'specialitate': 'de specialitate',
                   'complementara': 'complementară', 'domeniu': 'de domeniu'}
        det = [cat_map.get(m['categorie'], m['categorie'])] if m['categorie'] else []
        if m['tip']:
            det.append(m['tip'])
        return f"Disciplina {nume} este o disciplină {', '.join(det)}."

    return None


def _summary(m):
    """Rezumat prietenos al unei materii (pentru intrebari vagi: 'detalii despre X')."""
    cat_map = {'fundamentala': 'fundamentală', 'specialitate': 'de specialitate',
               'complementara': 'complementară', 'domeniu': 'de domeniu'}
    s = f"Disciplina {m['nume']}"
    if m['abreviere']:
        s += f" ({m['abreviere']})"
    if m['credite'] is not None:
        s += f" are {m['credite']} credite ECTS"
    if m['an'] and m['semestru']:
        s += f" și se studiază în anul {m['an']}, semestrul {m['semestru']}"
    s += "."
    det = []
    if m['categorie']:
        det.append(cat_map.get(m['categorie'], m['categorie']))
    if m['tip']:
        det.append(m['tip'])
    if det:
        s += f" Este o disciplină {', '.join(det)}."
    if m['titular']:
        s += f" Titular: {m['titular']}."
    return s


# Calendar: date exacte pentru raspunsuri punctuale (2025-2026)
# (Intrebarile generale: "cum e structura anului", "detalii"; primesc raspunsul complet din
#  tabelul informatii; cele punctuale :"cand incepe sesiunea B?"; primesc doar data ceruta.)
_SESIUNI = {
    'a_i':  ('Sesiunea A-I (principală, semestrul I – iarnă)', '17.01.2026 – 08.02.2026'),
    'b_i':  ('Sesiunea B-I (restanțe și măriri de note, iarnă)', '16.02.2026 – 22.02.2026'),
    'a_ii': ('Sesiunea A-II (principală, semestrul II – vară)', '06.06.2026 – 28.06.2026'),
    'b_ii': ('Sesiunea B-II (restanțe și măriri de note, vară)', '02.07.2026 – 08.07.2026'),
    'c':    ('Sesiunea C (cu taxă, doar pentru anul 3 terminal)', '22.07.2026 – 25.07.2026'),
}
_VACANTE = {
    'iarna': ('Vacanța de iarnă', '20.12.2025 – 07.01.2026'),
    'paste': ('Vacanța de Paște', '04.04.2026 – 14.04.2026'),
    'vara':  ('Vacanța de vară', '09.07.2026 – 04.10.2026'),
}
# Cuvinte care indica o intrebare despre COST (pentru restanta/recontractare)
_COST_WORDS = ('costa', 'cost', 'pret', 'plat', 'bani', 'lei', 'suma', 'scump', 'achit', 'taxa')


def _fmt_perioade(items):
    """Formateaza una sau mai multe perioade (eticheta, interval)."""
    if len(items) == 1:
        et, per = items[0]
        start = per.split('–')[0].strip()
        return f"{et} începe pe {start} (perioada: {per})."
    return "Perioadele sunt:\n" + "\n".join(f"• {et}: {per}" for et, per in items)


def _raspuns_calendar(q_norm):
    """Raspuns PUNCTUAL pentru o sesiune/vacanta anume. Intoarce None daca intrebarea e
    generala (atunci se foloseste raspunsul complet din tabelul informatii)."""
    are_ses = 'sesiun' in q_norm
    are_vac = 'vacant' in q_norm
    if not (are_ses or are_vac):
        return None
    # Intrebare generala -> raspuns complet
    if any(k in q_norm for k in ('structur', 'detalii', 'care sunt', 'ce sesiuni', 'ce vacant',
                                 'toate', 'lista', 'organiz', 'impart')):
        return None

    iarna = any(k in q_norm for k in ('iarna', 'ianuarie', 'februarie', 'semestrul 1',
                                      'semestrul i', 'primul semestru', 'prima'))
    vara = any(k in q_norm for k in ('vara', 'iunie', 'iulie', 'semestrul 2', 'semestrul ii',
                                     'al doilea semestru', 'a doua'))

    if are_vac:
        if 'paste' in q_norm:
            return _fmt_perioade([_VACANTE['paste']])
        if iarna:
            return _fmt_perioade([_VACANTE['iarna']])
        if vara:
            return _fmt_perioade([_VACANTE['vara']])
        return None  # "vacantele" in general -> raspuns complet

    # sesiuni
    if 'sesiunea c' in q_norm or 'sesiune c' in q_norm:
        return _fmt_perioade([_SESIUNI['c']])
    vrea_b = any(k in q_norm for k in ('sesiunea b', 'sesiune b', 'restant', 'marir'))
    vrea_a = 'sesiunea a' in q_norm or 'sesiune a' in q_norm
    if vrea_b:
        if iarna:
            return _fmt_perioade([_SESIUNI['b_i']])
        if vara:
            return _fmt_perioade([_SESIUNI['b_ii']])
        return _fmt_perioade([_SESIUNI['b_i'], _SESIUNI['b_ii']])
    if vrea_a:
        if iarna:
            return _fmt_perioade([_SESIUNI['a_i']])
        if vara:
            return _fmt_perioade([_SESIUNI['a_ii']])
        return _fmt_perioade([_SESIUNI['a_i'], _SESIUNI['a_ii']])
    # fara litera explicita: "sesiunea de iarna/vara" -> sesiunea principala (A)
    if iarna:
        return _fmt_perioade([_SESIUNI['a_i']])
    if vara:
        return _fmt_perioade([_SESIUNI['a_ii']])
    return None  # "sesiunile" in general -> raspuns complet


def _raspuns_cost_examen(user_message, q_norm):
    """Cost pentru restanta (100 lei fix) sau recontractare (100 lei x credite). Pentru
    recontractare, daca se identifica o materie, afiseaza si calculul concret."""
    vrea_recontract = 'recontract' in q_norm
    vrea_restanta = 'restant' in q_norm
    vrea_cost = any(w in q_norm for w in _COST_WORDS)
    # Recontractarea implica mereu cost; restanta doar daca intrebarea e explicit despre cost
    # (ca sa nu se confunde cu "sesiunea de restante").
    if not (vrea_recontract or (vrea_restanta and vrea_cost)):
        return None

    materie = _find_best_materie(user_message)

    if vrea_recontract:
        if materie and materie['credite'] is not None:
            cr = materie['credite']
            return (f"Recontractarea disciplinei {materie['nume']} ({cr} credite) costă "
                    f"100 lei × {cr} = {100 * cr} lei (se plătesc 100 lei pentru fiecare credit).")
        return ("Recontractarea unei discipline costă 100 lei × numărul de credite al disciplinei "
                "(100 lei pentru fiecare credit). Spune-mi ce disciplină vrei să recontractezi și "
                "îți calculez suma exactă.")

    # restanta (reexaminare): taxa fixa
    if materie:
        return (f"O restanță (reexaminare) la disciplina {materie['nume']} costă 100 lei — taxă "
                "fixă, indiferent de numărul de credite.")
    return ("O restanță (reexaminare) costă 100 lei, taxă fixă indiferent de disciplină. "
            "Recontractarea unei discipline costă 100 lei × numărul ei de credite.")


def _calculeaza_raspuns(user_message, q_norm):
    """Toata logica de raspuns (retriever -> reader -> formulare), intoarce textul raspunsului."""
    global _last_materie

    # Cost restanta / recontractare (poate depinde de creditele unei materii)
    cost = _raspuns_cost_examen(user_message, q_norm)
    if cost:
        return cost

    # Intrebare punctuala despre calendar (o sesiune/vacanta anume)
    cal = _raspuns_calendar(q_norm)
    if cal:
        return cal

    # Informatii generale (absolvire, admitere, taxe...) au prioritate
    info = _match_info(q_norm)
    if info:
        return info

    # Retriever: gasim materia relevanta
    materie = _find_best_materie(user_message)

    # Follow-up: intrebare de continuare despre materia anterioara ("dar cine o preda?")
    if materie is None and _last_materie is not None and _este_followup(q_norm):
        materie = _last_materie

    # Intrebare in afara domeniului: intai incercam un mesaj dedicat (salut, multumiri,
    # orar/sali, cazare), altfel mesajul generic.
    if materie is None:
        return _mesaj_special(q_norm) or OUT_OF_DOMAIN

    _last_materie = materie  # retinem materia pentru eventuale intrebari de continuare

    # Caz special: Educatie fizica are mai multe sporturi cu titulari diferiti
    if _normalize(materie['nume']).startswith('educatie fizica') and \
            any(k in q_norm for k in ('cine', 'preda', 'titular', 'profesor', 'tine')):
        return ("Educația fizică se poate face la mai multe sporturi, cu titulari diferiți. "
                "Pentru lista completă a sporturilor consultă site-ul UVT sau scrie la infocentru.")

    # Formulam un raspuns natural in functie de intentie
    reply = _format_natural_answer(materie, q_norm)
    if reply is None:
        # Intrebare vaga ("detalii despre X", "ce imi poti spune") -> rezumat;
        # altfel, modelul extractiv (Reader) extrage raspunsul din context.
        if any(k in q_norm for k in ('detalii', 'despre', 'spune', 'informatii',
                                     'ce este', 'ce e', 'ce poti', 'disciplina', 'materia')):
            reply = _summary(materie)
        else:
            try:
                reply = process_with_ai(user_message, _build_context(materie))
            except Exception as e:
                print(f"Eroare AI: {e}")
                reply = "Momentan nu pot procesa cererea din cauza unei erori interne."
            # Intrebare ambigua: am gasit materia, dar nu e clar ce vrea utilizatorul.
            if reply is None:
                reply = (f"Am găsit disciplina {materie['nume']}, dar nu sunt sigur ce anume vrei "
                         f"să afli. Poți întreba despre credite, titular, mod de promovare, "
                         f"prezențe, ore sau anul de studiu.")

    return reply



#  ISTORIC PE SERVER: conversatiile sunt stocate in SQLite (istoric.db),
#  separate pe sesiune anonima. Fiecare browser primeste un id de sesiune intr-un cookie;
#  nu exista autentificare, dar istoricul persista pe server (nu doar in localStorage).

ISTORIC_DB = 'istoric.db'


def _istoric_conn():
    conn = sqlite3.connect(ISTORIC_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _init_istoric():
    conn = _istoric_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversatii (
            id            TEXT PRIMARY KEY,
            sesiune       TEXT NOT NULL,
            titlu         TEXT,
            creat_la      TEXT DEFAULT (datetime('now')),
            actualizat_la TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS mesaje (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            conversatie_id TEXT NOT NULL,
            rol            TEXT NOT NULL,
            text           TEXT NOT NULL,
            creat_la       TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (conversatie_id) REFERENCES conversatii(id)
        );
        CREATE INDEX IF NOT EXISTS idx_conv_sesiune ON conversatii(sesiune);
        CREATE INDEX IF NOT EXISTS idx_msg_conv ON mesaje(conversatie_id);
    """)
    conn.commit()
    conn.close()


_init_istoric()


@app.before_request
def _asigura_sesiune():
    """Citeste (sau genereaza) id-ul de sesiune anonima din cookie."""
    sid = request.cookies.get('unibot_sesiune')
    g.sesiune_noua = not sid
    g.sesiune = sid or uuid.uuid4().hex


@app.after_request
def _seteaza_cookie_sesiune(response):
    """Trimite cookie-ul de sesiune daca e nou (valabil 1 an)."""
    if getattr(g, 'sesiune_noua', False):
        response.set_cookie('unibot_sesiune', g.sesiune,
                            max_age=60 * 60 * 24 * 365, samesite='Lax', httponly=True)
    return response


def _salveaza_in_istoric(conversatie_id, user_message, reply):
    """Salveaza perechea intrebare-raspuns; creeaza o conversatie noua daca e cazul.
    Intoarce (conversatie_id, titlu)."""
    conn = _istoric_conn()
    titlu = None
    if conversatie_id:
        row = conn.execute("SELECT titlu FROM conversatii WHERE id=? AND sesiune=?",
                           (conversatie_id, g.sesiune)).fetchone()
        if row:
            titlu = row['titlu']
        else:
            conversatie_id = None  # id inexistent sau al altei sesiuni -> cream una noua
    if not conversatie_id:
        conversatie_id = uuid.uuid4().hex
        titlu = user_message[:42] + ('…' if len(user_message) > 42 else '')
        conn.execute("INSERT INTO conversatii (id, sesiune, titlu) VALUES (?,?,?)",
                    (conversatie_id, g.sesiune, titlu))
    conn.execute("INSERT INTO mesaje (conversatie_id, rol, text) VALUES (?,?,?)",
                (conversatie_id, 'user', user_message))
    conn.execute("INSERT INTO mesaje (conversatie_id, rol, text) VALUES (?,?,?)",
                (conversatie_id, 'bot', reply))
    conn.execute("UPDATE conversatii SET actualizat_la=datetime('now') WHERE id=?", (conversatie_id,))
    conn.commit()
    conn.close()
    return conversatie_id, titlu


@app.route('/get_response', methods=['POST'])
def get_response():
    data = request.get_json()
    user_message = data.get("message", "").strip()
    conversatie_id = (data.get("conversatie_id") or "").strip() or None

    if not user_message:
        return jsonify({"reply": "Te rog sa introduci o intrebare."})

    q_norm = _normalize(user_message)
    reply = _calculeaza_raspuns(user_message, q_norm)

    conversatie_id, titlu = _salveaza_in_istoric(conversatie_id, user_message, reply)
    return jsonify({"reply": reply, "conversatie_id": conversatie_id, "titlu": titlu})


@app.route('/conversatii', methods=['GET'])
def listeaza_conversatii():
    """Lista conversatiilor sesiunii curente (cele mai recente primele)."""
    conn = _istoric_conn()
    rows = conn.execute(
        "SELECT id, titlu, actualizat_la FROM conversatii WHERE sesiune=? ORDER BY actualizat_la DESC",
        (g.sesiune,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/conversatii/<cid>', methods=['GET'])
def ia_conversatie(cid):
    """Mesajele unei conversatii (doar daca apartine sesiunii curente)."""
    conn = _istoric_conn()
    conv = conn.execute("SELECT id FROM conversatii WHERE id=? AND sesiune=?",
                       (cid, g.sesiune)).fetchone()
    if not conv:
        conn.close()
        return jsonify({"error": "not found"}), 404
    msgs = conn.execute("SELECT rol, text FROM mesaje WHERE conversatie_id=? ORDER BY id",
                       (cid,)).fetchall()
    conn.close()
    return jsonify({"id": cid, "mesaje": [dict(m) for m in msgs]})


@app.route('/conversatii/<cid>', methods=['DELETE'])
def sterge_conversatie(cid):
    """Sterge o conversatie a sesiunii curente (impreuna cu mesajele ei)."""
    conn = _istoric_conn()
    conn.execute("DELETE FROM mesaje WHERE conversatie_id IN "
                 "(SELECT id FROM conversatii WHERE id=? AND sesiune=?)", (cid, g.sesiune))
    conn.execute("DELETE FROM conversatii WHERE id=? AND sesiune=?", (cid, g.sesiune))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route('/conversatii', methods=['DELETE'])
def sterge_tot_istoricul():
    """Sterge toate conversatiile sesiunii curente si compacteaza fisierul (VACUUM)."""
    conn = _istoric_conn()
    conn.execute("DELETE FROM mesaje WHERE conversatie_id IN "
                 "(SELECT id FROM conversatii WHERE sesiune=?)", (g.sesiune,))
    conn.execute("DELETE FROM conversatii WHERE sesiune=?", (g.sesiune,))
    conn.commit()
    conn.close()
    # Compactam fisierul ca spatiul eliberat sa fie returnat pe disc (VACUUM nu ruleaza
    # intr-o tranzactie -> conexiune separata in mod autocommit).
    vac = sqlite3.connect(ISTORIC_DB)
    vac.isolation_level = None
    vac.execute("VACUUM")
    vac.close()
    return jsonify({"ok": True})


if __name__ == '__main__':
    app.run(debug=True, port=5000)