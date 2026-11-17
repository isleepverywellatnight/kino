# Mises à jour de KINO

## Où KINO cherche

Au démarrage, puis à chaque « Rechercher une mise à jour » (Paramètres →
Mises à jour), KINO lit la dernière release de `UPDATE_REPO`
(`crates/kino/src/update.rs`). La CI y publie, à chaque nouvelle version sur `main` :

- macOS : `KINO-<version>-arm64.kinoupdate`, un manifeste JSON signé en
  Ed25519 et une archive de `KINO.app` ;
- Windows : `KINO-Windows.exe` et sa description signée `KINO-Windows.exe.sig`.

La clé publique est livrée avec l'app (`assets/update_public_key.hex`). La clé
privée correspondante n'existe que dans le secret `KINO_UPDATE_SIGNING_KEY` de
la CI : la remplacer rendrait les versions déjà installées incapables de se
mettre à jour. Sans ce secret, la CI publie sans paquets de mise à jour.

Sur macOS, KINO vérifie la signature, la taille et le SHA-256 de l'archive,
l'identité du bundle, l'architecture, la signature de code et `--self-check`
avant toute installation. Un paquet `.kinoupdate` posé dans `~/Downloads` ou
`~/Library/Application Support/KINO/Updates/inbox` est aussi accepté.

Sur Windows, l'installateur téléchargé est comparé à sa description signée,
puis lancé sans fenêtre ; il attend la fermeture de KINO et le relance.

## Installation et retour arrière (macOS)

KINO prépare le paquet sans fermer le lecteur. Si une lecture est active,
l'installation attend sa fermeture. Le lecteur intégré signale chaque seconde
sa Playback Session (`playing`, même en pause) ; la garde considère la lecture
terminée cinq secondes après le dernier signal. La demande d'installation
ferme ensuite KINO et lance l'assistant natif copié hors du bundle. L'assistant place la
nouvelle app à côté de l'ancienne dans `/Applications`, échange les bundles
avec `renameatx_np(RENAME_SWAP)`, exécute `--self-check`, lance la nouvelle
fenêtre et attend sa confirmation de chargement. Un échec restaure l'ancien
bundle et le relance.

Sur macOS, KINO garde un verrou de lancement pendant toute sa session. Une
seconde ouverture se termine avant de créer un autre serveur ou une autre
fenêtre, et tente de remettre l'instance existante au premier plan. Le verrou
est libéré à la fermeture, ce qui permet le redémarrage par l'assistant.

Avant l'échange, KINO sauvegarde la base SQLite avec l'API de backup SQLite,
ainsi que les fichiers de configuration et de playlist existants, dans
`~/Library/Application Support/KINO/Updates/data-*` (permissions privées).
L'ancienne app est conservée comme `/Applications/.KINO.backup-*.app` : ce
nom masqué évite de rajouter un résultat Spotlight. « Revenir à cette version »
effectue le même échange atomique et conserve les données récentes.

Au démarrage, l'app installée nettoie les restes des mises à jour précédentes
(`kino_update.prune_update_leftovers`) : elle garde le bundle de retour arrière
nommé dans `status.json`, la sauvegarde d'app la plus récente en plus et les
trois dernières copies de données, et supprime les apps préparées, les fichiers
de travail de l'assistant et les paquets de la boîte de réception qui ne sont
pas plus récents qu'elle. Rien n'est supprimé pendant une confirmation en cours.

Le retour arrière n'est proposé que pour le même `DATA_SCHEMA` (aujourd'hui
**1**) ; une mise à jour d'un autre schéma est refusée.

## Construire un paquet à la main

```sh
cargo run --release -p kino --bin kino-release -- --app dist/KINO.app --key <clé privée.pem> \
  --output dist/KINO-<version>-arm64.kinoupdate --notes "KINO <version>"
```
