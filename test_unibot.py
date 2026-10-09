# -*- coding: utf-8 -*-
"""
Teste automate pentru UniBot (Etapa 5).

Ruleaza:  python test_unibot.py            (sau: python -m unittest -v test_unibot)

Acopera: retriever-ul, abrevierile, detectia de follow-up, mesajele speciale
(salut/orar/cazare), informatiile generale, formularea raspunsului, endpoint-urile HTTP,
istoricul pe server (Etapa 4) si integritatea bazei de date.

Testele NU incarca modelul neural (~1.1 GB): logica testata e determinista si nu are nevoie
de el (variabila UNIBOT_SKIP_MODEL=1, setata mai jos inainte de import).
"""
import os
import sys
import tempfile
import unicodedata
import unittest

# Rulam din folderul proiectului (ca sa gasim facultate.db) si sarim peste model.
os.chdir(os.path.dirname(os.path.abspath(__file__)))
os.environ['UNIBOT_SKIP_MODEL'] = '1'

import app  # noqa: E402
import sqlite3  # noqa: E402

# Istoricul de test merge intr-o baza temporara, ca sa nu atinga istoric.db real.
app.ISTORIC_DB = os.path.join(tempfile.gettempdir(), 'unibot_test_istoric.db')
if os.path.exists(app.ISTORIC_DB):
    os.remove(app.ISTORIC_DB)
app._init_istoric()


def _fn(s):
    """Fara diacritice + litere mici (pentru asertii robuste)."""
    return unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()


def _norm(s):
    return app._normalize(s)


def _resp(msg):
    """Raspunsul complet al pipeline-ului pentru un mesaj (fara stare de follow-up)."""
    app._last_materie = None
    return app._calculeaza_raspuns(msg, _norm(msg))


# ============================================================================
class TestRetriever(unittest.TestCase):
    """Componenta Retriever: gaseste materia corecta din intrebare."""

    def _nume(self, intrebare):
        m = app._find_best_materie(intrebare)
        return m['nume'] if m else None

    def test_nume_complet(self):
        self.assertEqual(self._nume('Cate credite are Baze de date?'), 'Baze de date')

    def test_diacritice_lipsa(self):
        self.assertEqual(_fn(self._nume('cine preda inteligenta artificiala')),
                         _fn('Inteligență artificială'))

    def test_numeral_roman(self):
        self.assertEqual(self._nume('Cine preda Programare I?'), 'Programare I')

    def test_abreviere_3_litere(self):
        self.assertEqual(self._nume('Cate credite are ASD?'),
                         'Algoritmi și structuri de date I')

    def test_abreviere_compacta_so_ii(self):
        self.assertEqual(self._nume('Cate credite are SO II?'), 'Sisteme de operare II')

    def test_abreviere_scurta_BD_majuscule(self):
        self.assertEqual(self._nume('Cate credite are BD?'), 'Baze de date')

    def test_abreviere_scurta_bd_minuscule_nu_forteaza(self):
        # "bd" cu litere mici NU trebuie sa potriveasca (abrevierile scurte cer majuscule)
        self.assertNotEqual(self._nume('cate credite are bd'), 'Baze de date')

    def test_out_of_domain(self):
        self.assertIsNone(self._nume('Care e capitala Frantei?'))


# ============================================================================
class TestFollowup(unittest.TestCase):
    """Detectia intrebarilor de continuare."""

    def test_pronume_o(self):
        self.assertTrue(app._este_followup(_norm('dar cine o preda')))

    def test_atribut_gol(self):
        self.assertTrue(app._este_followup(_norm('si cate credite')))

    def test_pronume_ea(self):
        self.assertTrue(app._este_followup(_norm('dar ea')))

    def test_materie_necunoscuta_nu_e_followup(self):
        self.assertFalse(app._este_followup(_norm('cate credite are poo')))

    def test_pizza_nu_e_followup(self):
        # Bug "o" reparat: "o" din "o pizza" nu mai forteaza follow-up (exista "pizza")
        self.assertFalse(app._este_followup(_norm('vreau sa comand o pizza')))


