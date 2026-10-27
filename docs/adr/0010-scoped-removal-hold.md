# ADR 0010: Eksik okunan bölgede yalnızca o bölgenin kaldırmalarını durdurmak

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-27
- **Karar verenler:** Serdar Kafalı
- **İlişkili:** ADR 0006'daki "eksik tarama hiçbir şeyi kaldırmaz" kuralını daraltır

## Bağlam

ADR 0006'ya göre bir kayıt, art arda iki **tamamlanmış** taramada görülmezse kaldırılıyor;
tek bir grup bile okunamazsa tarama eksik sayılıyor ve hiçbir kayıt kaldırılmıyor.

ADR 0009'un üçüncü doğrulama dry-run'ı (2026-10-27) bu kuralın bir sınırını gösterdi:
`M, RU çıkaran, 37` grubunda 167 kayıt var, ancak soyadında Latin harfi olan herkes
okunduğu halde yalnızca 166'sı görülüyor. Kalan kayıt hiçbir liste sorgusunda dönmüyor
(Latin harfi olmayan bir soyadı ya da Interpol'ün toplamında sayılıp listede gösterilmeyen
bir kayıt). Kural değişmezse her tarama eksik bitecek ve listeden kalkan **hiçbir** kayıt,
dünyanın neresinde olursa olsun, kaldırılamayacak.

Kural gevşetilirken korunması gereken şey şu: listede duran ama bir tarama tarafından
görülemeyen bir kayıt kaldırılmamalı.

## Karar

**Bölge.** Collector her kimliği, onu döndüren sorgunun cinsiyeti ve yakalama kararını
çıkaran ülkesiyle etiketliyor: `M|RU`, `F|TR`. Daha genel bir sorguda görülen kimlik daha
genel bir bölge alıyor (`U|*`: yalnızca cinsiyete göre okunan küçük bir grup, `*|*`: bütün
liste). Bir kimlik birden fazla bölgede görülebiliyor (birden fazla ülkenin kararı).

**Eksik bölgeler.** Tarayıcı, tam okuyamadığı her grubun bölgesini raporluyor: kapsanamayan
bir tek yaş grubu (`M|RU`), başarısız bir sorgu (`F|*` gibi). Liste hiç okunamazsa ya da
tarama kapanış için yarıda kesilirse bölge `*|*` oluyor.

**Kaldırma kararı:**

- `*|*` eksikse eski kural geçerli: hiçbir şey değişmiyor.
- Görülmeyen bir kimlik, son görüldüğü bölgelerden biri eksik bir bölgeyle örtüşüyorsa
  **bekletiliyor** (`held_back`): kaldırılmıyor, "bir kez görülmedi" işareti değişmiyor,
  bölgesi korunuyor. Bölgesi bilinmeyen bir kimlik her yerde olabilir sayılıyor.
- Diğer görülmeyen kimlikler ADR 0006'daki iki tarama kuralıyla kaldırılıyor; %5 freni
  aynen geçerli.
- Bölge tekrar tam okunduğunda bekletilen kimlikler normal kurala dönüyor.

Örtüşme kuralı: `*` her değerle örtüşüyor, diğer değerler yalnızca kendileriyle. `M|*`
eksikse bütün erkeklerin kayıtları bekletiliyor; `M|RU` eksikse yalnızca erkek ve RU
kararlı olanlar.

**Neden güvenli?** Tam okunan bir grubun bütün kayıtları görülmüştür. Listede durup
görülmeyen bir kayıt ancak tam okunamayan bir grupta olabilir; o grubun bölgesindeki her
kayıt bekletildiği için yanlış kaldırma olmaz.

**Durum dosyası** sürüm 2: her kimlik için bölge listesi eklendi. Sürüm 1 dosyası
okunabiliyor; kimliklerin bölgesi bilinmiyor sayılıyor (güvenli taraf).

Bekletme `removals_held_back` uyarısıyla (sayı ve eksik bölgeler) loglanıyor; hata değil,
beklenen bir durum. Faz 7'deki alarm, bir bölgenin ne kadar süredir eksik olduğunu izleyecek.

## Değerlendirilen alternatifler

**Hiçbir şey yapmamak.** İlkeye uygun, ancak tek bir okunamayan kayıt yüzünden bütün
kaldırmalar süresiz durur. Reddettik.

**Eksik grupta bir kayıtlık farka göz yummak.** Basit, ancak okunamayan kayıt daha önce
görülmüşse (grup 160'ın altındayken görüldü) iki tarama sonra yanlışlıkla kaldırılır.
Reddettik.

**Kayıtları detaydaki doğum tarihi ve uyruğa göre korumak** (ADR 0009'da reddedilen
"güvenlik ağı"). Daha dar bir bölge seçer, ancak collector'ın kişisel bilgi saklamasını
gerektirir. Bölge yalnızca sorgu filtrelerimizden (cinsiyet, kararı çıkaran ülke) geliyor;
detay verisi gerekmiyor. Reddettik.

**Kayıp kaydı bulup incelemek.** Nedenini kesinleştirir ama sorunu çözmez ve kişisel
veriye bakmayı gerektirir. Reddettik.

## Sonuçlar

Okunamayan bir grup artık yalnızca kendi bölgesindeki kaldırmaları bekletiyor; tarama,
yeni kayıtların bulunması, detay çekme ve diğer bölgelerdeki kaldırmalar normal sürüyor.
Bugün bekletilen bölge `M|RU` (yaklaşık 2700 kayıt); bu bölgede listeden kalkan bir kayıt,
bölge tekrar tam okunana kadar veritabanında kalacak.

Buna karşılık bazı bedelleri kabul ediyoruz. Collector'ın durum dosyası artık kimlik
başına cinsiyet ve kararı çıkaran ülke kodunu tutuyor. Bir kaydın kararını çıkaran ülke
değişir (örneğin DE'nin kararı kalkıp RU'nunki eklenir) ve kayıt aynı anda tam okunamayan
gruba düşerse, eski bölgesine göre yanlışlıkla kaldırılabilir; bu ihtimal çok küçük ve en
kötü durumda tek bir kaydın kaldırılması demek (kayıt soft delete edilir). Sürüm 1 durum
dosyasından gelen ve son tamamlanmış taramadan beri görülmeyen birkaç kimliğin bölgesi
bilinmediği için, herhangi bir bölge eksik kaldıkça bunlar bekletilecek.

## Doğrulama

Gerçek API'ye karşı dry-run (2026-10-27), sürüm 1 durum dosyasıyla başlayarak:

| Ölçüm | Sonuç |
| --- | --- |
| Tarama | 659 istek; `incomplete_regions` yalnızca `["M|RU"]` |
| Görülen / toplam | 6359 / 6381 |
| Kaldırma kararı | `held_back` 1, `removed` 0, `blocked` 0; durum kaydedildi (`list_scan_processed`) |
| Ara bağlantı kesintileri (2 kez, birkaç dakika) | İlk yeniden denemede geçti; hiçbir sorgu kaybolmadı |

Bekletilen tek kimlik son tam taramada (2026-10-26) görülmüş, bu taramada görülmemiş. Büyük
ihtimalle artık hiçbir sorguda dönmeyen kayıt; sürüm 1 dosyasından geldiği için bölgesi
bilinmiyordu ve kesin söylenemiyor. Öyleyse "bir kayıtlık farka göz yummak" seçilseydi iki
tarama sonra yanlışlıkla kaldırılacaktı.
