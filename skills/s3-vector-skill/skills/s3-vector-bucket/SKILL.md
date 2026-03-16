---
name: s3-vector-bucket
description: "Amazon S3 向量桶全功能管理技能。覆盖向量桶、索引、向量数据的全生命周期（16 个核心 CRUD 能力）。含 Skill 路由子系统，通过 S3 Vectors 相似度搜索动态筛选 Top-K Skill，Token 降本约 91%。基于 S3 Vectors（re:Invent 2025 GA）+ Bedrock Titan v2（1024d），成本比传统向量 DB 低约 90%。"
triggers:
  - vector bucket
  - vector index
  - vector search
  - skill router
  - skill routing
  - 向量桶
  - 向量索引
  - 向量搜索
  - 向量存储
  - 插入向量
  - 相似度搜索
  - S3 vector
  - S3 vectors
  - Skill 路由
  - Token 降本
---

# Amazon S3 向量桶全功能管理技能

通过 boto3 的 `s3vectors` 客户端管理 Amazon S3 向量桶的完整生命周期。

> **S3 Vectors** 于 2025年12月 re:Invent 正式 GA，单索引支持 20 亿向量，查询延迟 < 100ms，
> 成本比 OpenSearch / pgvector 等方案低 90%。

## 能力总览

| 类别 | 能力 | 脚本 |
|------|------|------|
| **向量桶管理** | 创建向量桶 | `create_vector_bucket.py` |
| | 删除向量桶 | `delete_vector_bucket.py` |
| | 查询向量桶信息 | `get_vector_bucket.py` |
| | 列出所有向量桶 | `list_vector_buckets.py` |
| **桶策略管理** | 设置桶策略 | `put_vector_bucket_policy.py` |
| | 获取桶策略 | `get_vector_bucket_policy.py` |
| | 删除桶策略 | `delete_vector_bucket_policy.py` |
| **索引管理** | 创建索引 | `create_index.py` |
| | 查询索引信息 | `get_index.py` |
| | 列出所有索引 | `list_indexes.py` |
| | 删除索引 | `delete_index.py` |
| **向量数据操作** | 插入/更新向量 | `put_vectors.py` |
| | 获取指定向量 | `get_vectors.py` |
| | 列出向量列表 | `list_vectors.py` |
| | 删除向量 | `delete_vectors.py` |
| | 相似度搜索 | `query_vectors.py` |

---

## 首次使用 — 环境检查

### 步骤 1：检查 boto3

```bash
python3 -c "import boto3; client = boto3.client('s3vectors', region_name='ap-northeast-1'); print('OK')"
```

如果失败，安装/升级 boto3：

```bash
pip3 install boto3 --upgrade
```

### 步骤 2：检查 AWS 凭证

本机运行时优先使用 IAM Role（EC2 实例 Profile），无需手动配置凭证。

如需手动指定，支持以下方式（优先级从高到低）：

1. **环境变量**（临时使用）：
   ```bash
   export AWS_ACCESS_KEY_ID=AKIA...
   export AWS_SECRET_ACCESS_KEY=...
   export AWS_DEFAULT_REGION=ap-northeast-1
   ```

2. **AWS Profile**（通过 `--profile` 参数）：
   ```bash
   aws configure --profile my-profile
   python3 create_vector_bucket.py --bucket my-bucket --region ap-northeast-1 --profile my-profile
   ```

3. **实例 IAM Role**（推荐，EC2/EKS 上自动生效，无需配置）

### 步骤 3：确认 S3 Vectors 可用性

```bash
aws s3vectors list-vector-buckets --region ap-northeast-1
```

如提示 `Could not connect to the endpoint URL`，说明该 Region 尚未支持 S3 Vectors。
目前已支持的 Region：us-east-1, us-west-2, eu-west-1, ap-northeast-1（东京）等主要 Region。

### 公共参数

所有脚本都支持以下公共参数：

| 参数 | 必需 | 说明 |
|------|:---:|------|
| `--bucket` | ✅ | 向量桶名称（list_vector_buckets 除外） |
| `--region` | ❌ | AWS Region，默认 `ap-northeast-1` 或 `AWS_DEFAULT_REGION` 环境变量 |
| `--profile` | ❌ | AWS CLI 配置文件名（默认使用实例 IAM Role） |

---

## 一、向量桶管理

### 1. 创建向量桶

