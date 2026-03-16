# CLI 使用参考

> 本文档涵盖所有脚本的详细命令行用法。
> 日常 OpenClaw 对话使用无需阅读此文档，AI 会自动调用对应脚本。

---

## 公共参数

所有脚本支持以下参数：

| 参数 | 必需 | 说明 |
|------|:---:|------|
| `--bucket` | ✅ | 向量桶名称（list_vector_buckets 除外） |
| `--region` | ❌ | AWS Region，默认 `ap-northeast-1` 或 `AWS_DEFAULT_REGION` |
| `--profile` | ❌ | AWS CLI Profile 名称 |

---

## 一、向量桶管理

### 1. 创建向量桶

```bash
python3 scripts/create_vector_bucket.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1" \
  --sse-type SSE-S3    # 可选，启用 SSE-S3 加密（也支持 SSE-KMS）
```

### 2. 查询向量桶信息

```bash
python3 scripts/get_vector_bucket.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1"
```

### 3. 列出所有向量桶

```bash
python3 scripts/list_vector_buckets.py \
  --region "ap-northeast-1" \
  --max-results 20 \   # 可选，限制返回数量
  --prefix "my-"       # 可选，前缀过滤
```

### 4. 删除向量桶

```bash
python3 scripts/delete_vector_bucket.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1"
```

---

## 二、桶策略管理

### 5. 设置桶策略

```bash
python3 scripts/put_vector_bucket_policy.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1" \
  --policy '{"Statement": [{"Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::123456789012:role/MyRole"}, "Action": "s3vectors:*", "Resource": "*"}]}'
```

### 6. 获取桶策略

```bash
python3 scripts/get_vector_bucket_policy.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1"
```

### 7. 删除桶策略

```bash
python3 scripts/delete_vector_bucket_policy.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1"
```

---

## 三、索引管理

### 8. 创建索引

```bash
python3 scripts/create_index.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1" \
  --index "my-index" \
  --dimension 1024 \
  --data-type float32 \
  --distance-metric cosine
```

| 参数 | 必需 | 说明 |
|------|:---:|------|
| `--index` | ✅ | 索引名称 |
| `--dimension` | ✅ | 向量维度（1-4096），Bedrock Titan v2 推荐 1024 |
| `--data-type` | ❌ | 数据类型，默认 `float32` |
| `--distance-metric` | ❌ | 距离度量：`cosine`（默认）或 `euclidean` |
| `--non-filterable-keys` | ❌ | 非过滤元数据键，逗号分隔 |

### 9. 查询索引信息

```bash
python3 scripts/get_index.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1" \
  --index "my-index"
```

### 10. 列出所有索引

```bash
python3 scripts/list_indexes.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1" \
  --max-results 10
```

### 11. 删除索引

```bash
python3 scripts/delete_index.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1" \
  --index "my-index"
```

---

## 四、向量数据操作

### 12. 插入/更新向量

**方式 1：命令行传入 JSON**
```bash
python3 scripts/put_vectors.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1" \
  --index "my-index" \
  --vectors '[{"key":"doc-001","data":{"float32":[0.1,0.2,0.3]},"metadata":{"title":"文档1","category":"AI"}}]'
```

**方式 2：通过文件传入**
```bash
cat > vectors.json << 'EOF'
[
  {
    "key": "doc-001",
    "data": {"float32": [0.1, 0.2, 0.3]},
    "metadata": {"title": "人工智能简介", "category": "AI"}
  },
  {
    "key": "doc-002",
    "data": {"float32": [0.4, 0.5, 0.6]},
    "metadata": {"title": "机器学习算法", "category": "AI"}
  }
]
EOF

python3 scripts/put_vectors.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1" \
  --index "my-index" \
  --vectors-file vectors.json
```

### 13. 获取指定向量

```bash
python3 scripts/get_vectors.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1" \
  --index "my-index" \
  --keys "doc-001,doc-002" \
  --return-data \
  --return-metadata
```

### 14. 列出向量列表

```bash
python3 scripts/list_vectors.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1" \
  --index "my-index" \
  --max-results 10 \
  --return-metadata
```

### 15. 删除向量

```bash
python3 scripts/delete_vectors.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1" \
  --index "my-index" \
  --keys "doc-001,doc-002"
```

### 16. 相似度搜索

```bash
python3 scripts/query_vectors.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1" \
  --index "my-index" \
  --query-vector '[0.1, 0.2, 0.3]' \
  --top-k 5 \
  --filter '{"category": {"$eq": "AI"}}' \
  --return-metadata
```

也可以通过文件传入查询向量：
```bash
python3 scripts/query_vectors.py \
  --bucket "my-skill-vectors" \
  --region "ap-northeast-1" \
  --index "my-index" \
  --query-vector-file query.json \
  --top-k 5 \
  --return-metadata
```

---

## 输出格式

所有脚本统一输出 JSON 格式：

```json
// 成功
{"success": true, "action": "create_index", "bucket": "...", "index": "...", ...}

// 失败
{"success": false, "error": "错误信息", "error_code": "...", "request_id": "..."}
```

## 公共模块 `common.py`

| 函数 | 功能 |
|------|------|
| `base_parser()` | 创建包含区域和认证参数的基础解析器 |
| `create_client()` | 初始化 boto3 s3vectors 客户端 |
| `success_output()` | 统一的成功输出格式 |
| `fail()` | 统一的错误输出格式并退出 |
| `handle_error()` | 统一异常处理（ClientError / BotoCoreError） |
| `run()` | 包装主函数并捕获异常 |
