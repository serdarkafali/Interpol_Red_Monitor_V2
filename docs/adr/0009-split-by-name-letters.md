# ADR 0009: Tek yaşta bölünemeyen grupları soyadı parçalarıyla kapsamak

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-27
- **Karar verenler:** Serdar Kafalı
- **İlişkili:** ADR 0007'deki bölme sırasının dördüncü adımını (uyruk) değiştirir

## Bağlam

ADR 0007 ile tek yaşa inildiği halde 160'ı aşan bir grup son adımda uyruğa bölünüyordu.
Bu iki sorun bıraktı:

- `M`, `RU` tarafından çıkarılmış, yaş 37 grubunda RU uyruklular 160'tı: bir kişi daha
  eklendiğinde grup yeniden bölünemez hale gelecekti. 2026-10-26'da grup **161** oldu; her
  tarama yeniden eksik biter hale geldi ve kaldırma kararları durdu.
- Uyruk adımı bölünemeyen her grup için 249 istek, taramada yaklaşık 25 dakika ekliyordu.

Interpol'ün arama sayfasındaki `name` (soyadı) ve `forename` (ad) filtreleri iki probe ile
ölçüldü. Liste cevabındaki isimler yalnızca probe'un hafızasında karşılaştırıldı; ekrana ve
dosyaya isim yazılmadı.

`scripts/probe_interpol_name_filter.py` (2026-10-26), `M, RU çıkaran, 37, RU uyruklu`:

| Ölçüm | Sonuç |
| --- | --- |
| Tek harfin eşleşme kuralı | **İçerir** (`name=V`, soyadında V geçen herkes); "başlar" ve "bir kelimesi başlar" elendi. `forename` de aynı |
| 26 harfin kapsaması | Okunabilen 159 / 159; boş isim yok |
| Tek harfin küçültmesi | Zayıf: `name=V` 157, `name=A` 136, `forename=A` 149 |
| Filtrelerin birlikte kullanımı | "Ve" ile birleşiyor |

`scripts/probe_interpol_name_substrings.py` (2026-10-27):

| Ölçüm | Sonuç |
| --- | --- |
| İki ve üç harfli değerler (`OV`, `EV`, `AN`, `NOV`, `AEV`, ... 155 kişilik bir grupta 12 değer) | Hepsi **içerir** kuralına birebir uydu |
| `M, RU çıkaran, 37` (167): 26 harf | 166 kimlik |
| Aynı grup: `V`'nin 52 iki harfli uzantısı (`VA` ... `VZ`, `AV` ... `ZV`) | Hiçbiri 160'ı aşmadı; yine 166 |

İçerir kuralında bir kayıt birden fazla değerde görünür; değerlerin toplamları grubun
toplamını vermez, ADR 0006'daki toplam kontrolü burada kullanılamaz.

## Karar

Bölme sırası şu hale geliyor:

1. **Cinsiyet** (değişmedi).
2. **Yakalama kararını çıkaran ülke** (değişmedi).
3. **Yaş**, aralık ikiye bölünerek (değişmedi).
4. Tek yaşta hâlâ 160'ı aşan grup bölünmüyor, **soyadı parçalarıyla kapsanıyor**:
   - önce 26 harf (`name=A` ... `name=Z`),
   - 160'ı aşan bir değer iki yanından bir harf uzatılarak (`V` için `VA` ... `VZ` ve
     `AV` ... `ZV`), gerekirse dört harfe kadar.

Uyruk adımı kaldırılıyor.

**Uzatma neden herkesi kapsıyor?** `V` içeren ve en az iki harfli bir soyadında V'nin hemen
önünde ya da arkasında bir harf vardır; yani soyadı 52 uzantıdan en az birini içerir. Tek
istisna, değerin iki yanında da harf olmaması (örneğin tek harflik bir kelime); bu durumu
aşağıdaki kontrol yakalıyor.

**Tamlık, görülen kimliklerin sayısıyla kanıtlanıyor.** Kapsanan bir grup, altında
görülen farklı kimliklerin sayısı grubun toplamına ulaştığında tamam sayılıyor: grubun her
kaydı görülmüş demektir. Ulaşmazsa grup ve dolayısıyla tarama eksik sayılıyor
(`list_cover_incomplete` hatası) ve ADR 0006'daki gibi hiçbir kaldırma yapılmıyor.
Interpol filtrenin çalışma biçimini değiştirirse ya da hiçbir soyadı parçasının bulamadığı
bir kayıt çıkarsa kontrol başarısız olur ve sistem güvenli tarafta kalır.

**Gereksiz istek yapılmıyor.** Değerler sırayla sorgulanıyor: önce 26 harf, sonra 160'ı
aşanların uzantıları. 160'ı aşan değerlerin ilk 160 kimliği de grubun gerçek üyeleri
olduğu için sayıma katılıyor. Toplama ulaşıldığı anda sorgulama duruyor.