```bash
python3 {baseDir}/scripts/create_vector_bucket.py \
  --bucket "<BucketName>" \
  --region "<Region>" \
  [--sse-type SSE-S3|SSE-KMS] \
  [--kms-key-arn "<KmsKeyArn>"]
```

| 专有参数 | 说明 |
|----------|------|
| `--sse-type` | 可选，加密类型：`SSE-S3`（S3 托管密钥）或 `SSE-KMS`（KMS 密钥） |
| `--kms-key-arn` | 可选，KMS 密钥 ARN，仅 `--sse-type SSE-KMS` 时需要 |

### 2. 删除向量桶

```bash
python3 {baseDir}/scripts/delete_vector_bucket.py \
  --bucket "<BucketName>" \
  --region "<Region>"
```

### 3. 查询向量桶信息

```bash
python3 {baseDir}/scripts/get_vector_bucket.py \
  --bucket "<BucketName>" \
  --region "<Region>"
```

### 4. 列出所有向量桶

```bash
python3 {baseDir}/scripts/list_vector_buckets.py \
  --region "<Region>" \
  [--max-results 10] \
  [--prefix "my-"] \
  [--next-token "<Token>"]
```

| 专有参数 | 说明 |
|----------|------|
| `--max-results` | 可选，最大返回数量 |
| `--prefix` | 可选，桶名前缀过滤 |
| `--next-token` | 可选，分页 Token |

---

## 二、桶策略管理

### 5. 设置桶策略

```bash
python3 {baseDir}/scripts/put_vector_bucket_policy.py \
  --bucket "<BucketName>" \
  --region "<Region>" \
  --policy '{"Statement": [{"Effect":"Allow","Principal":{"AWS":"arn:aws:iam::123456789012:role/MyRole"},"Action":"s3vectors:*","Resource":"*"}]}'
```

### 6. 获取桶策略

```bash
python3 {baseDir}/scripts/get_vector_bucket_policy.py \
  --bucket "<BucketName>" \
  --region "<Region>"
```

### 7. 删除桶策略

```bash
python3 {baseDir}/scripts/delete_vector_bucket_policy.py \
  --bucket "<BucketName>" \
  --region "<Region>"
```

---

## 三、索引管理

### 8. 创建索引

```bash
python3 {baseDir}/scripts/create_index.py \
  --bucket "<BucketName>" \
  --region "<Region>" \
  --index "<IndexName>" \
  --dimension <Dimension> \
  [--data-type float32] \
  [--distance-metric cosine] \
  [--non-filterable-keys key1,key2]
```

| 专有参数 | 必需 | 说明 |
|----------|:---:|------|
| `--index` | ✅ | 索引名称 |
| `--dimension` | ✅ | 向量维度，范围 1-4096 |
| `--data-type` | ❌ | 数据类型，默认 `float32` |
| `--distance-metric` | ❌ | 距离度量，`cosine`（默认）或 `euclidean` |
| `--non-filterable-keys` | ❌ | 非过滤元数据键，逗号分隔 |

### 9. 查询索引信息

```bash
python3 {baseDir}/scripts/get_index.py \
  --bucket "<BucketName>" \
  --region "<Region>" \
  --index "<IndexName>"
```

### 10. 列出所有索引

```bash
python3 {baseDir}/scripts/list_indexes.py \
  --bucket "<BucketName>" \
  --region "<Region>" \
  [--max-results 10] \
  [--prefix "demo-"]
```

### 11. 删除索引

```bash
python3 {baseDir}/scripts/delete_index.py \
  --bucket "<BucketName>" \
  --region "<Region>" \
  --index "<IndexName>"
```

---

## 四、向量数据操作

### 12. 插入/更新向量

```bash
# 方式 1：直接传 JSON
python3 {baseDir}/scripts/put_vectors.py \
  --bucket "<BucketName>" \
  --region "<Region>" \
  --index "<IndexName>" \
  --vectors '[{"key":"doc-001","data":{"float32":[0.1,0.2,...]},"metadata":{"title":"标题"}}]'

# 方式 2：通过文件传入
python3 {baseDir}/scripts/put_vectors.py \
  --bucket "<BucketName>" \
  --region "<Region>" \
  --index "<IndexName>" \
  --vectors-file vectors.json
```

**向量 JSON 格式**：
```json
[
  {
    "key": "doc-001",
    "data": {"float32": [0.1, 0.2, 0.3, "..."]},
    "metadata": {"title": "文档标题", "category": "分类"}
  }
]
```

