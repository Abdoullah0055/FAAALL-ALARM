# FAAALL-ALARM · Lot 2

La V1 autonome ouvre les réglages Windows et permet de simuler une chute, son annulation et l’aller-retour SMS. La surveillance caméra, la détection et la voix à l’ouverture d’un incident sont à intégrer avec Adam.

## Essayer sous Windows

Depuis PowerShell, avec Python 3.13 et Tcl/Tk installés.

```powershell
Set-Location "C:\Portfolio Prog\FAAALL-ALARM"
py -3.13 -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".\.venv\Scripts\python.exe" -m faaall_alarm
```

Dans l’onglet **Simulation**, cliquez sur **Simuler une chute**, attendez le délai configuré, puis sélectionnez **Simuler la réponse**. Le SMS affiche **Simulé**, puis la réponse affiche **En route · Arrivée annoncée dans environ 20 min**. Rejouez et utilisez **Annuler l’alerte** avant l’échéance pour vérifier le deuxième parcours. Vous pouvez remplacer la réponse fictive par « OK » ou « Je ne peux pas venir ».

```powershell
& ".\.venv\Scripts\python.exe" -m faaall_alarm --demo
& ".\.venv\Scripts\python.exe" -m unittest discover -s tests -v
Start-Process ".\site\index.html"
```

Python 3.13.3 et Tk 8.6 ont servi à vérifier le lot. OpenCV et MediaPipe ne sont pas requis pour cette page autonome. Leur version et leur compatibilité restent à vérifier lors de l’intégration vision.

## Configurer les services réels

Copiez `.env.example` dans `.env` si le fichier local n’existe pas. Remplissez-le sur le poste. Redémarrez l’application après changement de clé. Les réglages enregistrés priment sur les valeurs initiales de configuration.

- **Twilio** demande `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` et `FAAALL_SMS_TO_NUMBER`. Pour enregistrer la configuration d’alerte réelle, indiquez aussi `FAAALL_MONITORED_FIRST_NAME`, `FAAALL_ALERT_ADDRESS` et `FAAALL_CONTACT_NAME`, dans le fichier ou dans la page. Les numéros sont au format international. Utilisez un compte permettant les messages personnalisés et un numéro compatible avec le SMS bidirectionnel. [Numéros et SMS au Canada](https://www.twilio.com/en-us/guidelines/ca/sms) · [Limites de l’essai Twilio](https://www.twilio.com/docs/usage/trials)
- **OpenRouter** demande `OPENROUTER_API_KEY`. `OPENROUTER_MODEL` est facultatif et vaut `openai/gpt-4o-mini` par défaut. Ce modèle accepte une sortie JSON Schema. L’interpréteur n’envoie que le texte utile du SMS associé, après masquage des coordonnées connues. Le texte libre peut contenir d’autres renseignements personnels. [Sorties structurées](https://openrouter.ai/docs/guides/features/structured-outputs) · [Modèle par défaut](https://openrouter.ai/openai/gpt-4o-mini)
- **Voix** demande [eSpeak NG](https://github.com/espeak-ng/espeak-ng/releases) installé sur Windows. S’il n’est pas dans le PATH, indiquez le chemin de son exécutable dans **Voix et délai**, puis cliquez sur **Tester le son** et écoutez depuis la zone surveillée.

Une fois le téléphone et l’envoi convenus avec le proche, suspendez la surveillance et réservez la conversation au test pendant sa durée. Choisissez **Réel** dans l’onglet **SMS**, puis **Vérifier le canal**. Cette action vérifie l’accès API sans envoyer. Cliquez une fois sur **Envoyer un SMS de test réel**, vérifiez les quatre derniers chiffres et confirmez. Gardez la fenêtre ouverte, vérifiez **Livré** si le transporteur le confirme et répondez depuis le téléphone avec « J’arrive dans 20 minutes ». La page consulte les messages pendant 30 minutes. Sans clé OpenRouter, la page affiche **Réponse reçue, à vérifier**. La simulation fonctionne entièrement hors ligne.

Les réglages et les bases locales sont dans `%LOCALAPPDATA%\FAAALL-ALARM`. Les bases contiennent les identifiants d’incident et de message, les horodatages, les états et une empreinte du numéro du proche. Gardez la base SMS pendant la reprise d’incidents pour prévenir les doublons. Le texte des réponses et le corps du SMS ne sont pas conservés dans ces bases.

- [Contrat d’intégration pour Adam](docs/integration-lot-2.md)
- [Corpus, annotations et accès](docs/corpus.md)
