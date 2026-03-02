---
name: s3-files
description: Upload and share files via Amazon S3 with time-limited pre-signed URLs. Generate download links, create upload pages for receiving files, and manage secure file sharing without exposing S3 buckets publicly.
metadata:
  {
    "openclaw": {
      "emoji": "📤",
      "requires": { "aws": ["s3"], "node": ">=18.0.0" },
      "homepage": "https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock"
    }
  }
---

# S3 Files Skill

Upload and share files via Amazon S3 with automatic expiration and clean download filenames.

## Features

- 📤 Upload files to S3 and generate shareable download links
- 🔗 Create pre-signed URLs for existing S3 objects  
- 📥 Generate upload pages for receiving files from others
- ⏰ Automatic expiration (configurable, default 24 hours)
- 🔒 No public S3 buckets required
- ✨ Clean download filenames (hybrid approach prevents collisions)

## Quick Reference

| Command | Purpose |
|---------|---------|
| `node upload.js <file-path>` | Upload file and get download link |
| `node download-url.js <s3-key>` | Generate download URL for existing file |
| `node generate-upload-page.js [max-size-mb]` | Create upload page for receiving files |

## Upload File

```bash
cd ~/.openclaw/workspace/skills/s3-files
node upload.js /path/to/file.pdf
```

**Output:**
- File uploaded to S3 with timestamp prefix (collision-free)
- Download URL with 24-hour expiration
- Clean filename for downloads (no timestamp visible)

**Example:**
```
📤 Uploading report.pdf...
✅ Upload complete!
📁 S3 Key: uploads/1772120357022-report.pdf
📥 Download as: report.pdf
🔗 Download URL (24h):
https://bucket.s3.amazonaws.com/uploads/1772120357022-report.pdf?...&response-content-disposition=attachment%3B%20filename%3D%22report.pdf%22
```

## Generate Download URL

For files already in S3:

```bash
node download-url.js uploads/1234567890-file.zip [hours]
```

**Parameters:**
- `s3-key`: Full S3 key (e.g., `uploads/1234567890-file.zip`)
- `hours`: Optional expiration in hours (default: 24)

## Create Upload Page

Generate a web page for others to upload files to your S3 bucket:

```bash
node generate-upload-page.js 50  # Max 50MB upload
```

**Output:**
- HTML page uploaded to S3
- Pre-signed upload credentials embedded (1-hour validity)
- Page URL with 24-hour expiration
- Uploaded files appear as `upload-{timestamp}` in S3

**Use case:** Share the page URL with someone who needs to send you files.

## Configuration

Copy `config.example.json` to `config.json`:

```json
{
  "bucketName": "your-bucket-name",
  "region": "us-west-2",
  "defaultExpirationHours": 24,
  "maxUploadSizeMB": 100
}
```

