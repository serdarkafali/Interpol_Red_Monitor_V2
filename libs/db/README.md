# interpol-db

Interpol Monitor veritabanının şeması: SQLAlchemy modelleri ve Alembic migration'ları.
Kararların gerekçesi için bkz. [ADR 0004](../../docs/adr/0004-database-schema.md).

Bu pakette yalnızca tablo tanımları bulunur. Sorgular ve iş mantığı, veritabanını
kullanan servislerin kendi kodunda durur.

## Yerel veritabanı

Repo kökünde `.env.example` dosyasını `.env` olarak kopyalayıp şifreyi değiştirin, sonra:

```powershell
docker compose up -d        # PostgreSQL 18 + pgvector
docker compose ps           # STATUS sütununda "healthy" görünmeli
```

## Migration'lar

Komutlar `libs/db` klasöründen çalıştırılır. Bağlantı adresi `DATABASE_URL` ortam
değişkeninden ya da repo kökündeki `.env` dosyasından okunur.

```powershell
cd libs/db
uv run alembic upgrade head                          # tüm migration'ları uygula
uv run alembic current                               # veritabanı hangi revizyonda
uv run alembic revision --autogenerate -m "açıklama"  # modellerden yeni migration üret
uv run alembic downgrade -1                          # son migration'ı geri al
```

Otomatik üretilen migration'lar her zaman elle okunup kontrol edilmelidir; Alembic
trigger, CHECK kısıtı ve veri taşıma gibi değişiklikleri her zaman doğru algılamaz.
