"""Lecture, validation et application des réglages (contrat avec la page Réglages)."""

from __future__ import annotations

import json

from faaall_alarm.config import AppConfig, apply_config, read_config, validate_config


def fields_with_issues(values):
    return {issue.field for issue in validate_config(values)}


def test_valeurs_par_defaut_valides_et_mode_simule_par_defaut():
    config = AppConfig()
    assert validate_config(config.to_dict()) == []
    assert config.sms_mode == "simulated" and config.countdown_seconds == 10.0


def test_decompte_hors_limites_rejete_avec_message_lisible():
    issues = validate_config({"countdown_seconds": 0})
    assert [issue.field for issue in issues] == ["countdown_seconds"]
    assert "secondes" in issues[0].message
    assert validate_config({"countdown_seconds": 1}) == [] and validate_config({"countdown_seconds": 300}) == []
    assert validate_config({"countdown_seconds": 301}) != []


def test_numeros_au_format_e164():
    assert "contact_number" in fields_with_issues({"contact_number": "514-555-0123"})
    assert validate_config({"contact_number": "+15145550123", "twilio_number": "+15145550199"}) == []


def test_mode_reel_exige_activation_explicite_et_configuration_complete():
    issues = fields_with_issues({"sms_mode": "live"})
    assert {"sms_live_confirmed", "monitored_first_name", "alert_address", "contact_number", "twilio_number"} <= issues
    complete = dict(sms_mode="live", sms_live_confirmed=True, monitored_first_name="Marie", alert_address="123, rue Exemple",
                    contact_number="+15145550123", twilio_number="+15145550199")
    assert validate_config(complete) == []


def test_mode_sms_inconnu_et_zones_invalides():
    assert "sms_mode" in fields_with_issues({"sms_mode": "reel"})
    assert "monitored_zone" in fields_with_issues({"monitored_zone": [0.5, 0.5, 0.2, 0.9]})
    assert "lying_zones" in fields_with_issues({"lying_zones": [[0, 0, 2, 1]]})
    assert validate_config({"lying_zones": [[0.1, 0.7, 0.5, 0.95]]}) == []


def test_apply_ecrit_valide_et_notifie(tmp_path):
    notified = []
    path = tmp_path / "config.local.json"
    result = apply_config({"countdown_seconds": 15, "monitored_first_name": "Marie"}, path, notified.append)
    assert result.ok and notified[0].countdown_seconds == 15
    assert json.loads(path.read_text(encoding="utf-8"))["monitored_first_name"] == "Marie"
    assert read_config(path, use_environment=False).countdown_seconds == 15


def test_apply_n_ecrit_rien_si_invalide(tmp_path):
    path = tmp_path / "config.local.json"
    result = apply_config({"countdown_seconds": 999}, path)
    assert not result.ok and result.issues[0].field == "countdown_seconds" and not path.exists()


def test_aucun_secret_n_est_ecrit_dans_le_fichier_local(tmp_path, monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "jeton-factice-ne-pas-ecrire")
    monkeypatch.setenv("OPENROUTER_API_KEY", "cle-factice-ne-pas-ecrire")
    path = tmp_path / "config.local.json"
    apply_config({"countdown_seconds": 12}, path)
    content = path.read_text(encoding="utf-8")
    assert "factice" not in content and "token" not in content.lower()


def test_lecture_priorite_fichier_sur_environnement(tmp_path, monkeypatch):
    monkeypatch.setenv("FAAALL_MONITORED_FIRST_NAME", "DepuisEnv")
    path = tmp_path / "config.local.json"
    assert read_config(path).monitored_first_name == "DepuisEnv"
    path.write_text(json.dumps({"monitored_first_name": "DepuisFichier"}), encoding="utf-8")
    assert read_config(path).monitored_first_name == "DepuisFichier"


def test_lecture_tolere_un_fichier_illisible_ou_invalide(tmp_path):
    path = tmp_path / "config.local.json"
    path.write_text("{pas du json", encoding="utf-8")
    assert read_config(path, use_environment=False) == AppConfig()
    path.write_text(json.dumps({"countdown_seconds": 999, "contact_name": "Sophie"}), encoding="utf-8")
    config = read_config(path, use_environment=False)
    assert config.countdown_seconds == 10.0 and config.contact_name == "Sophie"
