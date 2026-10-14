# interpol-observability

Tüm servislerin ortak kullandığı yapılandırılmış loglama altyapısı (ileride metrikler de).
Her servis aynı formatta (JSON, UTC zaman damgası, `service` alanı) log basar.

## Kullanım

`configure_logging()` her serviste `main()` fonksiyonunun en başında **bir kez** çağrılır;
modüller logger'ı `get_logger(__name__)` ile alır. Olay adları sabit `snake_case` isimlerdir
(`notice_fetched`), değişken bilgiler ayrı alanlar olarak verilir. Kişisel veri loglanmaz.

## Örnek Kullanım
from interpol_observability import configure_logging, get_logger
configure_logging(service_name="collector", level="INFO", log_format="json")
logger = get_logger(__name__)
logger.info("notice_fetched", notice_id="2024/12345")

## Açıklama
json ve console modları ile stdout bilgisi yok. “Çıktı stdout’a gider; console modu
geliştirme içindir; toplama işini Docker ve log toplayıcı yapar”