### 13. 获取指定向量

```bash
python3 {baseDir}/scripts/get_vectors.py \
  --bucket "<BucketName>" \
  --region "<Region>" \
  --index "<IndexName>" \
  --keys "doc-001,doc-002" \
  [--return-data] \
  [--return-metadata]
```

### 14. 列出向量列表

```bash
python3 {baseDir}/scripts/list_vectors.py \
  --bucket "<BucketName>" \
  --region "<Region>" \
  --index "<IndexName>" \
  [--max-results 10] \
  [--return-data] \
  [--return-metadata] \
  [--segment-count 4] \
  [--segment-index 0]
```

| 专有参数 | 说明 |
|----------|------|
| `--segment-count` | 可选，并行分段总数（用于大规模并行遍历） |
| `--segment-index` | 可选，当前分段索引（从 0 开始） |

### 15. 删除向量

```bash
python3 {baseDir}/scripts/delete_vectors.py \
  --bucket "<BucketName>" \
  --region "<Region>" \
  --index "<IndexName>" \
  --keys "doc-001,doc-002"
```

### 16. 相似度搜索（query_vectors）

```bash
# 方式 1：直接传查询向量
python3 {baseDir}/scripts/query_vectors.py \
  --bucket "<BucketName>" \
  --region "<Region>" \
  --index "<IndexName>" \
  --query-vector '[0.1, 0.2, ...]' \
  --top-k 5 \
  [--filter '{"category": {"$eq": "AI"}}'] \
  [--return-metadata]

# 方式 2：通过文件传入
python3 {baseDir}/scripts/query_vectors.py \
  --bucket "<BucketName>" \
  --region "<Region>" \
  --index "<IndexName>" \
  --query-vector-file query.json \
  --top-k 5
```

| 专有参数 | 必需 | 说明 |
|----------|:---:|------|
| `--index` | ✅ | 索引名称 |
| `--query-vector` | ✅* | 查询向量 JSON 数组 |
| `--query-vector-file` | ✅* | 查询向量文件（与 --query-vector 二选一） |
| `--top-k` | ✅ | 返回最相似的 K 个结果 |
| `--filter` | ❌ | 过滤条件 JSON |
| `--return-metadata` | ❌ | 返回元数据 |

---

## 关键技术细节

1. **boto3 客户端**：使用 `boto3.client('s3vectors', region_name=...)` 专用客户端
2. **参数命名**：boto3 使用 camelCase（如 `vectorBucketName`、`indexName`、`topK`）
3. **认证优先级**：实例 IAM Role > 环境变量 > AWS Profile > 显式凭证
4. **桶名规则**：小写字母、数字和连字符 `-`，3-63 个字符，全局唯一
5. **向量维度**：范围 1-4096，推荐使用 Bedrock Titan Embeddings v2（1024 维）
6. **数据类型**：当前支持 `float32`
7. **距离度量**：`cosine`（余弦相似度，推荐 RAG 场景）或 `euclidean`（欧氏距离）
8. **加密类型**：`SSE-S3`（默认免费）或 `SSE-KMS`（使用 AWS KMS 密钥）
9. **规模上限**：单索引最多 20 亿向量，查询延迟 < 100ms

## 公共模块

所有脚本共享 `common.py` 公共模块，提供：
- `base_parser()` — 创建包含区域和认证参数的基础解析器
- `create_client()` — 初始化 boto3 s3vectors 客户端
- `success_output()` — 统一的成功输出格式（JSON）
- `fail()` — 统一的错误输出格式并退出
- `handle_error()` — 统一的异常处理（ClientError / BotoCoreError）
- `run()` — 包装主函数并捕获异常

## 错误处理

- **ClientError**：服务端错误（如桶已存在、权限不足、Region 不支持等）
- **BotoCoreError**：客户端错误（如网络问题、凭证无效等）

所有脚本输出统一 JSON 格式：
```json
{"success": true, "action": "...", ...}   // 成功
{"success": false, "error": "..."}        // 失败
```

调用失败时先检查：
1. AWS 凭证是否有效（`aws sts get-caller-identity`）
2. IAM 权限是否包含 `s3vectors:*`
3. Region 是否支持 S3 Vectors
4. 向量桶名称是否符合规范

## API 参考

详细 API 参数定义见 `references/api_reference.md`。
