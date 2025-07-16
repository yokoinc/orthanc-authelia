# Token Sharing Guide

This guide explains how to use the token-based sharing system for external access to medical studies.

## Overview

The token sharing system allows authenticated users (admin/doctor) to create secure, limited-access links for external users to view specific medical studies without requiring full system access.

## How Token Sharing Works

### 1. Token Creation
- Authenticated user selects study/series to share
- System generates unique token with specific permissions
- Token is associated with specific DICOM resources
- URL is generated for external access

### 2. External Access
- External user receives sharing URL
- URL contains token parameter: `http://domain/ohif/?token=abc123`
- System validates token and grants temporary access
- User can view studies without authentication

### 3. Security Controls
- Time-limited access (configurable expiration)
- Usage limits (maximum number of views)
- Resource restrictions (only specified studies accessible)
- Audit logging (all access tracked)

## Token Management Interface

### Accessing the Interface
1. Login as **admin user** (doctor users cannot access this interface)
2. Navigate to: `http://your-domain/auth/tokens/manage`
3. Use the web interface to manage tokens

**Important**: This interface is **restricted to admin users only** and requires Authelia authentication. Doctor and external users cannot access token management features.

### Interface Features
- **Active Tokens**: View all currently valid tokens with full details
- **Expired Tokens**: Review previously expired tokens and their usage history
- **Token Statistics**: Real-time usage analytics and system metrics
- **Revoke Tokens**: Manually disable tokens before their natural expiration
- **Interactive Dashboard**: Modern web interface with live updates
- **Detailed Token Information**: 
  - Token ID and type
  - Creation and expiration dates
  - Current usage count vs. maximum allowed
  - Associated DICOM resources (studies/series)
  - Access patterns and timing
- **Security Monitoring**: Identify suspicious or unusual token usage
- **Bulk Operations**: Manage multiple tokens simultaneously
- **Audit Trail**: Complete history of all token operations

## Creating Sharing Tokens

### Method 1: Via Orthanc Explorer 2
1. Login to main PACS interface
2. Navigate to study/series to share
3. Click "Share" button
4. Configure sharing parameters:
   - Duration (7, 15, 30, or 90 days)
   - Access level (view-only)
   - Usage limits
5. Generate token and copy sharing URL

### Method 2: Via OHIF Viewer
1. Open study in OHIF viewer
2. Use sharing controls in viewer interface
3. Generate token with study context
4. Share generated URL

### Method 3: Via API (for integration)
```bash
curl -X POST "http://your-domain/auth/tokens/ohif-viewer-publication" \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic $(echo -n 'username:password' | base64)" \
  -d '{
    "Resources": [
      {
        "Level": "study",
        "DicomUid": "1.2.3.4.5",
        "OrthancId": "study-id"
      }
    ],
    "ValidityDuration": 604800
  }'
```

## Token Configuration

### Expiration Settings
- **Default**: 7 days
- **Options**: 1, 7, 15, 30, 90 days
- **Custom**: API allows custom durations
- **Unlimited**: Available for admin users (1 year maximum)

### Usage Limits
- **Default**: 50 uses per token
- **Configurable**: Can be adjusted per token
- **Tracking**: Each access increments counter
- **Auto-revoke**: Token disabled when limit reached

### Resource Restrictions
- **Study Level**: Access to entire study
- **Series Level**: Access to specific series only
- **Instance Level**: Access to individual DICOM instances
- **Hierarchical**: Study tokens include all series/instances

## External User Experience

### Accessing Shared Studies
1. Receive sharing URL via email/message
2. Click URL to open OHIF viewer
3. Study loads automatically with token authentication
4. Full OHIF viewing capabilities available
5. No additional login required

### Medical Viewer Features (Token Access)

#### OHIF Viewer v3.10.2 (Primary)
- **Multi-planar Reconstruction (MPR)**
- **Measurement tools**
- **Window/Level adjustments**
- **Zoom and pan**
- **Series navigation**
- **Study information display**

#### Stone Web Viewer (Secondary)
- **High-performance rendering**
- **Advanced visualization**
- **Optimized for large datasets**
- **Cross-sectional views**
- **Specialized medical algorithms**

#### VolView (Volumetric)
- **3D volume rendering**
- **Interactive 3D manipulation**
- **Volumetric analysis tools**
- **CT/MRI optimization**
- **Advanced visualization techniques**

All viewers support token-based access with the same security controls and limitations.

### Limitations for Token Users
- **Read-only access**: Cannot modify or upload
- **No system access**: Cannot browse other studies
- **Time-limited**: Access expires automatically
- **Usage-limited**: Token may be exhausted
- **Audit-logged**: All activity recorded

## Security Features