# ============================================================================
class TestMesajeSpeciale(unittest.TestCase):
    """Raspunsuri dedicate pentru salut/multumiri/orar/cazare."""

    def test_salut(self):
        self.assertIn('unibot', _fn(app._mesaj_special(_norm('Salut!'))))

    def test_multumiri(self):
        self.assertIn('placere', _fn(app._mesaj_special(_norm('Mersi mult!'))))

    def test_orar(self):
        self.assertIn('orar', _fn(app._mesaj_special(_norm('Unde e sala 045?'))))

    def test_cazare(self):
        self.assertIn('camin', _fn(app._mesaj_special(_norm('Vreau cazare la camin'))))

    def test_fara_categorie(self):
        self.assertIsNone(app._mesaj_special(_norm('Care e capitala Frantei?')))


# ============================================================================
class TestInfoGenerale(unittest.TestCase):
    """Informatii generale (tabelul informatii)."""

    def test_taxe(self):
        self.assertIn('4900', app._match_info(_norm('Cat e taxa de scolarizare?')))

    def test_taxe_plural(self):
        r = app._match_info(_norm('Ce taxe sunt la facultate?'))
        self.assertIsNotNone(r)
        self.assertIn('4900', r)

    def test_taxe_contine_restanta_recontractare(self):
        r = app._match_info(_norm('Cat e taxa de scolarizare?'))
        self.assertIn('restant', _fn(r))
        self.assertIn('recontract', _fn(r))

    def test_admitere(self):
        self.assertIn('concurs', _fn(app._match_info(_norm('Cum se intra la Informatica?'))))

    def test_licenta(self):
        self.assertIn('licen', _fn(app._match_info(_norm('Cum se da examenul de licenta?'))))

    def test_absolvire(self):
        self.assertIn('absolv', _fn(app._match_info(_norm('Care sunt conditiile de absolvire?'))))

    def test_structura_an(self):
        r = app._match_info(_norm('Cum e structurat anul universitar?'))
        self.assertIsNotNone(r)
        self.assertIn('saptamani', _fn(r))

    def test_fara_info(self):
        self.assertIsNone(app._match_info(_norm('Cine preda Baze de date?')))


# ============================================================================
class TestFormulareRaspuns(unittest.TestCase):
    """Formularea raspunsului natural pentru o materie."""

    @classmethod
    def setUpClass(cls):
        cls.bd = app._find_best_materie('Baze de date')

    def test_credite(self):
        r = app._format_natural_answer(self.bd, _norm('cate credite are'))
        self.assertIn('5 credite', _fn(r))

    def test_titular(self):
        r = app._format_natural_answer(self.bd, _norm('cine o preda'))
        self.assertIn('pop', _fn(r))

    def test_prezente(self):
        r = app._format_natural_answer(self.bd, _norm('cate prezente'))
        self.assertIn('curs', _fn(r))
        self.assertIn('laborator', _fn(r))

    def test_an_semestru(self):
        r = app._format_natural_answer(self.bd, _norm('in ce an'))
        self.assertIn('anul 2', _fn(r))


# ============================================================================
class TestEndpointRaspuns(unittest.TestCase):
    """Endpoint-ul /get_response, end-to-end (fara model)."""

    def setUp(self):
        app._last_materie = None
        self.client = app.app.test_client()

    def _reply(self, msg, cid=None):
        r = self.client.post('/get_response', json={'message': msg, 'conversatie_id': cid})
        return r.get_json()

    def test_credite_bd(self):
        d = self._reply('Cate credite are BD?')
        self.assertIn('5 credite', _fn(d['reply']))
        self.assertTrue(d['conversatie_id'])

    def test_salut(self):
        self.assertIn('unibot', _fn(self._reply('Buna ziua')['reply']))

    def test_out_of_domain(self):
        self.assertIn('pot raspunde doar', _fn(self._reply('Vreau sa comand o pizza')['reply']))

    def test_lant_followup(self):
        d1 = self._reply('Cate credite are Programare I?')
        d2 = self._reply('Dar cine o preda?', cid=d1['conversatie_id'])
        self.assertIn('bonchis', _fn(d2['reply']))

    def test_mesaj_gol(self):
        self.assertIn('introduci', _fn(self._reply('')['reply']))


