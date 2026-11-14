"""Masquage des données sensibles, journal de session et historique local."""

from __future__ import annotations

from datetime import datetime

from faaall_alarm.clock import ManualClock
from faaall_alarm.logbook import DetectionLog, HistoryStore, format_entry, LogEntry
from faaall_alarm.privacy import MASK, SensitiveMasker, mask_sensitive


def test_numero_international_et_national_masques():
    assert mask_sensitive("Contact +15145550123 joint") == f"Contact {MASK} joint"
    assert mask_sensitive("appel 514 555 0123 ok") == f"appel {MASK} ok"


def test_heures_et_durees_ne_sont_pas_masquees():
    text = "14:32:07 [vidéo 00:06.5] décompte 10 s · incident INC-001-14h32"
    assert mask_sensitive(text) == text


def test_jetons_et_cles_masques_meme_dans_un_message_d_erreur_du_fournisseur():
    sid = "AC" + "a1" * 16
    assert sid not in mask_sensitive(f"Erreur Twilio pour {sid}")
    assert "sk-abcdefghijklmnopqrstuv" not in mask_sensitive("clé sk-abcdefghijklmnopqrstuv refusée")
    assert "hunter2" not in mask_sensitive("auth_token=hunter2 invalide")


def test_valeurs_enregistrees_comme_l_adresse_sont_masquees():
    masker = SensitiveMasker()
    masker.register("123, rue Exemple, Montréal")
    assert "rue Exemple" not in masker.mask("Envoi vers 123, rue Exemple, Montréal demandé")


def test_tout_copier_contient_toute_la_session_y_compris_hors_ecran():
    clock = ManualClock()
    log = DetectionLog(clock)
    for index in range(1000):
        clock.advance(1.0)
        log.add(f"événement {index}")
    lines = log.export_text().splitlines()
    assert len(lines) == 1000 and lines[0].endswith("événement 0") and lines[-1].endswith("événement 999")


def test_le_texte_copie_est_masque_meme_pour_un_secret_enregistre_apres_coup():
    masker = SensitiveMasker()
    log = DetectionLog(ManualClock(), masker)
    log.add("Envoi pour Sophie au code-secret-1234")
    masker.register("code-secret-1234")
    assert "code-secret-1234" not in log.export_text()


def test_entrees_horodatees_hh_mm_ss_avec_temps_video_en_mp4():
    entry = LogEntry(datetime(2026, 1, 15, 14, 32, 7), "Personne : FALLING", 12.4)
    assert format_entry(entry) == "14:32:07 [vidéo 00:12.4] Personne : FALLING"
    assert format_entry(LogEntry(datetime(2026, 1, 15, 14, 32, 7), "Caméra active")) == "14:32:07 Caméra active"


def test_historique_local_masque_enregistre_et_s_efface(tmp_path):
    masker = SensitiveMasker()
    store = HistoryStore(tmp_path / "data" / "history.jsonl", "simulé", ManualClock(), masker)
    store.record("sms", durable=True, incident="INC-001-14h32", statut="Simulé", erreur="échec vers +15145550123")
    records = store.read()
    assert records[0]["mode"] == "simulé" and records[0]["evenement"] == "sms"
    assert "+1514" not in str(records)
    store.clear()
    assert store.read() == []