### Token Generation
- **Cryptographically secure**: UUID4 random generation
- **Unpredictable**: Cannot be guessed or enumerated
- **Single-use URLs**: Each token unique per sharing instance
- **No personal data**: Tokens contain no identifiable information

### Access Control
- **Resource isolation**: Only authorized resources accessible
- **Session isolation**: No cross-contamination with other users
- **Network restrictions**: Can be limited to specific IP ranges (optional)
- **Browser security**: Tokens work only in designated viewer contexts

### Audit and Monitoring
- **Access logging**: Every token use recorded
- **User tracking**: IP addresses and timestamps logged
- **Usage analytics**: Token effectiveness metrics
- **Security alerts**: Suspicious activity detection

## Administrative Features

### Token Analytics
- **Usage statistics**: Views, duration, resources accessed
- **Popular studies**: Most frequently shared content
- **User behavior**: Access patterns and timing
- **Performance metrics**: System load from token usage

### Batch Operations
- **Bulk revocation**: Disable multiple tokens
- **Expiration management**: Extend or shorten token lifetimes
- **Resource updates**: Modify token permissions
- **User notifications**: Alert token creators of status changes

## Integration Examples

### Email Integration
```html
<!DOCTYPE html>
<html>
<head>
    <title>Medical Study Sharing</title>
</head>
<body>
    <h2>Medical Study Shared</h2>
    <p>A medical study has been shared with you.</p>
    <p><a href="http://your-domain/share/?token=TOKEN_HERE">View Study</a></p>
    <p>This link expires in 7 days and is limited to 50 uses.</p>
</body>
</html>
```

### QR Code Generation
```python
import qrcode

def generate_sharing_qr(token_url):
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(token_url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")
```

## Best Practices

### Token Creation
- **Minimum duration**: Use shortest acceptable expiration time
- **Specific resources**: Share only necessary studies/series
- **Usage limits**: Set appropriate view limits
- **Descriptive names**: Use clear token descriptions for tracking

### Distribution
- **Secure channels**: Share URLs via encrypted communication
- **Need-to-know**: Only share with intended recipients
- **Time-sensitive**: Send URLs close to when they'll be used
- **Clear instructions**: Provide usage guidance to recipients

### Monitoring
- **Regular review**: Check active tokens periodically
- **Usage tracking**: Monitor access patterns
- **Expiration management**: Clean up expired tokens
- **Security alerts**: Watch for suspicious activity

## Troubleshooting

### Common Issues

#### Token Not Working
1. Check token expiration status
2. Verify usage limits not exceeded
3. Ensure correct URL format
4. Check for typing errors in token

#### Viewer Not Loading
1. Verify token is valid and active
2. Check browser compatibility
3. Ensure network connectivity
4. Review OHIF viewer logs

#### Permission Denied
1. Confirm token has access to requested resource
2. Check resource permissions
3. Verify token not revoked
4. Review audit logs for details

### Error Messages
- **"Invalid token"**: Token not found or malformed
- **"Token expired"**: Token past expiration date
- **"Usage limit exceeded"**: Token used too many times
- **"Resource not found"**: Study/series not available
- **"Access denied"**: Insufficient permissions

## Configuration

### Environment Variables
```bash
# Token settings
DEFAULT_TOKEN_MAX_USES=50
DEFAULT_TOKEN_VALIDITY_SECONDS=604800  # 7 days
UNLIMITED_TOKEN_DURATION=31536000      # 1 year

# Cache settings
CACHE_VALIDITY_SHARE_TOKEN=60          # 1 minute
AUDIT_RETENTION_DAYS=90                # 3 months
```

### Database Storage
Tokens are stored in Redis with automatic expiration:
- **Key format**: `token:TOKEN_UUID`
- **Data**: JSON with permissions and metadata
- **TTL**: Automatic cleanup on expiration
- **Persistence**: Optional Redis persistence for audit

## API Reference

### Token Validation Endpoint
```bash
POST /tokens/validate
{
  "token-value": "bearer TOKEN_UUID",
  "level": "study",
  "method": "get",
  "orthanc-id": "study-id",
  "dicom-uid": "1.2.3.4.5",
  "uri": "/studies/study-id"
}
```

### Token Creation Endpoint
```bash
POST /tokens/ohif-viewer-publication
{
  "Resources": [
    {
      "Level": "study",
      "DicomUid": "1.2.3.4.5",
      "OrthancId": "study-id"
    }
  ],
  "ValidityDuration": 604800,
  "ExpirationDate": "2024-01-01T00:00:00Z"
}
```

### Token Management Endpoints
- `GET /tokens` - List active tokens
- `GET /tokens/expired` - List expired tokens
- `DELETE /tokens/{token_id}` - Revoke token
- `GET /tokens/stats` - Usage statistics

This token sharing system provides secure, controlled external access while maintaining full audit trails and administrative oversight.