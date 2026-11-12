# DIGDAG – QGIS-plugin v0.4.2

QGIS 3.44 og 4.2 – historiske administrative grænser i Danmark, 1660–2010.

## Installation

1. I QGIS: **Plugins → Administrér og installér plugins → Installér fra ZIP**. Vælg `DIGDAG_QGIS_v0.4.2.zip`.
2. Genstart QGIS (især ved opgradering fra v0.3), og åbn **DIGDAG** fra pluginmenuen.
3. WMS er standardkilde. Åbn **Datakilde** hvis dit token endnu ikke er angivet. Klik **Find DIGDAG-lag**.
4. Vælg tema og undertema, vælg år/dato, og klik **Vis / opdater DIGDAG på kortet**.
5. Afspil med **▶ Afspil · 10 år**. Der kan pauses undervejs.

## Rettelse i v0.4.2

- **QGIS 4.2 / Qt6**: Retter fejl under »Find DIGDAG-lag«: `int() argument must be ... not 'NetworkError'`. Netværksfejlen testes nu med en Qt5-/Qt6-kompatibel enum-kontrol. WMS-svaret frigives også ved fejl.
- Øvrig funktionalitet i v0.4 er uændret.

## Rettelser i v0.4

- **Zoom**: Afspilning er tilladt i alle målestokke fra 1:1 til 1:100.000 (inklusive). **Pluginet ændrer aldrig zoomniveauet automatisk.** Ved mere oversigtsprægede kort vises en besked, og afspilleren starter ikke. Almindelig visning/tidsfiltrering har ingen zoomgrænse.
- **Stabil afspilning**: Har nu egen timer frem for at vente på `mapCanvasRefreshed`. Hvert år vises i minimum 1,6 sekund; når QGIS stadig tegner, venter timeren i 200-ms trin i op til 20 sekunder. Et bevidst tryk på pause annullerer timere. Fra 2010 starter næste afspilning igen i 1660.
- **QGIS 4.2 WMS**: Anvender GDAL WMS-servicebeskrivelser frem for QGIS' egen WMS-provider. Dette omgår providerens problem med gzip-komprimeret GetCapabilities i visse Qt6/QGIS 4.2-installationer. GDAL-beskrivelserne genererer `GetMap`-kald med `time=YYYY-MM-DD` og læser kortbilleder som virtuelle blokke.
- **QGIS 3.44 WMS**: Beholder den tidligere native QGIS-WMS-provider, hvor den virker.
- Vektorfilter og eksisterende enkeltklik-/træk-opdatering på tidslinjen er uændrede.

## Tekniske noter

- Lokale SHP-filer filtreres med de oprindelige `FRA`/`TIL`-udtryk.
- GDAL WMS-kilder oprettes som XML-filer i undermappen `digdag_wms` under QGIS' brugerprofil. De bliver liggende, så gemte QGIS-projekter kan genåbne dem. De indeholder et Dataforsyningen-token som del af WMS-adressen: behandl brugerprofilen som privat, og del ikke XML-filer med token.
- GDAL-varianten kræver, at QGIS-installationens GDAL har WMS-driveren. Ved virksomhedsmiljøer med særlig proxy-opsætning kan GDALs netværksadgang skulle konfigureres separat.
- Denne version er testet med ren Python og ved, at GDAL kan åbne den genererede WMS-beskrivelse. Korttegningen og netværksadgangen på din konkrete QGIS 4.2-maskine kan ikke verificeres herfra.