# ============================================================================
class TestIstoricServer(unittest.TestCase):
    """Etapa 4: istoric pe server, sesiuni anonime, CRUD."""

    def test_creare_si_listare(self):
        c = app.app.test_client()
        self.assertEqual(c.get('/conversatii').get_json(), [])
        cid = c.post('/get_response', json={'message': 'Cate credite are BD?'}).get_json()['conversatie_id']
        lista = c.get('/conversatii').get_json()
        self.assertEqual(len(lista), 1)
        self.assertEqual(lista[0]['id'], cid)

    def test_mesaje_persistate(self):
        c = app.app.test_client()
        cid = c.post('/get_response', json={'message': 'Cine preda Programare I?'}).get_json()['conversatie_id']
        c.post('/get_response', json={'message': 'Dar cine o preda?', 'conversatie_id': cid})
        mesaje = c.get('/conversatii/' + cid).get_json()['mesaje']
        self.assertEqual(len(mesaje), 4)  # 2 perechi user+bot
        self.assertEqual(mesaje[0]['rol'], 'user')
        self.assertEqual(mesaje[1]['rol'], 'bot')

    def test_stergere_o_conversatie(self):
        c = app.app.test_client()
        cid = c.post('/get_response', json={'message': 'Cate credite are BD?'}).get_json()['conversatie_id']
        c.delete('/conversatii/' + cid)
        self.assertEqual(c.get('/conversatii').get_json(), [])

    def test_stergere_totala(self):
        c = app.app.test_client()
        c.post('/get_response', json={'message': 'Cate credite are BD?'})
        c.post('/get_response', json={'message': 'Cine preda ASD?'})
        self.assertEqual(len(c.get('/conversatii').get_json()), 2)
        c.delete('/conversatii')
        self.assertEqual(c.get('/conversatii').get_json(), [])

    def test_izolare_sesiuni(self):
        c1 = app.app.test_client()
        c1.post('/get_response', json={'message': 'Cate credite are BD?'})
        c2 = app.app.test_client()  # sesiune noua (alt cookie)
        self.assertEqual(c2.get('/conversatii').get_json(), [])

    def test_conversatie_altei_sesiuni_inaccesibila(self):
        c1 = app.app.test_client()
        cid = c1.post('/get_response', json={'message': 'Cate credite are BD?'}).get_json()['conversatie_id']
        c2 = app.app.test_client()
        self.assertEqual(c2.get('/conversatii/' + cid).status_code, 404)