**Required IAM Permissions:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::your-bucket-name/*"
    }
  ]
}
```

## How It Works

### Hybrid Filename Approach

**Storage (S3 key):**
- Files stored with timestamp prefix: `uploads/1772120357022-report.pdf`
- Prevents filename collisions
- Sortable by upload time

**Download (browser):**
- Uses `Content-Disposition` header to suggest clean filename
- Browser saves as: `report.pdf` (no timestamp)
- Users see friendly filenames

**Technical:**
```javascript
// S3 key has timestamp
const key = "uploads/1772120357022-report.pdf";

// Download URL includes Content-Disposition
ResponseContentDisposition: 'attachment; filename="report.pdf"'

// Result: Browser saves as "report.pdf"
```

### Pre-Signed URLs

All downloads use AWS S3 pre-signed URLs:
- No S3 bucket needs to be public
- URLs expire automatically (default 24 hours)
- Secure access without managing credentials

## Security Best Practices

1. **Bucket Configuration**
   - Keep bucket private (block public access)
   - Enable versioning for accidental overwrites
   - Configure lifecycle rules to auto-delete old files

2. **IAM Permissions**
   - Use least-privilege permissions (PutObject, GetObject only)
   - Scope to specific bucket: `arn:aws:s3:::bucket-name/*`
   - Don't use root credentials

3. **Input Validation**
   - File paths are sanitized (no directory traversal)
   - Filenames cleaned of special characters
   - File size limits enforced

4. **Expiration**
   - Default 24-hour URL expiration
   - Adjust based on use case
   - Upload pages expire in 1 hour

5. **Rate Limiting**
   - Built-in rate limiter (10 requests/minute)
   - Prevents abuse and cost overruns

## Troubleshooting

**Error: "File not found"**
- Check file path is correct
- Ensure file exists and is readable

**Error: "Failed to generate download URL"**
- Verify S3 key exists in bucket
- Check IAM permissions
- Ensure AWS credentials are configured

**Error: "Rate limit exceeded"**
- Wait 60 seconds
- Built-in protection against rapid API calls

**Upload page not loading**
- Check if URL expired (1 hour for page credentials)
- Regenerate upload page

## Advanced Usage

### Custom S3 Key

```bash
node upload.js file.pdf uploads/custom-name.pdf
```

### Different Expiration

```bash
node download-url.js uploads/file.zip 48  # 48 hours
```

### Programmatic Use

```javascript
const { uploadFile } = require('./upload.js');
const { generateDownloadUrl } = require('./download-url.js');

// Upload
const { key, downloadUrl } = await uploadFile('/path/to/file.pdf');

// Generate URL
const url = await generateDownloadUrl(key, 24);
```

## Files Included

```
s3-files/
├── upload.js                  # Upload files and get download links
├── download-url.js            # Generate pre-signed download URLs
├── generate-upload-page.js    # Create upload pages for others
├── config.example.json        # Configuration template
└── package.json               # Node.js dependencies
```

## Dependencies

```json
{
  "@aws-sdk/client-s3": "^3.x",
  "@aws-sdk/s3-request-presigner": "^3.x",
  "@aws-sdk/s3-presigned-post": "^3.x"
}
```

Install with:
```bash
npm install
```

## Example Workflow

**Scenario:** Share a log file with a colleague for 6 hours

```bash
# 1. Upload the file
node upload.js /var/log/app.log

# Output includes download URL
# 📥 Download as: app.log
# 🔗 Download URL (24h): https://...

# 2. Send URL to colleague (they download as "app.log")

# 3. URL expires in 24 hours automatically
```

**Scenario:** Receive a file from someone

```bash
# 1. Generate upload page
node generate-upload-page.js 100

# Output:
# 📄 Page URL (24h expiration): https://...
# 📦 Files will be uploaded to S3 with key: upload-1234567890

# 2. Send page URL to person

# 3. They upload file via browser

# 4. Generate download URL for the uploaded file
node download-url.js upload-1234567890
```

## Cost Considerations

**S3 Pricing (us-west-2 example):**
- Storage: ~$0.023/GB/month
- PUT requests: ~$0.005/1000 requests
- GET requests: ~$0.0004/1000 requests
- Data transfer out: First 100GB free/month

**Typical costs for file sharing:**
- Uploading 100 files/month: < $0.10
- 1000 downloads/month: < $0.50
- 10GB storage: < $0.25/month

**Total: < $1/month for moderate use**

## Rules for OpenClaw

1. **Always ask before uploading files**
   - Confirm file path and destination
   - Explain expiration time

2. **Share URLs responsibly**
   - Remind user URLs expire
   - Don't share sensitive files without encryption

3. **Clean up old files**
   - Suggest S3 lifecycle rules
   - Don't let bucket fill up indefinitely

4. **Respect rate limits**
   - Wait if rate limited
   - Don't retry rapidly

5. **Verify before generating upload pages**
   - Upload pages allow anyone to upload
   - Only create when explicitly requested

## Additional Resources

- [AWS S3 Documentation](https://docs.aws.amazon.com/s3/)
- [Pre-signed URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/ShareObjectPreSignedURL.html)
- [OpenClaw Skills Guide](https://docs.openclaw.ai/skills/)
