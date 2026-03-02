#!/usr/bin/env node
const { S3Client, PutObjectCommand } = require('@aws-sdk/client-s3');
const { getSignedUrl } = require('@aws-sdk/s3-request-presigner');
const { GetObjectCommand } = require('@aws-sdk/client-s3');
const { createPresignedPost } = require('@aws-sdk/s3-presigned-post');
const fs = require('fs');
const path = require('path');
const config = require('./config.json');

const s3Client = new S3Client({ region: config.region });

const HTML_TEMPLATE = `<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Upload to S3</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { 
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .container {
      background: white;
      border-radius: 16px;
      padding: 40px;
      max-width: 500px;
      width: 100%;
      box-shadow: 0 20px 60px rgba(0,0,0,0.3);
    }
    h1 { 
      color: #667eea;
      margin-bottom: 10px;
      font-size: 28px;
    }
    .subtitle {
      color: #666;
      margin-bottom: 30px;
      font-size: 14px;
    }
    .upload-area {
      border: 3px dashed #667eea;
      border-radius: 12px;
      padding: 60px 20px;
      text-align: center;
      background: #f8f9ff;
      cursor: pointer;
      transition: all 0.3s;
      margin-bottom: 20px;
    }
    .upload-area:hover {
      border-color: #764ba2;
      background: #f0f2ff;
    }
    .upload-area.dragover {
      border-color: #764ba2;
      background: #e8ebff;
      transform: scale(1.02);
    }
    .upload-icon {
      font-size: 48px;
      margin-bottom: 15px;
    }
    .upload-text {
      color: #667eea;
      font-size: 18px;
      font-weight: 600;
      margin-bottom: 8px;
    }
    .upload-hint {
      color: #999;
      font-size: 14px;
    }
    input[type="file"] {
      display: none;
    }
    button {
      width: 100%;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      color: white;
      border: none;
      padding: 16px;
      border-radius: 8px;
      font-size: 16px;
      font-weight: 600;
      cursor: pointer;
      transition: transform 0.2s;
    }
    button:hover {
      transform: translateY(-2px);
    }
    button:disabled {
      background: #ccc;
      cursor: not-allowed;
      transform: none;
    }
    .file-info {
      background: #f8f9ff;
      padding: 15px;
      border-radius: 8px;
      margin: 20px 0;
      display: none;
    }
    .file-info.show {
      display: block;
    }
    .file-name {
      font-weight: 600;
      color: #333;
      margin-bottom: 5px;
    }
    .file-size {
      color: #666;
      font-size: 14px;
    }
    .progress-container {
      margin-top: 20px;
      display: none;
    }
    .progress-container.show {
      display: block;
    }
    .progress-bar {
      height: 8px;
      background: #e0e0e0;
      border-radius: 4px;
      overflow: hidden;
      margin-bottom: 10px;
    }
    .progress-fill {
      height: 100%;
      background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
      width: 0%;
      transition: width 0.3s;
    }
    .progress-text {
      text-align: center;
      color: #666;
      font-size: 14px;
    }
    .status {
      margin-top: 20px;
      padding: 15px;
      border-radius: 8px;
      display: none;
      text-align: center;
      font-weight: 500;
    }
    .status.show {
      display: block;
    }
    .status.success {
      background: #d4edda;
      color: #155724;
    }
    .status.error {
      background: #f8d7da;
      color: #721c24;
    }
  </style>
</head>
<body>
  <div class="container">
    <h1>📤 Upload to S3</h1>
    <div class="subtitle">Drop your file or click to browse</div>
    
    <div class="upload-area" id="uploadArea">
      <div class="upload-icon">📁</div>
      <div class="upload-text">Choose a file or drag it here</div>
      <div class="upload-hint">Max __MAX_SIZE__ MB</div>
    </div>
    
    <input type="file" id="fileInput" />
    
    <div class="file-info" id="fileInfo">
      <div class="file-name" id="fileName"></div>
      <div class="file-size" id="fileSize"></div>
    </div>
    
    <button id="uploadBtn" disabled>Select a file first</button>
    
    <div class="progress-container" id="progressContainer">
      <div class="progress-bar">
        <div class="progress-fill" id="progressFill"></div>
      </div>
      <div class="progress-text" id="progressText">Uploading...</div>
    </div>
    
    <div class="status" id="status"></div>
  </div>

  <script>
    const UPLOAD_URL = __UPLOAD_URL__;
    const UPLOAD_FIELDS = __UPLOAD_FIELDS__;

    const uploadArea = document.getElementById('uploadArea');
    const fileInput = document.getElementById('fileInput');
    const fileInfo = document.getElementById('fileInfo');
    const fileName = document.getElementById('fileName');
    const fileSize = document.getElementById('fileSize');
    const uploadBtn = document.getElementById('uploadBtn');
    const progressContainer = document.getElementById('progressContainer');
    const progressFill = document.getElementById('progressFill');
    const progressText = document.getElementById('progressText');
    const status = document.getElementById('status');

    let selectedFile = null;

    uploadArea.addEventListener('click', () => fileInput.click());

    uploadArea.addEventListener('dragover', (e) => {
      e.preventDefault();
      uploadArea.classList.add('dragover');
    });

    uploadArea.addEventListener('dragleave', () => {
      uploadArea.classList.remove('dragover');
    });

    uploadArea.addEventListener('drop', (e) => {
      e.preventDefault();
      uploadArea.classList.remove('dragover');
      const files = e.dataTransfer.files;
      if (files.length > 0) {
        handleFile(files[0]);
      }
    });

    fileInput.addEventListener('change', (e) => {
      if (e.target.files.length > 0) {
        handleFile(e.target.files[0]);
      }
    });

    function handleFile(file) {
      selectedFile = file;
      const sizeMB = (file.size / (1024 * 1024)).toFixed(2);
      
      fileName.textContent = file.name;
      fileSize.textContent = \`\${sizeMB} MB\`;
      fileInfo.classList.add('show');
      
      uploadBtn.disabled = false;
      uploadBtn.textContent = 'Upload to S3';
      
      status.classList.remove('show');
    }

    uploadBtn.addEventListener('click', async () => {
      if (!selectedFile) return;

      uploadBtn.disabled = true;
      uploadBtn.textContent = 'Uploading...';
      progressContainer.classList.add('show');
      status.classList.remove('show');

      const formData = new FormData();
      
      Object.entries(UPLOAD_FIELDS).forEach(([key, value]) => {
        formData.append(key, value);
      });
      
      formData.append('file', selectedFile);

      try {
        const xhr = new XMLHttpRequest();

        xhr.upload.addEventListener('progress', (e) => {
          if (e.lengthComputable) {
            const percent = Math.round((e.loaded / e.total) * 100);
            progressFill.style.width = percent + '%';
            progressText.textContent = \`Uploading... \${percent}%\`;
          }
        });

        xhr.addEventListener('load', () => {
          if (xhr.status === 204 || xhr.status === 200) {
            status.textContent = '✅ Upload successful!';
            status.className = 'status success show';
            uploadBtn.textContent = 'Upload another file';
            uploadBtn.disabled = false;
            fileInfo.classList.remove('show');
            progressContainer.classList.remove('show');
            selectedFile = null;
            fileInput.value = '';
          } else {
            throw new Error(\`Upload failed: \${xhr.status}\`);
          }
        });

        xhr.addEventListener('error', () => {
          status.textContent = '❌ Upload failed. Please try again.';
          status.className = 'status error show';
          uploadBtn.disabled = false;
          uploadBtn.textContent = 'Try again';
          progressContainer.classList.remove('show');
        });

        xhr.open('POST', UPLOAD_URL);
        xhr.send(formData);

      } catch (error) {
        status.textContent = '❌ Error: ' + error.message;
        status.className = 'status error show';
        uploadBtn.disabled = false;
        uploadBtn.textContent = 'Try again';
        progressContainer.classList.remove('show');
      }
    });
  </script>
</body>
</html>`;

