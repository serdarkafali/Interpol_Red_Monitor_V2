# interpol-face-worker

Red notice fotoğraflarını Interpol'den indirip S3 uyumlu nesne deposuna (SeaweedFS)
yazan servis (ADR 0011). Faz 5'te fotoğraflardan yüz benzerliği için gerekli verileri de
üretecek.

## Ne yapar?

- İndirilecek fotoğrafları Kafka'dan değil, `notice_photos` tablosundan seçer:
  `downloaded_at` boş olan ve kaydı kaldırılmamış satırlar.
- Fotoğrafı indirir, `notices/{entity_id}/{picture_id}` adıyla depoya yazar, ardından
  satırın `object_key`, `sha256` ve `downloaded_at` sütunlarını doldurur.
- Başarısız bir indirmeyi artan aralıklarla en fazla 5 kez dener.
- Günde bir kez depoda veritabanında karşılığı kalmamış dosyaları siler.

## Çalıştırma

Face-worker gerçek Interpol API'sine istek attığı için `face-worker` profilinin
arkasındadır; düz bir `docker compose up` onu başlatmaz. Gerçek fotoğraf indirmek,
`COLLECTOR_DRY_RUN=false` ile birlikte ayrıca verilecek bir karardır (ADR 0011).

**Uçtan uca deneme (gerçek Interpol yok).** `docker-compose.e2e.yml`, uydurma bir gri
JPEG sunan sahte bir Interpol sunucusu (`scripts/fake_interpol_photos.py`) ekler ve
face-worker'ı ona yönlendirir:

```powershell
docker compose up -d --build
uv run --env-file .env python scripts/produce_fake_events.py
docker compose -f docker-compose.yml -f docker-compose.e2e.yml --profile face-worker up -d --build face-worker
docker compose logs face-worker
docker compose run --rm --entrypoint aws storage-init --endpoint-url http://seaweedfs:8333 s3 ls s3://notice-photos --recursive
```

Beklenen: `1900/1` depoya yazılır (`photo_stored`), `1900/2` `not_jpeg`, `1900/6`
`not_found` ile reddedilir, kaldırılmış `1900/3` hiç denenmez.

Durdurmak için:
`docker compose -f docker-compose.yml -f docker-compose.e2e.yml --profile face-worker stop face-worker fake-interpol`.
Servis o anki fotoğrafı bitirip kapanır.

## Yapılandırma

Tüm ayarlar `FACE_WORKER_` önekli ortam değişkenleriyle verilir; servis kendisi dosya
okumaz. Veritabanı adresi ve depo anahtarları ortak `DATABASE_URL` ve `STORAGE_*`
değişkenlerinden de okunur. Liste ve açıklamalar için kökteki `.env.example` dosyasına
bakın.

## Loglar

| Olay | Anlamı |
| --- | --- |
| `photo_stored` | Fotoğraf depoya yazıldı ve satırı güncellendi |
| `photo_download_failed` | İndirme başarısız; `reason` sabit bir kod, `attempts` deneme sayısı |
| `photo_download_given_up` | 5. başarısız deneme; fotoğraf bir daha denenmez |
| `photo_row_gone` | Satır indirme sırasında silindi; dosyayı günlük temizlik siler |
| `orphan_photos_deleted` | Günlük temizlik; `count` silinen dosya sayısı |
| `dependency_unavailable` | Depoya ya da veritabanına ulaşılamadı; deneme sayılmaz, beklenir |

Loglarda yalnızca `entity_id` ve `picture_id` yer alır; fotoğrafın kendisi asla loglanmaz.
