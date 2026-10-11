# interpol-collector

Interpol'ün herkese açık Red Notice API'sinden kayıtları ve fotoğrafları toplayan,
bunları Kafka'ya yayınlayan servis.

## Çalıştırma

    uv run --package interpol-collector interpol-collector

## Yapılandırma

Tüm ayarlar `COLLECTOR_` önekli ortam değişkenleriyle verilir.
Liste ve açıklamalar için kökteki `.env.example` dosyasına bakın.
