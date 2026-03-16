# Amazon S3 Vectors API 参考

> 官方文档: https://docs.aws.amazon.com/AmazonS3/latest/API/API_Operations_Amazon_S3_Vectors.html
> boto3 文档: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3vectors.html
> 产品主页: https://aws.amazon.com/s3/features/vectors/
> GA 博客: https://aws.amazon.com/blogs/aws/amazon-s3-vectors-now-generally-available-with-increased-scale-and-performance/

---

## boto3 客户端初始化

```python
import boto3

# 方式 1：使用实例 IAM Role（推荐，EC2/EKS 上自动生效）
client = boto3.client('s3vectors', region_name='ap-northeast-1')

# 方式 2：使用 AWS Profile
session = boto3.Session(profile_name='my-profile')
client = session.client('s3vectors', region_name='ap-northeast-1')

# 方式 3：显式指定凭证（不推荐，建议使用 IAM Role）
client = boto3.client(
    's3vectors',
    region_name='ap-northeast-1',
    aws_access_key_id='AKIA...',
    aws_secret_access_key='...',
)
```

---

## create_vector_bucket

创建一个 S3 向量桶，用于专门存储和检索向量数据。

### 方法原型

```python
response = client.create_vector_bucket(
    vectorBucketName='string',
    encryptionConfiguration={        # 可选
        'sseType': 'SSE-S3' | 'SSE-KMS',
        'kmsKeyArn': 'string'         # SSE-KMS 时需要
    }
)
```

### 请求参数

| 参数名称 | 描述 | 类型 | 是否必选 |
|----------|------|------|----------|
| vectorBucketName | 向量桶名称，仅支持小写字母、数字和连字符 `-`，长度 3-63 字符，全局唯一 | String | 是 |
| encryptionConfiguration | 加密配置，`sseType` 支持 `SSE-S3` 和 `SSE-KMS` | Dict | 否 |

### 返回值

```python
{
    'ResponseMetadata': {'RequestId': 'xxx', 'HTTPStatusCode': 200, ...}
}
```

### 示例

```python
# 创建普通向量桶
client.create_vector_bucket(vectorBucketName='my-skill-vectors')

# 创建 SSE-S3 加密向量桶
client.create_vector_bucket(
    vectorBucketName='my-skill-vectors',
    encryptionConfiguration={'sseType': 'SSE-S3'}
)
```

---

## delete_vector_bucket

删除向量桶（需先删除所有索引）。

```python
response = client.delete_vector_bucket(vectorBucketName='string')
```

---

## get_vector_bucket

获取向量桶详细信息。

```python
response = client.get_vector_bucket(vectorBucketName='string')
# response 包含: vectorBucketName, vectorBucketArn, creationTime, encryptionConfiguration, ...
```

---

## list_vector_buckets

列出当前账号在指定 Region 的所有向量桶。

```python
response = client.list_vector_buckets(
    maxResults=10,          # 可选
    nextToken='string',     # 可选，分页
    prefix='my-',           # 可选，前缀过滤
)
# response 包含: vectorBuckets (list), nextToken
```

---

## put_vector_bucket_policy / get_vector_bucket_policy / delete_vector_bucket_policy

管理向量桶的资源策略（类似 S3 Bucket Policy）。

```python
# 设置策略
client.put_vector_bucket_policy(
    vectorBucketName='string',
    policy='{"Statement":[...]}',   # JSON 字符串
)

# 获取策略
response = client.get_vector_bucket_policy(vectorBucketName='string')
# response['policy'] 为策略 JSON 字符串

# 删除策略
client.delete_vector_bucket_policy(vectorBucketName='string')
```

---

## create_index

在向量桶内创建向量索引。

### 方法原型

```python
response = client.create_index(
    vectorBucketName='string',
    indexName='string',
    dataType='float32',
    dimension=1024,
    distanceMetric='cosine' | 'euclidean',
    metadataConfiguration={         # 可选
        'nonFilterableMetadataKeys': ['key1', 'key2']
    }
)
```

### 请求参数

| 参数名称 | 描述 | 类型 | 是否必选 |
|----------|------|------|----------|
| vectorBucketName | 向量桶名称 | String | 是 |
| indexName | 索引名称 | String | 是 |
| dataType | 向量数据类型，当前支持 `float32` | String | 是 |
| dimension | 向量维度，范围 1-4096 | Integer | 是 |
| distanceMetric | 距离度量，`cosine` 或 `euclidean` | String | 是 |
| metadataConfiguration | 元数据配置，指定不参与过滤的 key | Dict | 否 |

### 维度推荐

| Embedding 模型 | 维度 | 推荐场景 |
|----------------|------|----------|
| Bedrock Titan Embeddings v2 | 1024 | 通用语义搜索（推荐） |
| Bedrock Titan Embeddings v1 | 1536 | 旧版兼容 |
| OpenAI text-embedding-3-small | 1536 | 跨云场景 |
| OpenAI text-embedding-ada-002 | 1536 | 旧版兼容 |

