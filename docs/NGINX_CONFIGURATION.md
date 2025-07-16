# Nginx Configuration Guide

## Current Implementation Status

The Nginx reverse proxy is **fully functional** and serves as the central routing component of ORTHANC-AUTHELIA. It handles all incoming requests and routes them to the appropriate backend services.

## What Nginx Does

### 1. Request Routing
- **Authentication routes** (`/auth/*`, `/api/*`, `/static/*`) → Authelia
- **Token sharing routes** (`/share/*`) → Auth-Service (no authentication required)
- **Protected routes** (`/ohif/*`, `/ui/*`, `/wado*`, `/dicom-web*`, etc.) → Orthanc/OHIF (with auth_request)

### 2. Authentication Integration
- **auth_request module**: Validates user sessions with Authelia before allowing access to protected routes
- **Header forwarding**: Passes user and group information from Authelia to backend services
- **Token extraction**: Extracts tokens from URL parameters and forwards them as headers

### 3. Static Asset Optimization
- **Caching**: Implements appropriate cache headers for static assets
- **Compression**: Gzip compression for text-based content
- **Performance**: Optimized buffer sizes for medical imaging workloads

### 4. Security Headers
- **CSRF protection**: Implements security headers
- **Frame options**: Prevents clickjacking
- **Content security**: Basic CSP implementation

## Current SSL/TLS Strategy

### Production Setup (Cloudflare Tunnel)
Currently, the system is designed to work behind **Cloudflare Tunnel** in production:

```
Internet → Cloudflare (HTTPS) → Tunnel → Nginx (HTTP:80) → Backend Services
```

**Advantages**:
- Cloudflare handles SSL termination
- Automatic certificate management
- DDoS protection and CDN benefits
- Zero-trust network access

**Configuration**:
- Nginx listens on port 80 (HTTP only)
- All traffic is local between containers
- Cloudflare handles HTTPS conversion
- No certificate management needed

### Self-Hosted SSL Support (In Development)
We have **prepared** the infrastructure for self-hosted SSL certificates:

**Available Configuration**:
- `nginx.ssl.conf` - SSL-enabled configuration
- Certificate volume mounts in docker-compose
- SSL environment variables

**Current Status**: 
- SSL configuration exists but is **not yet fully tested**
- Certificate management workflow **needs refinement**
- Let's Encrypt integration **under consideration**

## Configuration Files

### Main Configuration
- `services/reverse-proxy/nginx.conf` - Main HTTP configuration (current)
- `services/reverse-proxy/nginx.ssl.conf` - SSL configuration (prepared)

### Include Files
- `conf.d/auth_request.conf` - Authelia authentication
- `conf.d/auth_headers.conf` - User header forwarding
- `conf.d/proxy_headers.conf` - Standard proxy headers
- `conf.d/security_headers.conf` - Security headers
- `conf.d/cors_headers.conf` - CORS configuration
- `conf.d/extract_token.conf` - Token extraction logic

### Static Content
- `html/errors/` - Custom error pages
- `html/403.html`, `html/404.html`, `html/502.html` - Error templates

## Key Nginx Features

### Route Protection Logic
```nginx
# Protected routes require authentication
location /ohif/ {
    include /etc/nginx/conf.d/auth_request.conf;  # Validate with Authelia
    proxy_pass http://ohif:8080/;
}

# Token routes bypass authentication
location /share/ {
    proxy_pass http://auth_service/share/;  # Direct to Auth-Service
}

# Static assets are public
location ~* \.(js|css|png|jpg|gif|svg|ico)$ {
    proxy_pass http://orthanc;  # No authentication needed
}
```

### Authentication Integration
```nginx
# Internal authentication endpoint
location /authelia/ {
    internal;  # Only accessible internally
    proxy_pass http://authelia/api/verify;
    # Forward original request info
    proxy_set_header X-Original-URL https://$host$request_uri;
}
```

### Performance Optimization
```nginx
# Optimized for medical imaging
client_max_body_size 2g;  # Large DICOM uploads
large_client_header_buffers 8 16k;  # OHIF metadata
gzip_comp_level 6;  # Balanced compression
keepalive_timeout 65;  # Connection reuse
```

## Current Limitations

### SSL Implementation
- **Manual certificate management**: No automation for certificate renewal
- **Let's Encrypt integration**: Not yet implemented
- **Certificate validation**: SSL configuration needs thorough testing

### Performance
- **Image caching**: No specialized DICOM image caching
- **Load balancing**: Single instance only
- **Rate limiting**: Not implemented

### Security
- **Advanced CSP**: Basic implementation only
- **HSTS**: Not configured for self-hosted SSL
- **Security scanning**: No automated security checks

## What Remains To Do

### High Priority

1. **Complete SSL Implementation**
   - Test and validate `nginx.ssl.conf`
   - Implement Let's Encrypt automation
   - Add certificate renewal scripts
   - Document SSL setup procedure

2. **Certificate Management**
   - Create certificate generation scripts
   - Implement renewal automation
   - Add certificate validation checks
   - Document certificate replacement procedure

### Medium Priority

3. **Performance Improvements**
   - Implement DICOM image caching
   - Add rate limiting configuration
   - Optimize buffer sizes for large studies
   - Implement connection pooling

4. **Security Enhancements**
   - Enhanced Content Security Policy
   - HSTS implementation for SSL
   - Security header optimization
   - Automated security scanning

### Low Priority

5. **Monitoring and Logging**
   - Structured logging format
   - Performance metrics collection
   - Error tracking improvements
   - Health check endpoints

6. **Advanced Features**
   - Multi-instance load balancing
   - Failover configuration
   - Geographic load distribution
   - Advanced caching strategies

## Testing SSL Configuration

When ready to test SSL implementation:

```bash
# Switch to SSL configuration
export NGINX_CONFIG=nginx.ssl.conf
export SSL_ENABLED=true
export SSL_CERT_PATH=/path/to/cert.pem
export SSL_KEY_PATH=/path/to/key.pem

# Restart with SSL
docker-compose restart nginx
```

## Summary

The Nginx configuration is **production-ready** for Cloudflare Tunnel deployment and provides all essential routing, authentication, and security features. The SSL implementation is **prepared** but requires additional development and testing for self-hosted scenarios.