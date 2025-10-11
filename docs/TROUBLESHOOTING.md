# Troubleshooting Guide

Common issues and solutions for ORTHANC-AUTHELIA.

## Table of Contents

- [Startup Issues](#startup-issues)
- [Authentication Problems](#authentication-problems)
- [Database Issues](#database-issues)
- [SSL/Certificate Issues](#ssl-certificate-issues)
- [Viewer Problems](#viewer-problems)
- [Network and Connectivity](#network-and-connectivity)
- [Performance Issues](#performance-issues)
- [Viewing Logs](#viewing-logs)
- [Resetting the Stack](#resetting-the-stack)

## Startup Issues

### Services fail to start - "network database not found"

**Symptom**: Error message about missing database network

**Solution**:
```bash
# Create the external database network
docker network create database

# Restart the stack
docker-compose up -d
```

### Port already in use (30080 or 30443)

**Symptom**: Error "bind: address already in use"

**Solution**:
```bash
# Check what's using the port
sudo netstat -tulpn | grep 30080
sudo netstat -tulpn | grep 30443

# Option 1: Stop the conflicting service
sudo systemctl stop <service-name>

# Option 2: Change ports in docker-compose.yml
# Edit the nginx service ports section
```

### Container immediately exits after starting

**Symptom**: Container starts but immediately stops

**Solution**:
```bash
# Check container logs
docker logs <container-name>

# Common causes:
# - Missing required environment variables
# - Invalid configuration file
# - Permission issues on volumes
```

## Authentication Problems

### Cannot login to Authelia - "Invalid credentials"

**Symptom**: Login fails with correct password

**Possible causes and solutions**:

1. **Passwords not hashed correctly**:
```bash
# Regenerate users with proper hashing
./manage-authelia-users.sh

# Restart Authelia
docker-compose restart authelia
```

2. **Wrong user file loaded**:
```bash
# Verify the file exists
ls -la services/authelia/config/users_database.yml

# Check Authelia logs
docker-compose logs authelia | grep -i "user"
```

3. **Session issues**:
```bash
# Clear browser cookies and cache
# Restart Authelia
docker-compose restart authelia redis
```

### Constant re-login required / Session not persisting

**Symptom**: Must login on every page refresh

**Possible causes**:

1. **Domain mismatch**:
```yaml
# In services/authelia/config/configuration.yml
session:
  domain: pacs.yourdomain.com  # Must match your actual domain
```

2. **Cookie issues with IP addresses**:
- If using IP address instead of domain, set `domain: 192.168.1.100` (your IP)
- Browsers may block cookies on localhost

3. **Redis connection issues**:
```bash
# Check Redis is running
docker-compose ps redis

# Test Redis connection
docker exec orthanc-redis redis-cli ping
# Should return: PONG
```

### "Authorization denied" in Orthanc logs

**Symptom**: Orthanc logs show authorization failures

**Solution**:
```bash
# Verify AUTH_USERNAME and AUTH_PASSWORD match in:
# 1. .env file
grep AUTH_ .env

# 2. services/orthanc/config/orthanc.json
grep -A5 '"Authorization"' services/orthanc/config/orthanc.json

# They must be identical
# Restart services after fixing
docker-compose restart orthanc auth-service
```

## Database Issues

### Orthanc fails with "Connection refused" to PostgreSQL

**Symptom**: Orthanc can't connect to database

**Solutions**:

1. **Check PostgreSQL is running**:
```bash
docker ps | grep postgres
```

2. **Verify PostgreSQL is on the database network**:
```bash
docker network inspect database | grep -A 10 postgres
```

3. **Connect PostgreSQL to the network if missing**:
```bash
docker network connect database your-postgres-container
```

4. **Check credentials match**:
```bash
# Compare docker-compose.yml and orthanc.json
grep -A5 POSTGRES docker-compose.yml
grep -A10 PostgreSQL services/orthanc/config/orthanc.json
```

5. **Test database connection manually**:
```bash
docker exec orthanc-server ping postgres-database -c 3
```

### Database connection timeout

**Symptom**: Orthanc startup very slow or times out

**Solutions**:

1. **Check PostgreSQL resource usage**:
```bash
docker stats postgres-database
```

2. **Increase PostgreSQL memory**:
```yaml
# In docker-compose.yml
postgres:
  environment:
    - POSTGRES_SHARED_BUFFERS=256MB
```

3. **Check disk space**:
```bash
df -h
```

## SSL Certificate Issues

### Nginx generates new certificates on every restart

**Symptom**: Certificates regenerated each time

**Solution**:
```bash
# Check volume exists
docker volume ls | grep nginx_ssl

# If missing, it will regenerate
# To keep certificates, don't delete the volume

# Verify certificates persist
docker exec orthanc-nginx ls -la /etc/nginx/ssl/
docker-compose restart nginx
docker exec orthanc-nginx ls -la /etc/nginx/ssl/
# Should show same files with same timestamps
```

### Browser shows "Your connection is not private"

**Symptom**: Certificate warning in browser

**Explanation**: This is **normal** for self-signed certificates.

**Solutions**:

1. **For development/testing**: Click "Advanced" → "Proceed anyway"

2. **For production**: Use proper SSL certificates:
   - See [SSL Setup Guide](SSL_SETUP.md) for custom certificates
   - Use a reverse proxy with Let's Encrypt
   - Use Cloudflare Tunnel

### SSL certificate expired

**Symptom**: Certificate error after 365 days

**Solution**:
```bash
# Regenerate self-signed certificates
docker-compose down
docker volume rm orthanc_nginx_ssl
docker-compose up -d

# Or switch to Let's Encrypt with reverse proxy
```

## Viewer Problems

### OHIF viewer shows "Failed to load study"

**Symptom**: Can't view DICOM images in OHIF

**Solutions**:

1. **Check Orthanc is accessible**:
```bash
docker-compose logs orthanc | grep -i error
```

2. **Verify DICOMweb is enabled**:
```bash
grep -A5 '"DICOMweb"' services/orthanc/config/orthanc.json
```

3. **Check browser console** (F12):
   - Look for CORS errors
   - Check network tab for failed requests
   - Verify authentication cookies are set

4. **Test DICOMweb endpoint**:
```bash
curl -I http://localhost:30080/dicom-web/studies
```

5. **Verify OHIF configuration**:
```bash
cat services/ohif/config/app-config.js | grep -i dicomweb
```

### Stone Web Viewer not loading

**Symptom**: Stone viewer shows blank page or errors

**Solutions**:

1. **Check plugin is enabled**:
```bash
grep -i "StoneWebViewer" services/orthanc/config/orthanc.json
```

2. **Verify Orthanc Explorer 2 is working**:
   - Access `https://your-domain/ui/`
   - Check for integration buttons

3. **Check browser console for JavaScript errors**

## Network and Connectivity

### Can't access application from other machines

**Symptom**: Works on localhost but not from network

**Solutions**:

1. **Check firewall**:
```bash
# Allow ports 30080 and 30443
sudo ufw allow 30080/tcp
sudo ufw allow 30443/tcp
```

2. **Verify Docker is binding to all interfaces**:
```bash
sudo netstat -tulpn | grep 30080
# Should show 0.0.0.0:30080, not 127.0.0.1:30080
```

3. **Check DOMAIN in .env**:
```bash
# Should be accessible hostname/IP, not "localhost"
grep DOMAIN .env
```

### Services can't communicate with each other

**Symptom**: Inter-container communication fails

**Solutions**:

1. **Check all services are on same network**:
```bash
docker network inspect orthanc-network
```

2. **Test connectivity between containers**:
```bash
docker exec orthanc-server ping redis -c 3
docker exec orthanc-server ping auth-service -c 3
```

3. **Check container names are correct**:
```bash
docker-compose ps
```

## Performance Issues

### Slow DICOM uploads

**Symptom**: Uploading studies takes very long

**Solutions**:

1. **Check disk I/O**:
```bash
iostat -x 5
```

2. **Monitor PostgreSQL performance**:
```bash
docker exec postgres-database psql -U orthanc -d orthanc -c \
  "SELECT * FROM pg_stat_activity WHERE state = 'active';"
```

3. **Increase PostgreSQL resources**:
```yaml
postgres:
  deploy:
    resources:
      limits:
        cpus: '2'
        memory: 2G
```

### High memory usage

**Symptom**: Containers using excessive RAM

**Solutions**:

1. **Check resource usage**:
```bash
docker stats
```

2. **Limit container memory** in docker-compose.yml:
```yaml
services:
  orthanc:
    deploy:
      resources:
        limits:
          memory: 1G
```

3. **Tune Orthanc cache settings** in orthanc.json:
```json
"MaximumStorageSize": 10000,
"MaximumPatientCount": 1000
```

## Viewing Logs

### All services
```bash
docker-compose logs -f
```

### Specific service
```bash
docker-compose logs -f orthanc
docker-compose logs -f authelia
docker-compose logs -f nginx
docker-compose logs -f auth-service
```

### Last N lines
```bash
docker-compose logs --tail=100 orthanc
```

### Search logs
```bash
docker-compose logs orthanc | grep -i error
docker-compose logs authelia | grep -i "failed"
```

### Live follow with filter
```bash
docker-compose logs -f orthanc | grep -i "authorization"
```

## Resetting the Stack

### Soft reset (keep data)
```bash
# Restart all services
docker-compose restart

# Or recreate containers (keeps volumes)
docker-compose down
docker-compose up -d
```

### Hard reset (delete everything)

**WARNING**: This deletes ALL data including:
- SSL certificates
- User accounts
- Authelia database
- (But NOT your DICOM data in PostgreSQL)

```bash
# Stop and remove containers
docker-compose down

# Remove volumes
docker volume rm orthanc_nginx_ssl

# Remove Authelia data
rm services/authelia/config/users_database.yml
rm services/authelia/config/db.sqlite3

# Recreate configuration
./manage-authelia-users.sh

# Start fresh
docker-compose up -d
```

### Complete reset including DICOM data

**EXTREME WARNING**: This deletes EVERYTHING including all DICOM studies!

```bash
# Stop everything
docker-compose down

# Remove all volumes
docker volume rm orthanc_nginx_ssl
docker volume rm orthanc_postgres_data  # If using local PostgreSQL

# Reset configuration
rm services/authelia/config/users_database.yml
rm services/authelia/config/db.sqlite3

# Recreate
./manage-authelia-users.sh
docker-compose up -d
```

## Getting Help

If none of these solutions work:

1. **Collect information**:
```bash
# System info
docker version
docker-compose version
uname -a

# Container status
docker-compose ps

# Recent logs
docker-compose logs --tail=200 > logs.txt
```

2. **Check existing issues**: Search GitHub issues for similar problems

3. **Create a new issue** with:
   - Description of the problem
   - Steps to reproduce
   - System information
   - Relevant log excerpts (remove sensitive data)
   - Configuration files (remove passwords/secrets)
