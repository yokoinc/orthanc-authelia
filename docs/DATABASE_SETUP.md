# Database Setup Guide

Complete guide for PostgreSQL database setup with ORTHANC-AUTHELIA.

## Table of Contents

- [Overview](#overview)
- [Option 1: External PostgreSQL Database](#option-1-external-postgresql-database)
- [Option 2: Local PostgreSQL Container](#option-2-local-postgresql-container)
- [Database Configuration](#database-configuration)
- [Verification](#verification)
- [Backup and Restore](#backup-and-restore)
- [Performance Tuning](#performance-tuning)

## Overview

ORTHANC-AUTHELIA requires a PostgreSQL database (12+, 16 shipped) for storing DICOM index and image data. You have two options:

1. **Local Container** — the shipped default. `docker-compose.yml.example` starts a `postgres:16-alpine` container alongside the stack; nothing to prepare before the first `docker compose up`.
2. **External Database** — connect to an existing PostgreSQL instance instead, via a `docker-compose.override.yml`. Useful when a database is already administered and backed up elsewhere.

> Both options are supported. The choice is about where the database is administered, not about production versus development: the shipped container is a regular PostgreSQL, and an external instance carries no special guarantee of its own.

## Option 1: External PostgreSQL Database

This is **not** the default: the stack ships its own PostgreSQL container. Follow this section only to point Orthanc at an instance you already run — see the `docker-compose.override.yml` in the README's *Advanced* section, which disables the bundled container.

### Requirements

- PostgreSQL 12+ running (PostgreSQL 15 recommended)
- Network connectivity between Orthanc and PostgreSQL
- Database and user credentials

### Step 1: Create the database network

If not already created:
```bash
docker network create database
```

### Step 2: Connect your PostgreSQL container to the network

**If using an existing PostgreSQL container:**
```bash
docker network connect database your-postgres-container
```

**If creating a new PostgreSQL container:**
```bash
docker run -d \
  --name postgres-database \
  --network database \
  -e POSTGRES_DB=orthanc \
  -e POSTGRES_USER=orthanc \
  -e POSTGRES_PASSWORD=your-secure-password \
  -v postgres-data:/var/lib/postgresql/data \
  postgres:15-alpine
```

### Step 3: Update credentials

**In `docker-compose.yml`:**
```yaml
orthanc:
  environment:
    - POSTGRES_HOST=postgres-database  # Your PostgreSQL container/hostname
    - POSTGRES_USER=orthanc
    - POSTGRES_PASSWORD=your-secure-password
```

**In `services/orthanc/config/orthanc.json`:**
```json
"PostgreSQL": {
  "Host": "postgres-database",
  "Username": "orthanc",
  "Password": "your-secure-password",
  "Database": "orthanc"
}
```

### Step 4: Verify network connectivity

```bash
# Check the network exists
docker network ls | grep database

# Check what containers are connected
docker network inspect database
```

### Database Structure

- Orthanc automatically creates all required tables on first start
- No manual schema setup needed
- PostgreSQL used for both DICOM index and storage
- Optimized for medical imaging workloads

## Option 2: Local PostgreSQL Container

**This is what the stack ships**, and nothing has to be done to enable it: `bootstrap.sh` generates a `docker-compose.yml` whose postgres service is already active, with credentials drawn from `.env`.

The section below documents the service for reference — to read what it declares, or to adjust it. It is not a set of steps to follow on a fresh install.

### The shipped PostgreSQL service

```yaml
postgres:
  image: postgres:16-alpine
  container_name: orthanc-postgres
  restart: unless-stopped
  environment:
    - POSTGRES_DB=orthanc
    - POSTGRES_USER=orthanc
    - POSTGRES_PASSWORD=change_this_password
  volumes:
    - postgres_data:/var/lib/postgresql/data
  networks:
    - orthanc-network
```

### Le volume associé

```yaml
volumes:
  postgres_data:
    name: orthanc_postgres_data
```

Les données vivent dans un volume Docker nommé, pas dans le dépôt : `docker compose down` les conserve, `docker compose down -v` les détruit. C'est cette seconde commande que la procédure de remise à zéro du README emploie — elle efface les examens.

### Ce que voit Orthanc

Orthanc joint le conteneur par son nom de service sur le réseau interne, `POSTGRES_HOST=postgres`, avec les identifiants du `.env` que `bootstrap.sh` a générés. Aucun port n'est publié sur l'hôte : la base n'est accessible que depuis la stack.

### Passer à une instance externe

Rien à modifier dans le `docker-compose.yml` généré. Un `docker-compose.override.yml` désactive le conteneur fourni et redirige Orthanc, sans toucher au fichier principal — la recette complète est dans la section *Advanced : DB PostgreSQL externe* du README. L'option 1 ci-dessus décrit alors la préparation à faire côté base existante.

## Database Configuration

### Recommended PostgreSQL Settings

For optimal performance with medical imaging data, consider these PostgreSQL settings:

```bash
# In postgresql.conf or via environment variables
shared_buffers = 256MB
effective_cache_size = 1GB
maintenance_work_mem = 128MB
checkpoint_completion_target = 0.9
wal_buffers = 16MB
default_statistics_target = 100
random_page_cost = 1.1
effective_io_concurrency = 200
work_mem = 4MB
min_wal_size = 1GB
max_wal_size = 4GB
```

### Connection Pooling

For high-traffic deployments, consider using PgBouncer:

```yaml
pgbouncer:
  image: pgbouncer/pgbouncer:latest
  environment:
    - DATABASES_HOST=postgres
    - DATABASES_PORT=5432
    - DATABASES_USER=orthanc
    - DATABASES_PASSWORD=your-password
    - DATABASES_DBNAME=orthanc
    - PGBOUNCER_POOL_MODE=transaction
    - PGBOUNCER_MAX_CLIENT_CONN=100
    - PGBOUNCER_DEFAULT_POOL_SIZE=20
  networks:
    - orthanc-network
```

Then point Orthanc to pgbouncer instead of postgres directly.

## Verification

### Check database connection

```bash
# View Orthanc logs
docker compose logs orthanc | grep -i postgres

# Should see: "Connected to PostgreSQL database"
```

### Check database tables

```bash
# Connect to PostgreSQL
docker exec -it orthanc-postgres psql -U orthanc -d orthanc

# List tables
\dt

# Should see tables like:
# - Resources
# - DicomIdentifiers
# - MainDicomTags
# - Changes
# - ExportedResources
```

### Check database size

```bash
docker exec -it orthanc-postgres psql -U orthanc -d orthanc -c \
  "SELECT pg_size_pretty(pg_database_size('orthanc'));"
```

## Backup and Restore

### Backup

**Full database backup:**
```bash
docker exec orthanc-postgres pg_dump -U orthanc orthanc > orthanc_backup.sql
```

**Compressed backup:**
```bash
docker exec orthanc-postgres pg_dump -U orthanc orthanc | gzip > orthanc_backup.sql.gz
```

**Automated daily backups:**
```bash
# Add to crontab
0 2 * * * docker exec orthanc-postgres pg_dump -U orthanc orthanc | gzip > /backups/orthanc_$(date +\%Y\%m\%d).sql.gz
```

### Restore

**From SQL file:**
```bash
docker exec -i orthanc-postgres psql -U orthanc orthanc < orthanc_backup.sql
```

**From compressed backup:**
```bash
gunzip -c orthanc_backup.sql.gz | docker exec -i orthanc-postgres psql -U orthanc orthanc
```

### Backup Docker volume (alternative)

```bash
# Stop Orthanc first
docker compose stop orthanc

# Backup the volume
docker run --rm \
  -v orthanc_postgres_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/postgres_data_backup.tar.gz /data

# Restart Orthanc
docker compose start orthanc
```

## Performance Tuning

### Monitor database performance

```bash
# Check active connections
docker exec orthanc-postgres psql -U orthanc -d orthanc -c \
  "SELECT count(*) FROM pg_stat_activity;"

# Check slow queries
docker exec orthanc-postgres psql -U orthanc -d orthanc -c \
  "SELECT pid, now() - pg_stat_activity.query_start AS duration, query
   FROM pg_stat_activity
   WHERE state = 'active' AND now() - pg_stat_activity.query_start > interval '5 seconds';"

# Check table sizes
docker exec orthanc-postgres psql -U orthanc -d orthanc -c \
  "SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
   FROM pg_catalog.pg_statio_user_tables
   ORDER BY pg_total_relation_size(relid) DESC;"
```

### Vacuum and analyze

Regular maintenance improves performance:

```bash
# Manual vacuum
docker exec orthanc-postgres psql -U orthanc -d orthanc -c "VACUUM ANALYZE;"

# Enable autovacuum (should be enabled by default)
# Check status:
docker exec orthanc-postgres psql -U orthanc -d orthanc -c "SHOW autovacuum;"
```

### Index optimization

Orthanc creates necessary indexes automatically, but you can verify:

```bash
docker exec orthanc-postgres psql -U orthanc -d orthanc -c \
  "SELECT tablename, indexname FROM pg_indexes WHERE schemaname = 'public';"
```

## Troubleshooting

### Connection refused

```bash
# Check PostgreSQL is running
docker ps | grep postgres

# Check network connectivity
docker network inspect database

# Test connection from orthanc container
docker exec orthanc-server ping orthanc-postgres -c 3
```

### Authentication failed

- Verify credentials match in `docker-compose.yml` and `orthanc.json`
- Check PostgreSQL logs: `docker logs orthanc-postgres`

### Disk space issues

```bash
# Check database size
docker exec orthanc-postgres psql -U orthanc -d orthanc -c \
  "SELECT pg_size_pretty(pg_database_size('orthanc'));"

# Check available disk space
df -h | grep docker
```

### Performance issues

- Check PostgreSQL logs for slow queries
- Consider increasing shared_buffers
- Enable connection pooling with PgBouncer
- Monitor disk I/O with `iostat`
