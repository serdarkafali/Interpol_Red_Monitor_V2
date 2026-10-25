# interpol-collector

Interpol'ün herkese açık Red Notice API'sindeki listeyi tarayan ve kayıtların
detaylarını çeken, bunları Kafka'ya olay olarak yayınlayan servis (ADR 0005, ADR 0006).

## Ne yapar?

- **Liste taraması** (varsayılan 6 saatte bir): bütün `entity_id`'leri toplar, her biri
  için `notice.seen`, art arda iki tam taramada görülmeyenler için `notice.removed`
  gönderir.
- **Detay çekme** (taramalar arasında sürekli): önce yeni kayıtların, sonra en uzun
  süredir denenmemiş kayıtların detayını ve fotoğraf listesini çeker, `notice.observed`
  gönderir.

Durum, taramalar arasında `COLLECTOR_STATE_DIR` klasöründeki iki JSON dosyasında tutulur:
`removal_state.json` ve `detail_schedule.json`.

## Çalıştırma

Repo kökünden; ayarlar `.env` dosyasından okunur:

    uv run --env-file .env --package interpol-collector interpol-collector

Varsayılan olarak **dry-run** modunda çalışır: Kafka'ya hiçbir şey yazmaz, yalnızca kaç
olay göndereceğini loglar. Gerçek veriyi Kafka'ya yazmak için `COLLECTOR_DRY_RUN=false`
gerekir. Durdurmak için `Ctrl+C`; servis o anki adımı bitirip durumu kaydeder.

## Yapılandırma

Tüm ayarlar `COLLECTOR_` önekli ortam değişkenleriyle verilir; servis kendisi dosya
okumaz. Liste ve açıklamalar için kökteki `.env.example` dosyasına bakın.