async function generateUploadPage(maxSizeMB) {
  const maxSize = maxSizeMB || config.maxUploadSizeMB;
  const key = `upload-${Date.now()}`;
  
  console.log(`📤 Generating upload page for: ${key}`);
  console.log(`📏 Max file size: ${maxSize} MB`);
  console.log(`⏰ Expiration: 1 hour\n`);

  // Generate pre-signed POST credentials
  const { url, fields } = await createPresignedPost(s3Client, {
    Bucket: config.bucketName,
    Key: key,
    Conditions: [
      ['content-length-range', 0, maxSize * 1024 * 1024],
    ],
    Expires: 3600,
  });

  // Create HTML page with credentials embedded
  let html = HTML_TEMPLATE;
  html = html.replace('__UPLOAD_URL__', JSON.stringify(url));
  html = html.replace('__UPLOAD_FIELDS__', JSON.stringify(fields));
  html = html.replace('__MAX_SIZE__', maxSize);

  // Upload the page to S3
  const pageName = `upload-page-${Date.now()}.html`;
  const pageCommand = new PutObjectCommand({
    Bucket: config.bucketName,
    Key: pageName,
    Body: html,
    ContentType: 'text/html',
  });

  await s3Client.send(pageCommand);

  // Generate download URL for the page
  const pageDownloadCommand = new GetObjectCommand({
    Bucket: config.bucketName,
    Key: pageName,
  });

  const pageUrl = await getSignedUrl(s3Client, pageDownloadCommand, { 
    expiresIn: 86400 // 24 hours
  });

  console.log(`✅ Upload page created!\n`);
  console.log(`📄 Page URL (24h expiration):`);
  console.log(pageUrl);
  console.log(`\n📦 Files will be uploaded to S3 with key: ${key}`);

  return { pageUrl, uploadKey: key };
}

// CLI usage
if (require.main === module) {
  const maxSizeMB = parseInt(process.argv[2]) || config.maxUploadSizeMB;

  generateUploadPage(maxSizeMB).catch(err => {
    console.error('❌ Failed:', err.message);
    process.exit(1);
  });
}

module.exports = { generateUploadPage };
