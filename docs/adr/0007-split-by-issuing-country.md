# ADR 0007: Liste sorgularını yakalama kararını çıkaran ülkeye göre bölmek

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-25
- **Karar verenler:** Serdar Kafalı
- **İlişkili:** ADR 0006'nın "Sorguların bölünmesi" bölümünü değiştirir

## Bağlam

Collector'ın gerçek API'ye karşı ilk dry-run'ı (2026-10-25) ADR 0006'nın iki riskini
gerçek bir sorun olarak gösterdi:

| Ölçüm | Sonuç |
| --- | --- |
| Tam bir liste taraması | 574 istek, yaklaşık 65 dakika; 72 istek yaş bölmesi için |
| Görülen / Interpol'ün toplamı | 6269 / 6378; 109 kayıt hiç görülmedi |
| Bölünemeyen grup | `sexId=M, nationality=RU`, yaş 37: 162 kayıt. Tek yaşa inildiği için bölünemiyor; her tarama eksik bitiyor, `notice.removed` hiç gönderilemiyor |
| Toplam farkı uyarıları | `M/IN` 218 → 215, `M/PK` 171 → 170: doğum tarihi olmayan kayıtlar (beklenen) |

Ardından `scripts/probe_interpol_gaps.py` ile ölçüldü:

| Ölçüm | Sonuç |
| --- | --- |
| `arrestWarrantCountryId=RU` | 3135 (toplam 6378): filtre çalışıyor |
| Var olmayan uyruk kodu `QQ` | 0: geçersiz değer yok sayılmıyor, boş sonuç dönüyor |
| ISO dışı uyruk kodları (`XK`, `UN`, `XX`, `ZZ`, pycountry'deki eski ülke kodları: `YU`, `SU`, `CS`, ...) | Hepsi 0 |
| `M, RU, 37` yakalama kararını çıkaran ülkeye göre | RU 160, DE 2; toplam 162 |

Görülmeyen kayıtlar ISO dışı bir uyruk koduyla açıklanamıyor. Kalan en olası açıklama,
uyruğu hiç girilmemiş kayıtlar: bunlar hiçbir uyruk sorgusunda görünmüyor. Bunu doğrudan
ölçebilecek bir filtre yok.

## Karar

Bölme sırası şu hale geliyor:

1. **Cinsiyet:** `M`, `F`, `U` (değişmedi).
2. **Yakalama kararını çıkaran ülke** (`arrestWarrantCountryId`): ISO 3166-1 listesindeki
   her ülke kodu. Uyruğun yerini alıyor.
3. **Yaş:** 0–120 aralığı ikiye bölünerek (değişmedi).
4. **Uyruk:** yalnızca tek yaşa inildiği halde 160'ı aşan bir grup kalırsa.

Kırmızı bültenin dayanağı bir yakalama kararı olduğu için her kayıtta düzenleyen bir ülke
bekliyoruz; uyruğu girilmemiş kayıtlar da böylece bir sorguda görünecek. Birden fazla
ülkenin kararı olan kayıtlar birden fazla grupta görünüyor; uyrukta olduğu gibi her kimlik
bir kez sayılıyor ve bu bölmede toplam kontrolü yapılmıyor.

`M, RU, 37` grubu ikinci adımda "RU'nun çıkardığı kararlar" grubuna giriyor, orada yaşa
bölünüyor ve tek yaşta 160'ı aşarsa uyruğa bölünüyor. Dördüncü adım tükenirse tarama
ADR 0006'daki gibi eksik sayılıyor.

## Değerlendirilen alternatifler

**Uyruk ikinci adımda kalsın, düzenleyen ülke yalnızca dördüncü adıma eklensin.** RU/37
sorununu çözerdi ve istek sayısı aynı kalırdı. Ancak uyruğu girilmemiş kayıtlar görünmemeye
devam ederdi. Reddettik.

**Hem uyruğa hem düzenleyen ülkeye göre iki ayrı tarama.** Her iki boşluğu da kapatırdı,
ancak istek sayısını ikiye katlardı (taramada yaklaşık 1100 istek, iki saat). Reddettik;
ölçümler yeni sırada da kayıp gösterirse yeniden değerlendirilecek.

**İsim filtreleriyle (`name`, `forename`) bölmek.** API'nin bu filtreleri tam eşleşme mi,
içerme mi, benzerlik mi diye uyguladığını bilmiyoruz; bir bölmenin bütün kayıtları
kapsadığını kanıtlayamayız. Reddettik.

**Bölünemeyen grupları tamlıktan muaf tutmak.** Tarama tam sayılır, yalnızca o grupta son
görülen kayıtlar kaldırılmazdı. Kayıtların hangi grupta görüldüğünü saklamayı ve yaş
değiştikçe grup değiştiren kayıtları izlemeyi gerektirir; yanlış silme riski taşır.
Gerekirse sonraki adım olarak düşünülecek.

## Sonuçlar

Şu an bilinen bölünemeyen grup kalkıyor ve uyruğu girilmemiş kayıtlar görünür hale
geliyor. Bu, yeni sırayla yapılacak ikinci dry-run'da `seen` ile `reported_total`
karşılaştırılarak doğrulanacak.

Buna karşılık bazı bedelleri kabul ediyoruz. Dördüncü adım her bölünemeyen grup için
yaklaşık 250 istek (yarım saat) ekliyor. `M, RU, 37` içindeki RU uyruklular tam 160; bir kayıt
daha eklenirse grup yine bölünemez hale gelir ve tarama eksik biter. Bu durumda hata
loglanıyor ve Faz 7'de alarm olacak. Düzenleyen ülkesi girilmemiş bir kayıt varsa artık o
görünmeyecek; bunu doğrudan ölçemiyoruz, `seen` ile `reported_total` farkı izlenecek.

## Doğrulama

Yeni sırayla yapılan ikinci dry-run (2026-10-25/26) beklentiyi doğruladı:

| Ölçüm | İlk dry-run (uyruk) | İkinci dry-run (düzenleyen ülke) |
| --- | --- | --- |
| Tarama tam mı? | Hayır (`M, RU, 37` bölünemedi) | **Evet** |
| Görülen / toplam | 6269 / 6378 (109 eksik) | **6357 / 6378 (21 eksik)** |
| Bölünemeyen grup | 1 | 0 |
| İstek sayısı | 574 | 831 |
| Yaş bölmesi istekleri | 72 | 329 |
| Süre | yaklaşık 65 dakika | yaklaşık 90 dakika |

Görülmeyen kayıtların 88'i artık görünüyor; uyruğu girilmemiş kayıt hipotezini destekliyor.
Kalan 21 kaydın bir kısmı doğum tarihi olmayan kayıtlar (`M, IN` kararları 282 → 278);
fark metrik olarak izlenmeye devam ediyor.

Bedel beklenenden büyük çıktı: kararı çıkaran ülke grupları uyruk gruplarından daha büyük
(RU'nun kararları 3135), bu yüzden yaş bölmesi istekleri 72'den 329'a çıktı ve tarama 25
dakika uzadı. Tarama süresi 6 saatlik aralığın dörtte birine yaklaştığı için ADR 0006'da
ertelenen warm start (önceki taramanın yaş sınırlarıyla başlamak) artık değerlendirilmeye
değer: yaş bölmesi isteklerinin önemli bir kısmını kaldırabilir.
