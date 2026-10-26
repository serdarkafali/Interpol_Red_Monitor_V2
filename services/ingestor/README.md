# interpol-ingestor

Kafka'daki `interpol.notices.v1` topic'inden notice olaylarını okuyup PostgreSQL'e yazan
servis (ADR 0008).

## Ne yapar?

| Olay | Veritabanında |
| --- | --- |
| `notice.observed` | Kaydı ekler ya da günceller; uyrukları, tutuklama emirlerini ve fotoğraf listesini yazar. `content_hash` değişmediyse hiçbir şey yazmaz |
| `notice.seen` | `last_seen_at`'i günceller; kaldırılmış bir kaydı geri getirir |
| `notice.removed` | Kaydı soft delete eder (`deleted_at`) |

Interpol'ün değerleri yazılmadan önce şemanın tiplerine çevrilir (`normalize.py`).
İşlenemeyen mesajlar `ingestor.dlq` topic'ine yazılır; veritabanına erişilemediğinde ise
mesaj DLQ'ya gitmez, beklenip yeniden denenir.
