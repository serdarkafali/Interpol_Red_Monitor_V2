# interpol-face-worker

Red notice fotoğraflarını Interpol'den indirip S3 uyumlu nesne deposuna (SeaweedFS)
yazan (ADR 0011), ardından fotoğraflardaki yüzleri bulup birbiriyle karşılaştıran servis
(ADR 0012).

## Ne yapar?

- İndirilecek fotoğrafları Kafka'dan değil, `notice_photos` tablosundan seçer:
  `downloaded_at` boş olan ve kaydı kaldırılmamış satırlar.
- Fotoğrafı indirir, `notices/{entity_id}/{picture_id}` adıyla depoya yazar, ardından
  satırın `object_key`, `sha256` ve `downloaded_at` sütunlarını doldurur.
- Başarısız bir indirmeyi artan aralıklarla en fazla 5 kez dener.
- Günde bir kez depoda veritabanında karşılığı kalmamış dosyaları siler.
- İndirilmiş her fotoğrafı depodan okur, YuNet ile yüzleri bulur ve SFace ile her yüzü
  128 sayılık bir vektöre çevirir. Yüzler `faces` tablosuna yazılır, fotoğrafın
  `faces_processed_at` sütunu doldurulur.
- Her yeni yüzü, kaydın diğer yüzleriyle ve başka kayıtların yüzleriyle karşılaştırır;
  sonuçlar `face_matches` tablosuna yazılır. Eşik altındaki uzaklık bir **olası
  eşleşmedir**, kimlik tespiti değildir.

Bir adımda tek iş yapılır, bu sırayla: günlük temizlik, yüz analizi, indirme, bekleme.

## Çalıştırma

Face-worker gerçek Interpol API'sine istek attığı için `face-worker` profilinin
arkasındadır; düz bir `docker compose up` onu başlatmaz. Gerçek fotoğraf indirmek,
`COLLECTOR_DRY_RUN=false` ile birlikte ayrıca verilecek bir karardır (ADR 0011).

**Uçtan uca deneme (gerçek Interpol yok).** `docker-compose.e2e.yml`, sahte bir Interpol
sunucusu (`scripts/fake_interpol_photos.py`) ekler ve face-worker'ı ona yönlendirir.
Sunucu, `samples/e2e-faces` içinde hazırlanmış sentetik yüzleri, olmayanlar için de gri
bir JPEG'i sunar. Sentetik yüzler bir kez, DigiFace-1M zip'inden seçilir (ADR 0012):

```powershell
uv run python scripts/prepare_e2e_faces.py --dataset samples/faces/subjects_133333-166665_5_imgs.zip --models samples/models
docker compose up -d --build
uv run --env-file .env python scripts/produce_fake_events.py
docker compose -f docker-compose.yml -f docker-compose.e2e.yml --profile face-worker up -d --build face-worker
docker compose logs face-worker
docker compose run --rm --entrypoint aws storage-init --endpoint-url http://seaweedfs:8333 s3 ls s3://notice-photos --recursive
```

Beklenen: `1900/1` depoya yazılır (`photo_stored`), `1900/2` `not_jpeg`, `1900/6`
`not_found` ile reddedilir, kaldırılmış `1900/3` hiç denenmez. Yüz tarafında `1900/1`
için `faces: 0`, `1900/8` için `1900/7` ile iki `possible_match`, `1900/9` için bir
`notice_photo_mismatch` görülür. Sonuçlar veritabanında:

```powershell
docker compose exec postgres psql -U interpol -d interpol -c "select * from face_matches"
```

Durdurmak için:
`docker compose -f docker-compose.yml -f docker-compose.e2e.yml --profile face-worker stop face-worker fake-interpol`.
Servis o anki fotoğrafı bitirip kapanır.

## Yapılandırma

Tüm ayarlar `FACE_WORKER_` önekli ortam değişkenleriyle verilir; servis kendisi dosya
okumaz. Veritabanı adresi ve depo anahtarları ortak `DATABASE_URL` ve `STORAGE_*`
değişkenlerinden de okunur. Liste ve açıklamalar için kökteki `.env.example` dosyasına
bakın.

Yüz modelleri (YuNet ve SFace, OpenCV Zoo) Docker imajına build sırasında indirilir ve
SHA-256'ları kontrol edilir. Servisi Docker dışında `uv run` ile çalıştırırken
`FACE_WORKER_DETECTOR_MODEL_PATH` ve `FACE_WORKER_RECOGNIZER_MODEL_PATH` ile
`samples/models` içindeki dosyaları gösterin.

## Loglar

| Olay | Anlamı |
| --- | --- |
| `photo_stored` | Fotoğraf depoya yazıldı ve satırı güncellendi |
| `photo_download_failed` | İndirme başarısız; `reason` sabit bir kod, `attempts` deneme sayısı |
| `photo_download_given_up` | 5. başarısız deneme; fotoğraf bir daha denenmez |
| `photo_row_gone` | Satır indirme sırasında silindi; dosyayı günlük temizlik siler |
| `orphan_photos_deleted` | Günlük temizlik; `count` silinen dosya sayısı |
| `dependency_unavailable` | Depoya ya da veritabanına ulaşılamadı; deneme sayılmaz, beklenir |
| `faces_analysed` | Fotoğrafın yüzleri bulundu ve kaydedildi; `faces` yüz sayısı (0 olabilir) |
| `possible_match` | Yeni yüz başka bir kaydın yüzüne eşikten yakın; `distance` uzaklık |
| `notice_photo_mismatch` | Yeni yüz, kendi kaydının bir yüzüne benzemiyor |
| `face_analysis_failed` | Dosya resim olarak çözülemedi; yüzsüz sayıldı |
| `photo_file_missing` | Dosya depoda yok; yüzsüz sayıldı |

Loglarda yalnızca `entity_id`, `picture_id`, fotoğraf ve yüz id'leri ile uzaklık yer
alır; fotoğrafın kendisi ve kişi bilgileri asla loglanmaz.
