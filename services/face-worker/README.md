# interpol-face-worker

Red notice fotoğraflarını Interpol'den indirip S3 uyumlu nesne deposuna (SeaweedFS)
yazan servis (ADR 0011). Faz 5'te fotoğraflardan yüz benzerliği için gerekli verileri de
üretecek.

> **Durum:** Geliştirme aşamasında; servis henüz çalışmıyor.

## Ne yapar?

- İndirilecek fotoğrafları Kafka'dan değil, `notice_photos` tablosundan seçer:
  `downloaded_at` boş olan ve kaydı kaldırılmamış satırlar.
- Fotoğrafı indirir, `notices/{entity_id}/{picture_id}` adıyla depoya yazar, ardından
  satırın `object_key`, `sha256` ve `downloaded_at` sütunlarını doldurur.
- Başarısız bir indirmeyi artan aralıklarla en fazla 5 kez dener.
- Günde bir kez depoda veritabanında karşılığı kalmamış dosyaları siler.

Çalıştırma talimatları servis tamamlandığında eklenecek.