---

## get_index / list_indexes / delete_index

```python
# 获取索引信息
response = client.get_index(vectorBucketName='string', indexName='string')

# 列出所有索引
response = client.list_indexes(
    vectorBucketName='string',
    maxResults=10,
    nextToken='string',
    prefix='demo-',
)

# 删除索引
client.delete_index(vectorBucketName='string', indexName='string')
```

---

## put_vectors

向索引中插入或更新向量数据（Upsert 语义，key 相同则覆盖）。

### 方法原型

```python
response = client.put_vectors(
    vectorBucketName='string',
    indexName='string',
    vectors=[
        {
            'key': 'string',           # 必需，唯一标识
            'data': {
                'float32': [1.0, 2.0, ...]   # 必需，向量数据
            },
            'metadata': {              # 可选，任意 key-value
                'title': 'Document Title',
                'category': 'AI',
                'score': 0.95,
            }
        }
    ]
)
```

### 批量限制

- 单次 `put_vectors` 最多 500 条
- 大批量时分批调用

---

## get_vectors

按 key 获取指定向量。

```python
response = client.get_vectors(
    vectorBucketName='string',
    indexName='string',
    keys=['key1', 'key2'],
    returnData=True,        # 可选，返回向量数据
    returnMetadata=True,    # 可选，返回元数据
)
# response['vectors'] 为向量列表
```

---

## list_vectors

列出索引中的向量，支持分页和并行分段遍历。

```python
response = client.list_vectors(
    vectorBucketName='string',
    indexName='string',
    maxResults=100,         # 可选
    nextToken='string',     # 可选，分页
    returnData=False,       # 可选
    returnMetadata=True,    # 可选
    segmentCount=4,         # 可选，并行遍历总分段数
    segmentIndex=0,         # 可选，当前分段索引（0-based）
)
```

---

## delete_vectors

删除指定 key 的向量。

```python
response = client.delete_vectors(
    vectorBucketName='string',
    indexName='string',
    keys=['key1', 'key2'],
)
```

---

## query_vectors

向量相似度搜索（最核心的操作）。

### 方法原型

```python
response = client.query_vectors(
    vectorBucketName='string',
    indexName='string',
    queryVector={
        'float32': [0.1, 0.2, ...]   # 查询向量，维度需与索引一致
    },
    topK=5,                          # 返回最相似的 K 个结果
    filter={                         # 可选，元数据过滤
        'category': {'$eq': 'AI'}
    },
    returnMetadata=True,             # 可选，返回元数据
)
```

### 返回值

```python
{
    'vectors': [
        {
            'key': 'doc-001',
            'distance': 0.123,       # 距离/相似度值
            'metadata': {'title': '...', 'category': '...'}
        },
        ...
    ],
    'ResponseMetadata': {...}
}
```

### 过滤条件语法

```python
# 精确匹配
filter = {'category': {'$eq': 'AI'}}

# 不等于
filter = {'status': {'$ne': 'deleted'}}

# 数值比较
filter = {'score': {'$gte': 0.8}}

# IN 查询
filter = {'category': {'$in': ['AI', 'ML']}}

# 复合条件
filter = {
    '$and': [
        {'category': {'$eq': 'AI'}},
        {'score': {'$gte': 0.8}}
    ]
}
```

---

## IAM 权限参考

最小权限策略（用于访问 S3 Vectors）：

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3vectors:CreateVectorBucket",
                "s3vectors:DeleteVectorBucket",
                "s3vectors:GetVectorBucket",
                "s3vectors:ListVectorBuckets",
                "s3vectors:PutVectorBucketPolicy",
                "s3vectors:GetVectorBucketPolicy",
                "s3vectors:DeleteVectorBucketPolicy",
                "s3vectors:CreateIndex",
                "s3vectors:GetIndex",
                "s3vectors:ListIndexes",
                "s3vectors:DeleteIndex",
                "s3vectors:PutVectors",
                "s3vectors:GetVectors",
                "s3vectors:ListVectors",
                "s3vectors:DeleteVectors",
                "s3vectors:QueryVectors"
            ],
            "Resource": "*"
        }
    ]
}
```

---

## 错误代码参考

| 错误代码 | 说明 | 处理建议 |
|----------|------|----------|
| `VectorBucketAlreadyExists` | 向量桶已存在 | 检查桶名或直接使用已有桶 |
| `VectorBucketNotFound` | 向量桶不存在 | 确认桶名和 Region |
| `IndexAlreadyExists` | 索引已存在 | 直接使用或先删除 |
| `IndexNotFound` | 索引不存在 | 先创建索引 |
| `DimensionMismatch` | 向量维度与索引不匹配 | 检查 Embedding 模型和索引 dimension |
| `AccessDenied` | 无权限 | 检查 IAM Role 权限 |
| `InvalidVectorBucketName` | 桶名不合法 | 使用小写字母、数字、连字符 |
