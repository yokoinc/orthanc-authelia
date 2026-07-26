# Personnalisation de la page de connexion Authelia

Ce dossier est le `asset_path` d'Authelia (`configuration.yml`, section
`server`). Authelia n'y lit que trois choses, et rien d'autre :

```
assets/
├── favicon.ico                       icône d'onglet
├── logo.png                          logo affiché sur la page de connexion
└── locales/<langue>/portal.json      textes du portail
```

Référence : https://www.authelia.com/reference/guides/server-asset-overrides/

## logo.png — en place

Copie du logo Orthanc officiel, pour que la page de connexion et le PACS
présentent la même identité. Sa présence bascule `data-logooverride` à `true`
dans le HTML du portail ; Authelia le sert ensuite à
`/static/media/logo.png`. Aucun réglage à activer.

## favicon.ico — possible, non fait

Même principe. Déposer le fichier suffit.

## locales/ — déconseillé

Techniquement fonctionnel, mais **la surcharge remplace le fichier entier,
elle ne fusionne pas**. Déposer un `portal.json` contenant une seule phrase
fait retomber les 112 autres sur l'anglais. Personnaliser un seul libellé
oblige donc à recopier les 113 chaînes dans ce dépôt, puis à les
resynchroniser à chaque version d'Authelia — toute chaîne ajoutée en amont
resterait masquée par notre copie figée.

Piège supplémentaire : lorsqu'une clé contient un marqueur (`{{authelia}}`),
la traduction doit le conserver. Sinon Authelia refuse de démarrer, en
boucle de redémarrage, avec :

```
translation key '...' has a value which is missing a required placeholder
```

Pour récupérer le fichier complet d'une langue avant de le modifier :

```bash
docker exec orthanc-nginx curl -s http://authelia:9091/locales/fr/portal.json
```

## CSS — non supporté

Authelia n'a pas de mécanisme de feuille de style personnalisée : un
`custom.css` déposé ici est ignoré. La seule voie est l'injection par nginx,
déjà utilisée dans `services/nginx/nginx.ssl.conf` (bloc `location /auth/`)
pour masquer le lien de réinitialisation de mot de passe.

## Thème

Indépendant de ce dossier : option `theme` dans `configuration.yml`
(`auto`, `light`, `dark`, `grey`).
