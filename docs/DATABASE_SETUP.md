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

ORTHANC-AUTHELIA requires a PostgreSQL database (12+, 15 recommended) for storing DICOM index and image data. You have two options:

1. **External Database** (recommended for production) - Connect to an existing PostgreSQL instance
2. **Local Container** (development/testing) - Run PostgreSQL in a Docker container alongside the stack

## Option 1: External PostgreSQL Database

This is the **default configuration** and is recommended for production deployments.

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

For development or testing, you can run PostgreSQL locally in the same stack.

### Step 1: Uncomment PostgreSQL service

In `docker-compose.yml`, uncomment the postgres service:

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

### Step 2: Uncomment the volume

```yaml
volumes:
  postgres_data:
    name: orthanc_postgres_data
```

### Step 3: Update Orthanc service

**Remove the external database network:**
```yaml
orthanc:
  networks:
    - orthanc-network
    # Remove this line: - database
```

**Add postgres dependency:**
```yaml
orthanc:
  depends_on:
    auth-service:
      condition: service_started
    postgres:  # Add this
      condition: service_started
```

**Change POSTGRES_HOST:**
```yaml
orthanc:
  environment:
    - POSTGRES_HOST=postgres  # Changed from "database"
```

### Step 4: Remove external database network

At the bottom of `docker-compose.yml`, remove:
```yaml
database:
  external: true
  name: database
```

### Step 5: Update orthanc.json

In `services/orthanc/config/orthanc.json`:
```json
"PostgreSQL": {
  "Host": "postgres",  # Changed from "database"
  "Username": "orthanc",
  "Password": "change_this_password"
}
```

### Step 6: Start the stack

```bash
docker-compose up -d
```

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
docker-compose logs orthanc | grep -i postgres

# Should see: "Connected to PostgreSQL database"
```

### Check database tables

```bash
# Connect to PostgreSQL
docker exec -it postgres-database psql -U orthanc -d orthanc

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
docker exec -it postgres-database psql -U orthanc -d orthanc -c \
  "SELECT pg_size_pretty(pg_database_size('orthanc'));"
```

## Backup and Restore

### Backup

**Full database backup:**
```bash
docker exec postgres-database pg_dump -U orthanc orthanc > orthanc_backup.sql
```

**Compressed backup:**
```bash
docker exec postgres-database pg_dump -U orthanc orthanc | gzip > orthanc_backup.sql.gz
```

**Automated daily backups:**
```bash
# Add to crontab
0 2 * * * docker exec postgres-database pg_dump -U orthanc orthanc | gzip > /backups/orthanc_$(date +\%Y\%m\%d).sql.gz
```

### Restore

**From SQL file:**
```bash
docker exec -i postgres-database psql -U orthanc orthanc < orthanc_backup.sql
```

**From compressed backup:**
```bash
gunzip -c orthanc_backup.sql.gz | docker exec -i postgres-database psql -U orthanc orthanc
```

### Backup Docker volume (alternative)

```bash
# Stop Orthanc first
docker-compose stop orthanc

# Backup the volume
docker run --rm \
  -v orthanc_postgres_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/postgres_data_backup.tar.gz /data

# Restart Orthanc
docker-compose start orthanc
```

## Performance Tuning

### Monitor database performance

```bash
# Check active connections
docker exec postgres-database psql -U orthanc -d orthanc -c \
  "SELECT count(*) FROM pg_stat_activity;"

# Check slow queries
docker exec postgres-database psql -U orthanc -d orthanc -c \
  "SELECT pid, now() - pg_stat_activity.query_start AS duration, query
   FROM pg_stat_activity
   WHERE state = 'active' AND now() - pg_stat_activity.query_start > interval '5 seconds';"

# Check table sizes
docker exec postgres-database psql -U orthanc -d orthanc -c \
  "SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
   FROM pg_catalog.pg_statio_user_tables
   ORDER BY pg_total_relation_size(relid) DESC;"
```

### Vacuum and analyze

Regular maintenance improves performance:

```bash
# Manual vacuum
docker exec postgres-database psql -U orthanc -d orthanc -c "VACUUM ANALYZE;"

# Enable autovacuum (should be enabled by default)
# Check status:
docker exec postgres-database psql -U orthanc -d orthanc -c "SHOW autovacuum;"
```

### Index optimization

Orthanc creates necessary indexes automatically, but you can verify:

```bash
docker exec postgres-database psql -U orthanc -d orthanc -c \
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
docker exec orthanc-server ping postgres-database -c 3
```

### Authentication failed

- Verify credentials match in `docker-compose.yml` and `orthanc.json`
- Check PostgreSQL logs: `docker logs postgres-database`

### Disk space issues

```bash
# Check database size
docker exec postgres-database psql -U orthanc -d orthanc -c \
  "SELECT pg_size_pretty(pg_database_size('orthanc'));"

# Check available disk space
df -h | grep docker
```

### Performance issues

- Check PostgreSQL logs for slow queries
- Consider increasing shared_buffers
- Enable connection pooling with PgBouncer
- Monitor disk I/O with `iostat`
