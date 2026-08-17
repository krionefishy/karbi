# S3 mutations through Kafka

- Application and domain code may read from S3 and create presigned GET URLs.
- Every S3 `PUT` or `DELETE` is published as a Kafka event and handled by a storage subscriber.
- Topic names follow `storage.s3.<domain>.<action>` and are declared in one topic registry.
- Upload and delete are separate topics and separate consumers.
- Kafka events never contain credentials, access tokens, or encryption keys.
- `content_base64` is only for small payloads. Large exports use staging or a presigned upload workflow.
- Consumers commit offsets only after successful S3 mutation; handlers must therefore be idempotent.
- Broker topic auto-creation is disabled. On application startup, Kafka Admin creates missing topics
  from the Python registry in `shared/kafka_streams/topics.py`.
