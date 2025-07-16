# Configuration du Tunnel Cloudflare pour ORTHANC-AUTHELIA

Ce guide explique comment configurer un tunnel Cloudflare pour exposer votre instance ORTHANC-AUTHELIA sur Internet de manière sécurisée.

## Table des matières

1. [Prérequis](#prérequis)
2. [Installation de cloudflared](#installation-de-cloudflared)
3. [Authentification Cloudflare](#authentification-cloudflare)
4. [Configuration du tunnel](#configuration-du-tunnel)
5. [Configuration DNS](#configuration-dns)
6. [Configuration du backend HTTPS](#configuration-du-backend-https)
7. [Démarrage du tunnel](#démarrage-du-tunnel)
8. [Automatisation avec systemd](#automatisation-avec-systemd)
9. [Surveillance et logs](#surveillance-et-logs)
10. [Dépannage](#dépannage)

## Prérequis

- Un compte Cloudflare avec un domaine configuré
- Docker et docker-compose installés
- ORTHANC-AUTHELIA configuré avec HTTPS (port 30443)
- Certificats SSL générés (self-signed ou Let's Encrypt)

## Installation de cloudflared

### Sur Ubuntu/Debian

```bash
# Télécharger et installer cloudflared
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared
sudo mv cloudflared /usr/local/bin/
sudo chmod +x /usr/local/bin/cloudflared
```

### Vérification de l'installation

```bash
cloudflared --version
```

## Authentification Cloudflare

### 1. Connexion à votre compte Cloudflare

```bash
cloudflared tunnel login
```

Cette commande ouvrira votre navigateur pour l'authentification Cloudflare.

### 2. Sélection du domaine

Sélectionnez le domaine que vous souhaitez utiliser (ex: `yokoinc.ovh`).

## Configuration du tunnel

### 1. Créer un nouveau tunnel

```bash
cloudflared tunnel create orthanc-pacs
```

Cette commande créera un tunnel nommé `orthanc-pacs` et générera un UUID unique.

### 2. Créer le fichier de configuration

Créez le fichier `/etc/cloudflared/config.yml` :

```yaml
# Configuration du tunnel Cloudflare pour ORTHANC-AUTHELIA
tunnel: orthanc-pacs
credentials-file: /etc/cloudflared/orthanc-pacs.json

# Configuration des logs
log-level: info
log-file: /var/log/cloudflared.log

# Configuration du backend HTTPS
ingress:
  # Règle principale pour le domaine PACS
  - hostname: pacs.yokoinc.ovh
    service: https://localhost:30443
    # Configuration TLS pour le backend
    originRequest:
      # Désactiver la vérification TLS pour les certificats self-signed
      noTLSVerify: true
      # Forcer HTTP/2 pour de meilleures performances
      http2Origin: true
      # Définir les headers pour le proxy
      proxyHeaders:
        Host: pacs.yokoinc.ovh
      # Timeout de connexion
      connectTimeout: 30s
      # Timeout de lecture
      tlsTimeout: 10s
  
  # Règle par défaut (obligatoire)
  - service: http_status:404
```

### 3. Créer les répertoires nécessaires

```bash
sudo mkdir -p /etc/cloudflared
sudo mkdir -p /var/log
```

### 4. Copier le fichier de credentials

```bash
sudo cp ~/.cloudflared/orthanc-pacs.json /etc/cloudflared/
```

## Configuration DNS

### 1. Ajouter l'enregistrement DNS

```bash
cloudflared tunnel route dns orthanc-pacs pacs.yokoinc.ovh
```

### 2. Vérifier la configuration DNS

Vérifiez dans votre dashboard Cloudflare que l'enregistrement CNAME a été créé :
- **Type** : CNAME
- **Nom** : pacs
- **Cible** : `orthanc-pacs.cfargotunnel.com`
- **Proxy** : ✅ Activé (nuage orange)

## Configuration du backend HTTPS

### 1. Paramètres SSL/TLS dans Cloudflare

Dans votre dashboard Cloudflare, allez dans **SSL/TLS** > **Aperçu** :

- **Mode de chiffrement** : Full (strict) ou Full
- **Certificat de périphérie** : Automatique
- **Certificat d'origine** : Activé

### 2. Paramètres supplémentaires

Dans **SSL/TLS** > **Paramètres de périphérie** :

- **Version TLS minimale** : 1.2
- **Vérification TLS automatique** : Activée
- **Certificat d'origine** : Configuré

### 3. Configuration des règles de page (optionnel)

Créez une règle de page pour `pacs.yokoinc.ovh/*` :

- **Niveau de sécurité** : Moyen
- **Mode cache** : Standard
- **Réécriture HTTPS** : Activée

## Démarrage du tunnel

### 1. Test de la configuration

```bash
cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
```

### 2. Test de connectivité

```bash
cloudflared tunnel --config /etc/cloudflared/config.yml ingress rule https://pacs.yokoinc.ovh/
```

### 3. Démarrage manuel (pour test)

```bash
sudo cloudflared tunnel --config /etc/cloudflared/config.yml run
```

### 4. Vérification

Ouvrez votre navigateur et allez sur `https://pacs.yokoinc.ovh`. Vous devriez voir la page de connexion Authelia.

## Automatisation avec systemd

### 1. Créer le service systemd

Créez le fichier `/etc/systemd/system/cloudflared.service` :

```ini
[Unit]
Description=Cloudflare Tunnel
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/cloudflared tunnel --config /etc/cloudflared/config.yml run
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 2. Activer et démarrer le service

```bash
sudo systemctl daemon-reload
sudo systemctl enable cloudflared.service
sudo systemctl start cloudflared.service
```

### 3. Vérifier le statut

```bash
sudo systemctl status cloudflared.service
```

## Surveillance et logs

### 1. Logs en temps réel

```bash
sudo journalctl -u cloudflared.service -f
```

### 2. Logs Cloudflared

```bash
sudo tail -f /var/log/cloudflared.log
```

### 3. Métriques Cloudflare

Dans votre dashboard Cloudflare, consultez :
- **Analytics** > **Trafic** : Statistiques de trafic
- **Analytics** > **Sécurité** : Événements de sécurité
- **Analytics** > **Performance** : Métriques de performance

## Dépannage

### Erreur de connexion SSL

```bash
# Vérifier les certificats SSL
openssl s_client -connect localhost:30443 -servername pacs.yokoinc.ovh

# Tester avec curl
curl -k -H "Host: pacs.yokoinc.ovh" https://localhost:30443/auth/
```

### Erreur 502 Bad Gateway

1. Vérifiez que ORTHANC-AUTHELIA fonctionne :
   ```bash
   docker compose ps
   curl -k https://localhost:30443/auth/
   ```

2. Vérifiez la configuration cloudflared :
   ```bash
   cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
   ```

### Problèmes de DNS

```bash
# Vérifier la résolution DNS
nslookup pacs.yokoinc.ovh
dig pacs.yokoinc.ovh

# Vérifier les enregistrements Cloudflare
cloudflared tunnel route dns orthanc-pacs pacs.yokoinc.ovh
```

### Erreur d'authentification

```bash
# Réauthentifier
cloudflared tunnel login

# Lister les tunnels
cloudflared tunnel list
```

## Configuration avancée

### 1. Redirection automatique HTTPS

Dans votre configuration nginx, ajoutez :

```nginx
server {
    listen 80;
    server_name pacs.yokoinc.ovh;
    return 301 https://$server_name$request_uri;
}
```

### 2. Headers de sécurité

Ajoutez dans votre configuration nginx :

```nginx
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Frame-Options DENY always;
add_header X-Content-Type-Options nosniff always;
```

### 3. Limitation du taux de requêtes

Dans Cloudflare, configurez des règles de limitation :
- **Règle** : `pacs.yokoinc.ovh/auth/*`
- **Limite** : 10 requêtes par minute par IP
- **Action** : Bloquer temporairement

## Sécurité

### 1. Firewall

Configurez votre firewall pour bloquer l'accès direct au port 30443 :

```bash
sudo ufw deny 30443
sudo ufw allow from 127.0.0.1 to any port 30443
```

### 2. Authentification à deux facteurs

Assurez-vous que l'authentification à deux facteurs est activée dans Authelia :

```yaml
totp:
  issuer: ORTHANC-AUTHELIA
  period: 30
  skew: 1
```

### 3. Monitoring des accès

Surveillez les logs d'accès :

```bash
docker compose logs nginx | grep "GET /auth/"
docker compose logs authelia | grep "Access to"
```

## Maintenance

### 1. Mise à jour de cloudflared

```bash
# Télécharger la dernière version
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /tmp/cloudflared

# Remplacer la version actuelle
sudo systemctl stop cloudflared
sudo mv /tmp/cloudflared /usr/local/bin/
sudo chmod +x /usr/local/bin/cloudflared
sudo systemctl start cloudflared
```

### 2. Renouvellement des certificats

Si vous utilisez des certificats auto-signés, pensez à les renouveler régulièrement :

```bash
# Regénérer les certificats
cd /volume2/docker/pacs-orthanc-authelia
./scripts/generate_ssl_cert.sh

# Redémarrer nginx
docker compose restart nginx
```

## Support

- **Documentation Cloudflare** : https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/
- **GitHub cloudflared** : https://github.com/cloudflare/cloudflared
- **Logs du système** : `/var/log/cloudflared.log`
- **Status des services** : `systemctl status cloudflared`

---

*Ce guide a été créé pour ORTHANC-AUTHELIA v1.0. Pour les dernières mises à jour, consultez la documentation officielle.*