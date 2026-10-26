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

## Çalıştırma

Docker ile (PostgreSQL ve Kafka dahil). `migrate` adımı önce şemayı kurar, ingestor ondan
sonra başlar:

    docker compose up -d --build ingestor
    docker compose logs -f ingestor

Yerelde, repo kökünden; adresler `.env` dosyasındaki `DATABASE_URL` ve
`KAFKA_BOOTSTRAP_SERVERS` değişkenlerinden okunur:

    cd libs/db; uv run alembic upgrade head; cd ../..
    uv run --env-file .env --package interpol-ingestor interpol-ingestor

Durdurmak için `Ctrl+C` ya da `docker compose stop ingestor`; servis o anki mesajı
bitirip offset'ini commit eder.

## Yapılandırma

Tüm ayarlar `INGESTOR_` önekli ortam değişkenleriyle verilir; servis kendisi dosya
okumaz. Liste ve açıklamalar için kökteki `.env.example` dosyasına bakın.