Soyadı sorguları `name_split_requests` sayacında sayılıyor; yaş bölmesi sayacı artık
yalnızca yaş bölmesini gösteriyor (ADR 0007'deki sayım karışıklığı).

## Değerlendirilen alternatifler

**Uyruk adımını korumak (ADR 0007).** Sorunu çözmüyor: grup 161 olduğunda tek uyrukta da
160'ı aşıyor ve her bölünemeyen grup için 249 istek ekliyor. Reddettik.

**Soyadı harflerinden sonra ad harfleri ve uyruk yedeği.** Denendi: iki dry-run'da ikisi de
kayıp kaydı bulamadı (aşağıda); uyruk yedeği her denemede 249 istek ekledi, ad harfleri adı
girilmemiş bir kaydı hiç bulamaz. Soyadı uzatması ikisini de kapsıyor ve herkesi kapsadığı
kanıtlanabiliyor. Reddettik.

**Bölünemeyen grubun kayıtlarını profillerine göre korumak** ("güvenlik ağı"). Hiçbir
filtreye bağlı değil; ancak collector'ın kişilerin doğum tarihi ve uyruğu gibi bilgilerini
saklamasını ve "eksik taramada hiçbir şey silinmez" kuralının gevşetilmesini gerektiriyor.
Şimdilik reddettik; bunun yerine Faz 7'de "bir grup kapsanamadı, kaldırmalar durdu" alarmı
kurulacak. Alarm sık çalarsa yeniden değerlendirilecek.

## Sonuçlar

Bölünemeyen bir grup çoğunlukla birkaç harf sorgusuyla, gerekirse birkaç düzine uzantıyla
okunacak; uyruk adımının 249 isteği gidiyor. Liste cevabındaki isimler okunmuyor; sorgulara
yalnızca harf dizileri gönderiliyor, kişisel veri açısından bir şey değişmiyor.

Buna karşılık bazı bedelleri kabul ediyoruz. Bu da mutlak bir garanti değil: soyadında
Latin harfi olmayan bir kayıt ya da Interpol'ün toplamında sayılıp hiçbir liste sorgusunda
dönmeyen bir kayıt bulunamaz; o zaman tarama eksik biter (güvenli taraf) ve Faz 7'deki
alarm devreye girer. Böyle bir grupta her tarama dört harfe kadar uzatmayı deneyip boşa
istek harcar. İçerir kuralı tek bir ülkenin isimlerinde ölçüldü; aksanlı ya da başka
alfabeden yazılmış isimlerdeki davranışı bilinmiyor, ancak kapsama kontrolü bunu
yakalıyor. Belgelenmemiş bir filtreye daha bağımlıyız.

ADR 0006 ve 0007'den kalan bir boşluk da not ediliyor: yaşa bölünen gruplarda doğum tarihi
girilmemiş kayıtlar görünmüyor (`list_split_totals_differ`). Böyle bir kayıt, grubu 160'ın
altındayken görülmüş, sonra grup büyüyüp yaşa bölünmüşse iki tarama boyunca görülmez ve
kaldırılabilir. Bu durum nadir ve %5 freninin altında kalıyor; Faz 8'de ele alınacak.

## Doğrulama

**Birinci dry-run, soyadı ve ad harfleriyle (2026-10-26):** uyruk adımı kalkınca
bölünemeyen grup `M, RU çıkaran, 37` grubunun tamamı oldu (167). Harfler 166'da kaldı;
tarama eksik bitti (`seen` 6360 / 6378, 634 istek).

**İkinci dry-run, ek olarak uyruk yedeğiyle (2026-10-26/27):** uyruk yedeği de kayıp kaydı
bulamadı (`name=V` ve grup düzeyinde 249'ar istek, RU uyruklular yine 160'ın üstünde).

**Üçüncü dry-run, soyadı uzatmasıyla (2026-10-27):** yalnızca `V` 160'ı aştı
(`extended_values` 1); `V`'nin 52 uzantısının hepsi tam okundu. Grup yine 166 / 167'de kaldı;
tarama eksik bitti (`seen` 6360 / 6381, 659 istek, `name_split_requests` 77). Soyadında
Latin harfi olan herkes görüldüğüne göre kayıp kayıt ya Latin harfi olmayan bir soyadına
sahip ya da Interpol'ün toplamında sayılıp hiçbir liste sorgusunda dönmüyor; hiçbir soyadı
sorgusuyla bulunamıyor. Soyadı kapsaması istek sayısını düşürdüğü ve kapsanabilen grupları
kanıtlı biçimde okuduğu için korunuyor. Kapsanamayan bir grubun bütün kaldırmaları
durdurmaması ADR 0010 ile çözülüyor.
