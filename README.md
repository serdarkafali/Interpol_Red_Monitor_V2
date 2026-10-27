# Interpol Red Notice Monitor

Interpol'ün herkese açık Red Notice API'sindeki kayıtları düzenli olarak toplayan,
PostgreSQL'de saklayan ve ileride fotoğraflar üzerinden yüz benzerliği araması yapacak
servislerden oluşan bir sistem.

> **Durum:** Geliştirme aşamasında. Altyapı (Faz 0), veritabanı şeması (Faz 1),
> collector (Faz 2) ve ingestor (Faz 3) hazır; sırada face-worker ve MinIO (Faz 4) var.

## Mimari

```text
Interpol API ──► collector ──► Kafka ──► ingestor ──► PostgreSQL ◄── api
                                  │
                                  └────► face-worker ──► MinIO (fotoğraflar)

Tüm servisler ──► stdout (JSON log) ──► Filebeat ──► Logstash ──► Elasticsearch
```

Kararların gerekçeleri [`docs/adr`](docs/adr) altındaki ADR'lerde:

| ADR | Konu |
| --- | --- |
| [0001](docs/adr/0001-monorepo-structure.md) | Monorepo yapısı ve uv workspace |
| [0002](docs/adr/0002-kafka-message-broker.md) | Servisler arası mesajlaşma için Kafka |
| [0003](docs/adr/0003-structured-logging.md) | structlog ile yapılandırılmış loglama |
| [0004](docs/adr/0004-database-schema.md) | PostgreSQL şeması ve ortak `libs/db` paketi |
| [0005](docs/adr/0005-kafka-topics-and-message-contract.md) | Kafka topic'leri ve mesaj sözleşmesi |
| [0006](docs/adr/0006-collector-scan-strategy.md) | Collector tarama stratejisi |
| [0007](docs/adr/0007-split-by-issuing-country.md) | Sorguları yakalama kararını çıkaran ülkeye göre bölmek |
| [0008](docs/adr/0008-ingestor-design.md) | Ingestor'ın olayları veritabanına yazması |
| [0009](docs/adr/0009-split-by-name-letters.md) | Bölünemeyen grupları soyadı parçalarıyla kapsamak |

## Repo yapısı

```text
libs/
  contracts/       Servisler arasındaki Kafka mesaj şemaları
  db/              Veritabanı şeması: SQLAlchemy modelleri ve Alembic migration'ları
  observability/   Ortak loglama altyapısı
services/
  collector/       Interpol API'sinden kayıtları toplayan servis
  ingestor/        Kafka'daki olayları PostgreSQL'e yazan servis
scripts/           Tek seferlik araçlar (API örnekleme ve analiz)
docs/adr/          Mimari karar kayıtları
```

## Gereksinimler

- [uv](https://docs.astral.sh/uv/) (Python sürümünü ve bağımlılıkları o yönetir; Python
  3.12 ayrıca kurulmak zorunda değil)
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (yerel veritabanı ve
  veritabanı testleri için)

## Kurulum

Komutlar repo kökünden çalıştırılır.

```powershell
git clone https://github.com/serdarkafali/Interpol_Red_Monitor_V2.git
cd Interpol_Red_Monitor_V2

uv sync --all-packages             # sanal ortam ve tüm bağımlılıklar
uv run pre-commit install          # commit öncesi kontroller

Copy-Item .env.example .env        # sonra .env içindeki şifreleri değiştirin
docker compose up -d --build       # PostgreSQL 18 + pgvector, Kafka, migrate, ingestor
```

`docker compose up` migration'ları ayrı bir `migrate` adımında uygular. Docker dışında
uygulamak için: `cd libs/db; uv run alembic upgrade head; cd ../..`

Veritabanı ve migration'larla ilgili ayrıntılar: [`libs/db/README.md`](libs/db/README.md).


Collector'ı çalıştırmak (varsayılan olarak dry-run: Kafka'ya bir şey yazmaz):

```powershell
uv run --env-file .env --package interpol-collector interpol-collector
# ya da Docker ile:
docker compose --profile collector up -d --build collector
```

Ayrıntılar: [`services/collector/README.md`](services/collector/README.md).

Ingestor, Kafka'daki olayları veritabanına yazar ve `docker compose up` ile başlar.
Uçtan uca denemek için Kafka'ya uydurma olaylar yazılabilir (gerçek veri içermez):

```powershell
uv run --env-file .env python scripts/produce_fake_events.py
docker compose logs ingestor
```

Ayrıntılar: [`services/ingestor/README.md`](services/ingestor/README.md).

## Kontroller

CI her pull request'te aynı komutları çalıştırır:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest                      # veritabanı testleri için Docker açık olmalı
```

## Katkı kuralları

- `main`'e doğrudan push edilmez; her değişiklik bir branch ve pull request ile gelir.
- Commit ve PR başlıkları [Conventional Commits](https://www.conventionalcommits.org/)
  biçimindedir (`feat(db): ...`, `fix: ...`, `docs(adr): ...`). PR'lar squash ile
  birleştirilir.
- Kod, docstring ve log mesajları İngilizce; dokümanlar ve ADR'ler Türkçe yazılır.
- Mimariyi etkileyen her karar için yeni bir ADR yazılır.

## Kişisel veri

Red Notice kayıtları gerçek kişilere ait kişisel verilerdir. API'den indirilen örnekler
(`samples/`) ve `.env` dosyası git'e girmez. Loglara isim veya serbest metin yazılmaz;
kayıtlar yalnızca `entity_id` ile anılır.