# ============================================================================
class TestIntegritateDB(unittest.TestCase):
    """Integritatea bazei de date facultate.db."""

    @classmethod
    def setUpClass(cls):
        cls.conn = sqlite3.connect('facultate.db')
        cls.conn.row_factory = sqlite3.Row

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def test_numar_materii(self):
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM materii").fetchone()[0], 77)

    def test_o_prezenta_per_materie(self):
        nr_mat = self.conn.execute("SELECT COUNT(*) FROM materii").fetchone()[0]
        nr_prez = self.conn.execute("SELECT COUNT(*) FROM prezenta").fetchone()[0]
        self.assertEqual(nr_mat, nr_prez)

    def test_informatii_minim(self):
        self.assertGreaterEqual(self.conn.execute("SELECT COUNT(*) FROM informatii").fetchone()[0], 12)

    def test_bd_abreviere_corecta(self):
        r = self.conn.execute("SELECT abreviere, credite FROM materii WHERE nume='Baze de date'").fetchone()
        self.assertEqual(r['abreviere'], 'BD')
        self.assertEqual(r['credite'], 5)

    def test_exceptie_prezenta_so1(self):
        r = self.conn.execute(
            "SELECT p.prezente_curs FROM prezenta p JOIN materii m ON m.id=p.materie_id "
            "WHERE m.nume='Sisteme de operare I'").fetchone()
        self.assertIn('nu este obligatorie', _fn(r['prezente_curs']))

    def test_exceptie_prezenta_inginerie(self):
        r = self.conn.execute(
            "SELECT p.prezente_curs FROM prezenta p JOIN materii m ON m.id=p.materie_id "
            "WHERE m.nume LIKE 'Inginerie%'").fetchone()
        self.assertIn('minim 10', _fn(r['prezente_curs']))

    def test_titular_programare1(self):
        r = self.conn.execute("SELECT titular FROM materii WHERE nume='Programare I'").fetchone()
        self.assertIn('bonchis', _fn(r['titular']))


# ============================================================================
class TestCalendarPunctual(unittest.TestCase):
    """Raspunsuri PUNCTUALE la intrebari despre o sesiune/vacanta anume vs. cele generale."""

    def test_sesiunea_iarna_punctual(self):
        r = _resp('Când începe sesiunea de iarnă?')
        self.assertIn('17.01.2026', r)         # A-I
        self.assertNotIn('06.06.2026', r)      # nu si vara -> raspuns tintit

    def test_sesiunea_vara_punctual(self):
        r = _resp('Când începe sesiunea de vară?')
        self.assertIn('06.06.2026', r)
        self.assertNotIn('17.01.2026', r)

    def test_sesiunea_b(self):
        r = _resp('Când începe sesiunea B?')
        self.assertIn('16.02.2026', r)         # B-I
        self.assertIn('02.07.2026', r)         # B-II
        self.assertNotIn('17.01.2026', r)      # nu si sesiunea A

    def test_sesiunea_c_doar_terminal(self):
        r = _resp('Când e sesiunea C?')
        self.assertIn('22.07.2026', r)
        self.assertIn('terminal', _fn(r))      # mentioneaza ca e doar pentru anul 3

    def test_vacanta_vara_punctual(self):
        r = _resp('În ce dată începe vacanța de vară?')
        self.assertIn('09.07.2026', r)
        self.assertNotIn('20.12.2025', r)      # nu si iarna

    def test_general_ramane_complet(self):
        # Intrebare generala -> raspuns complet (ambii ani + sesiunile)
        r = _resp('Spune-mi cum e structura anului universitar')
        self.assertIn('terminal', _fn(r))
        self.assertIn('12 saptamani', _fn(r))


# ============================================================================
class TestCostExamen(unittest.TestCase):
    """Cost restanta (100 lei) si recontractare (100 lei x credite), cu calcul afisat."""

    def test_recontractare_cu_materie(self):
        r = _resp('Cât costă să recontractez Baze de date?')  # BD are 5 credite
        self.assertIn('100 lei × 5', r)
        self.assertIn('500 lei', r)

    def test_recontractare_alta_materie(self):
        r = _resp('Cât costă recontractarea la Programare I?')  # 6 credite
        self.assertIn('600 lei', r)

    def test_recontractare_fara_materie(self):
        r = _resp('Cât costă o recontractare?')
        self.assertIn('100 lei', r)
        self.assertIn('credite', _fn(r))

    def test_restanta_cost(self):
        r = _resp('Cât costă o restanță?')
        self.assertIn('100 lei', r)

    def test_restanta_nu_e_confundata_cu_sesiunea(self):
        # "sesiunea de restante" fara cuvant de cost -> NU raspuns de cost, ci datele sesiunii B
        r = _resp('Când e sesiunea de restanțe?')
        self.assertIn('16.02.2026', r)


if __name__ == '__main__':
    unittest.main(verbosity=2)
