# S3 Files Skill 📤

> An [OpenClaw](https://github.com/openclaw/openclaw) skill for secure file sharing via Amazon S3 with time-limited pre-signed URLs.

## What is This?

This skill teaches OpenClaw how to:

- Upload files to S3 and generate shareable download links
- Create pre-signed URLs for existing S3 objects  
- Generate upload pages for receiving files from others
- Manage secure file sharing without exposing S3 buckets publicly

Perfect for sharing logs, screenshots, packages, or any files with automatic expiration.

## Features

✅ **Upload & Share** - Upload files and get instant download links  
✅ **Clean Filenames** - Downloads have user-friendly names (no timestamps)  
✅ **Collision-Free** - Timestamp prefixes prevent overwriting  
✅ **Auto-Expiration** - Links expire after 24 hours (configurable)  
✅ **Upload Pages** - Create web pages for others to send you files  
✅ **No Public Bucket** - All access via secure pre-signed URLs  
✅ **Rate Limited** - Built-in protection against abuse  

## Prerequisites

- **AWS Account** with S3 access
- **IAM Permissions:**
  - `s3:PutObject`
  - `s3:GetObject`
- **Node.js** >= 18.0.0
- **S3 Bucket** (can be private)
- OpenClaw installed on an EC2 instance in AWS cloud

## Installation

### 1. Install the Skill

```bash
cd ~/.openclaw/workspace/skills
git clone https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock.git
cp -r sample-OpenClaw-on-AWS-with-Bedrock/skills/s3-files-skill/skills/s3-files ./
cd s3-files
npm install
```

### 2. Configure

Copy the example config:

```bash
cp config.example.json config.json
```

Edit `config.json` with your settings:

```json
{
  "bucketName": "your-s3-bucket-name",
  "region": "us-west-2",
  "defaultExpirationHours": 24,
  "maxUploadSizeMB": 100
}
```

### 3. Set Up IAM Permissions

Create an IAM policy with these permissions:

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

Attach to your IAM user or role.

### 4. Configure AWS Credentials

```bash
aws configure
# Enter your AWS Access Key ID
# Enter your AWS Secret Access Key  
# Enter your default region (e.g., us-west-2)
```

Or use environment variables:

```bash
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export AWS_REGION="us-west-2"
```

## Usage

### Upload a File

```bash
cd ~/.openclaw/workspace/skills/s3-files
node upload.js /path/to/report.pdf
```

**Output:**
```
📤 Uploading report.pdf...
✅ Upload complete!
🔗 Generating download URL...
📁 S3 Key: uploads/1772120357022-report.pdf
📥 Download as: report.pdf

✅ Download URL:
https://your-bucket.s3.us-west-2.amazonaws.com/uploads/1772120357022-report.pdf?...

📦 File: uploads/1772120357022-report.pdf (245.67 KB)
```

Share the download URL with anyone. They'll download it as `report.pdf` (clean filename, no timestamp).

### Generate Download URL for Existing File

```bash
node download-url.js uploads/1772120357022-report.pdf 48
# Generate 48-hour link for existing file
```

### Create Upload Page

Generate a web page for someone to upload files to you:

```bash
node generate-upload-page.js 50
# Max 50MB file size
```

**Output:**
```
📤 Generating upload page...
✅ Upload page created!

📄 Page URL (24h expiration):
https://your-bucket.s3.us-west-2.amazonaws.com/upload-page-1772120400000.html?...

📦 Files will be uploaded to S3 with key: upload-1772120400000
```

Share the page URL. When someone uploads, you'll get the file in S3 as `upload-1772120400000`.

## How It Works

### Hybrid Filename Approach

**The Problem:**  
Without prefixes, uploading `report.pdf` twice overwrites the first file.

**The Solution:**  
- **Storage:** Files stored with timestamp prefix (`uploads/1772120357022-report.pdf`)
- **Download:** Browser saves as clean filename (`report.pdf`)
- **How:** Uses S3's `Content-Disposition` header

**Result:**  
✅ No collisions (timestamp ensures uniqueness)  
✅ Clean downloads (users see friendly names)  
✅ Sortable by time (timestamp in S3 key)

### Pre-Signed URLs

All file access uses AWS pre-signed URLs:

1. **No public bucket needed** - Bucket stays private
2. **Automatic expiration** - Links expire after set time (default 24h)
3. **No credential sharing** - URLs include temporary credentials
4. **Secure access** - Only people with the link can download

## Use Cases

### Share Logs with Support

```bash
# Upload log file
node upload.js /var/log/application.log

# Send download URL to support team
# URL expires in 24 hours automatically
```

### Distribute Packages

```bash
# Upload APK/ZIP/installer
node upload.js ~/Downloads/app-v2.0.apk

# Share link with testers
# Clean filename: app-v2.0.apk (no timestamp)
```

### Receive Files from Others

```bash
# Generate upload page
node generate-upload-page.js 100

# Share page URL with colleague
# They upload file via browser
# You download it later
```

### Share Screenshots

```bash
# Upload screenshot
node upload.js ~/Desktop/screenshot.png

# Quick share link (expires in 24h)
```

## Architecture

```
┌──────────────┐
│              │
│    OpenClaw  │
│              │
└──────┬───────┘
       │
       │ upload.js / download-url.js
       │
       ▼
┌──────────────┐      ┌────────────────┐
│              │      │                │
│   AWS S3     │◄─────┤  Pre-signed    │
│   Bucket     │      │  URLs          │
│              │      │  (24h expire)  │
└──────┬───────┘      └────────────────┘
       │
       │ Download
       │
       ▼
┌──────────────┐
│              │
│   End User   │
│   Browser    │
│              │
└──────────────┘
```

**Flow:**
1. OpenClaw uploads file to S3 (with timestamp prefix)
2. Generates pre-signed URL (with clean filename header)
3. User clicks link → downloads as clean filename
4. URL expires automatically after 24 hours

## Security Best Practices

### Bucket Configuration

```bash
# Block public access (recommended)
aws s3api put-public-access-block \
  --bucket your-bucket-name \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# Enable versioning (optional, protects against accidental overwrites)
aws s3api put-bucket-versioning \
  --bucket your-bucket-name \
  --versioning-configuration Status=Enabled
```

### Lifecycle Rules

Auto-delete old files to save costs:

```json
{
  "Rules": [
    {
      "Id": "Delete old uploads",
      "Status": "Enabled",
      "Prefix": "uploads/",
      "Expiration": {
        "Days": 7
      }
    }
  ]
}
```

Apply with:
```bash
aws s3api put-bucket-lifecycle-configuration \
  --bucket your-bucket-name \
  --lifecycle-configuration file://lifecycle.json
```

### IAM Best Practices

1. **Use least-privilege permissions** (only PutObject, GetObject)
2. **Scope to specific bucket** (`arn:aws:s3:::bucket-name/*`)
3. **Don't use root credentials** (create IAM user/role)
4. **Rotate access keys regularly**
5. **Use IAM roles** for EC2/Lambda instead of keys

## Cost Estimate

**S3 Pricing (us-west-2, approximate):**

| Item | Price | Example Usage | Cost |
|------|-------|---------------|------|
| Storage | $0.023/GB/month | 10GB | $0.23 |
| PUT requests | $0.005/1000 | 100 uploads | $0.001 |
| GET requests | $0.0004/1000 | 1000 downloads | $0.0004 |
| Data transfer | First 100GB free | 50GB out | $0 |

**Typical monthly cost: < $1** for moderate use (100 uploads, 1000 downloads, 10GB storage)

*Prices vary by region. See [AWS S3 Pricing](https://aws.amazon.com/s3/pricing/) for details.*

## Troubleshooting

### "Access Denied" Error

**Cause:** Missing IAM permissions  
**Fix:** Add `s3:PutObject` and `s3:GetObject` to your IAM policy

### "Bucket not found" Error

**Cause:** Bucket name incorrect or wrong region  
**Fix:** Check `config.json` bucket name and region match your S3 bucket

### "Rate limit exceeded" Error

**Cause:** Too many requests in short time  
**Fix:** Wait 60 seconds. Built-in rate limiter allows 10 requests/minute

### Upload Page Not Working

**Cause:** Pre-signed credentials expired (1 hour validity)  
**Fix:** Regenerate upload page with `node generate-upload-page.js`

### File Size Too Large

**Cause:** File exceeds maxUploadSizeMB setting  
**Fix:** Increase in `config.json` or split file into chunks

## Advanced Configuration

### Custom Expiration Times

```bash
# Upload with 48-hour link
node upload.js file.pdf
node download-url.js uploads/1234-file.pdf 48

# Upload page with 2-hour validity
# (Edit generate-upload-page.js: Expires: 7200)
```

### Custom S3 Keys

```bash
# Upload with custom key structure
node upload.js file.pdf my-prefix/custom-name.pdf
```

### Programmatic Usage

```javascript
const { uploadFile } = require('./upload.js');
const { generateDownloadUrl } = require('./download-url.js');
const { generateUploadPage } = require('./generate-upload-page.js');

// Upload file
const result = await uploadFile('/path/to/file.pdf');
console.log('Download URL:', result.downloadUrl);

// Generate URL for existing file
const url = await generateDownloadUrl('uploads/file.pdf', 48);

// Create upload page
const page = await generateUploadPage(100);
console.log('Upload page:', page.pageUrl);
```

## Files Included

```
s3-files/
├── upload.js                # Upload files to S3
├── download-url.js          # Generate pre-signed download URLs  
├── generate-upload-page.js  # Create upload pages
├── config.example.json      # Configuration template
├── package.json             # Node.js dependencies
└── SKILL.md                 # Skill metadata for OpenClaw
```

## Contributing

Found a bug or have a feature request? 

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

This project is licensed under the MIT-0 License - see the [LICENSE](../../LICENSE) file for details.

## Additional Resources

- [OpenClaw Documentation](https://docs.openclaw.ai/)
- [AWS S3 Documentation](https://docs.aws.amazon.com/s3/)
- [Pre-signed URLs Guide](https://docs.aws.amazon.com/AmazonS3/latest/userguide/ShareObjectPreSignedURL.html)
- [AWS SDK for JavaScript](https://docs.aws.amazon.com/sdk-for-javascript/)

## Support

- **Issues:** [GitHub Issues](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/issues)
- **Discussions:** [GitHub Discussions](https://github.com/aws-samples/sample-OpenClaw-on-AWS-with-Bedrock/discussions)
- **OpenClaw Discord:** [Join Community](https://discord.gg/openclaw)

---

**Made with ❤️ for the OpenClaw community**
