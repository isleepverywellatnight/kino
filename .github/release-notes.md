## Télécharger KINO {{VERSION}}

| Système | Fichier |
|---|---|
| **macOS** (Mac Apple Silicon, macOS 14 ou plus récent) | [KINO-macOS.dmg](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-macOS.dmg) |
| **Windows** (10 ou 11, 64 bits) | [KINO-Windows.exe](https://github.com/isleepverywellatnight/kino/releases/latest/download/KINO-Windows.exe) |

Tout est inclus : rien d'autre à installer. Les versions suivantes s'installent depuis KINO (Paramètres → Mises à jour).

**Encore plus simple** : une commande installe ou met à jour KINO, sans aucun avertissement du système.

- macOS (app Terminal) : `curl -fsSL https://raw.githubusercontent.com/isleepverywellatnight/kino/main/packaging/install.sh | bash`
- Windows (PowerShell) : `irm https://raw.githubusercontent.com/isleepverywellatnight/kino/main/packaging/install.ps1 | iex`

<details><summary>Premier lancement après un téléchargement depuis le navigateur</summary>

KINO n'est pas encore signé par Apple ni par Microsoft.

- **macOS** : ouvrez le .dmg et glissez KINO dans Applications. Si macOS refuse de l'ouvrir : Réglages Système → Confidentialité et sécurité → **Ouvrir quand même**.
- **Windows** : si « Windows a protégé votre ordinateur » s'affiche, cliquez sur **Informations complémentaires** puis **Exécuter quand même**. L'installation se fait ensuite en un clic, sans droits administrateur.
</details>

Empreintes SHA-256 : `SHA256SUMS.txt`.
