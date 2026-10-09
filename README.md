# UniBot - University Virtual Assistant 🎓
*(Scroll down for the detailed Romanian documentation)*

**UniBot** is an educational NLP-based chatbot built for the Faculty of Computer Science (UVT). It is designed to answer natural language queries (in Romanian) regarding courses, credits, professors, schedules, and general academic rules.

### 🚀 Tech Stack & Key Features
- **Backend & API:** Python, Flask
- **Data & Databases:** SQLite, Data Extraction from Excel (ETL)
- **Machine Learning / NLP:** Hugging Face Transformers (`deepset/xlm-roberta-base-squad2`), PyTorch
- **Architecture:** Retriever-Reader paradigm designed to eliminate AI hallucinations by prioritizing structured database queries.
- **Quality Assurance:** 59 automated test suites.

---


# UniBot — Asistent virtual pentru Facultatea de Informatică (UVT)

UniBot este un chatbot educațional bazat pe procesarea limbajului natural, care răspunde în
limbaj natural (în limba română) la întrebări despre programul de studii Informatică:
discipline, credite, cadre didactice, mod de promovare, prezențe obligatorii, precum și
informații generale despre admitere, taxe, calendarul academic, condițiile de absolvire și
examenul de licență.

## Informații despre lucrare

- Lucrare de licență — Programul de studii: Informatică
- Universitatea de Vest din Timișoara
- Temă: Dezvoltarea unui chatbot educațional folosind procesarea limbajului natural
- Autor: Șușman Andrei-Alexandru
- Coordonator științific: Prof. Dr. Darian Onchiș

Notă: aplicația este destinată rulării locale și nu este publicată.

## Funcționalități

- Răspunsuri la întrebări despre o disciplină: credite, cadru didactic titular, mod de
  promovare, prezențe obligatorii, an și semestru, număr de ore, tip de disciplină.
- Răspunsuri la întrebări generale: admitere, taxe, sesiuni de examene, vacanțe, structura
  anului universitar, condiții de absolvire, examenul de licență, discipline complementare.
- Recunoașterea abrevierilor (de exemplu BD, ASD) și a formelor scrise fără diacritice.
- Răspunsuri punctuale despre calendarul academic (o sesiune sau o vacanță anume) și răspunsuri
  complete la întrebările generale despre structura anului.
- Răspunsuri calculate, cum ar fi costul de recontractare a unei discipline (în funcție de
  numărul de credite).
- Tratarea întrebărilor de continuare, a saluturilor și a întrebărilor din afara domeniului.
- Interfață web cu istoric al conversațiilor stocat pe server, teme luminoasă/întunecată,
  căutare în istoric și afișare responsivă.

## Arhitectură

Aplicație pe niveluri, organizată după paradigma Retriever–Reader, cu un strat suplimentar de
formulare a răspunsului pe bază de șabloane:

- Componenta Retriever selectează din baza de date disciplina relevantă pentru întrebare
  (normalizare a diacriticelor, potrivire pe nume și pe abreviere) și construiește un context.
- Stratul de formulare produce răspunsuri naturale direct din datele structurate, pentru
  intențiile clare.
- Componenta Reader, un model extractiv de tip question answering
  (`deepset/xlm-roberta-base-squad2`), este folosită ca rezervă, extrăgând răspunsul din context.

Modelul neuronal este folosit doar ca rezervă, iar răspunsurile provin întotdeauna din
informația verificată, evitând generarea liberă de text și riscul de halucinație.

## Structura pachetului

```
Lucrare_de_Licenta_Susman_Andrei_Alexandru/
├── app.py              Serverul Flask: retriever, reader, formulare, endpoint-uri, istoric
├── setup_db.py         Script de (re)construire a bazei de date facultate.db
├── test_unibot.py      Suită de teste automate (59 de teste)
├── requirements.txt    Dependințele Python
├── facultate.db        Baza de cunoștințe SQLite (77 discipline + informații generale)
├── UniBot_Date_UVT.xlsx  Fișierul-sursă cu datele colectate (planul de învățământ)
├── README.md           Acest fișier
├── templates/
│   └── index.html      Interfața web (chat, sidebar, istoric, teme)
└── static/             Logo, mascotă și favicon (SVG)
```

Fișierul `UniBot_Date_UVT.xlsx` conține datele colectate din planurile de învățământ și din fișele
de disciplină: cele 77 de discipline, cu cod, denumire, abreviere, an, semestru, categorie, tip,
credite, numărul de ore, cadrul didactic titular și modul de promovare. Este singura sursă externă
de date a aplicației, din care `setup_db.py` construiește baza de date.

Restul conținutului bazei de date este generat de `setup_db.py`: regulile de prezență obligatorie
sunt derivate automat din numărul de ore ale fiecărei discipline (cu excepțiile prevăzute în fișe),
iar răspunsurile la întrebările generale (admitere, taxe, calendar academic, absolvire) sunt
definite în script, fiind texte redactate, nu date tabelare.

Baza de date a istoricului conversațiilor (`istoric.db`) nu este inclusă, deoarece este creată
automat de aplicație la prima pornire.

## Cerințe

- Python 3.10 sau mai nou (testat pe Python 3.14, Windows).
- Dependințele din `requirements.txt`.

## Instalare și rulare

```
pip install -r requirements.txt
python app.py
```

Apoi se deschide în browser adresa `http://localhost:5000`.

La prima rulare, modelul neuronal (`deepset/xlm-roberta-base-squad2`, aproximativ 1,1 GB) se
descarcă automat de pe Hugging Face și se încarcă în memorie; acest pas poate dura între 30 și
60 de secunde.

Baza de cunoștințe `facultate.db` este inclusă deja construită, astfel încât aplicația poate fi
rulată direct, fără niciun pas suplimentar de configurare.

Scriptul `setup_db.py` este necesar doar dacă se dorește reconstruirea bazei de date din
fișierul-sursă `UniBot_Date_UVT.xlsx`, inclus în pachet:

```
python setup_db.py
```

Dacă fișierul-sursă lipsește, scriptul se oprește cu un mesaj explicativ și nu modifică baza de
date existentă.

## Testare

Pachetul include o suită de teste automate care verifică componenta Retriever, abrevierile,
detecția întrebărilor de continuare, mesajele dedicate, informațiile generale, formularea
răspunsului, răspunsurile despre calendar, costurile calculate, endpoint-urile HTTP, istoricul
pe server și integritatea bazei de date.

```
python test_unibot.py
```

Testele nu încarcă modelul neuronal (rulează în câteva zecimi de secundă), deoarece logica
verificată este deterministă.

## Limitări cunoscute

- Domeniul este restrâns intenționat la specializarea Informatică (limba română).
- Orarul și sălile sunt excluse, deoarece se modifică frecvent.
- Abrevierile de două litere sunt recunoscute atunci când sunt scrise cu majuscule.
- Toleranța la greșeli de tastare este limitată.

## Tehnologii

Python, Flask, SQLite, PyTorch, Hugging Face Transformers
(`deepset/xlm-roberta-base-squad2`), HTML, CSS, JavaScript (Fetch API).
