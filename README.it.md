<div align="center">

<img src="assets/icon.png" width="96" alt="Logo Levelo">

# Levelo

**Basta alzare e abbassare il volume durante i film.**
Le esplosioni vengono domate e i sussurri alzati, in automatico, per tutte le app del PC.

[Scarica](https://github.com/Xillo23/levelo/releases/latest) · [English](README.md) · [Come funziona](#come-funziona) · [Domande frequenti](#domande-frequenti)


[![Sostieni Levelo su Ko-fi](https://img.shields.io/badge/Ko--fi-Sostieni%20Levelo-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/xillo23)

</div>

> [!NOTE]
> **Levelo è in beta pubblica.** Funziona, ma non è ancora stato provato su molti PC.
> Se sul tuo qualcosa non va, [apri una segnalazione](https://github.com/Xillo23/levelo/issues).
> La versione 1.0 è prevista per febbraio.

---

La solita storia: alzi il volume per capire i dialoghi e alla prima scena d'azione ti
sfondi i timpani. **Levelo** risolve il problema: sta nell'area di notifica e
tiene tutto a un livello comodo, in tempo reale.

- 💥 **Abbassa le parti forti in 10-30 ms**: esplosioni, spari, musica improvvisa.
- 🗣️ **Alza dolcemente le parti deboli**: sussurri e dialoghi bassi diventano comprensibili.
- 🎚️ **Usi il volume di Windows come sempre**: tasti, barra delle applicazioni, casse. Non c'è uno slider in più.
- 🌙 **Tre intensità**: *Leggero*, *Normale* e *Notte* (tutto quasi allo stesso livello).
- 🪶 **Leggero e privato**: niente account, nessun tracciamento, niente driver. L'audio non esce dal PC.
- 🌍 **11 lingue**.

## Installazione

1. Scarica **`Levelo-x.y.z-Setup.exe`** dall'[ultima release](https://github.com/Xillo23/levelo/releases/latest).
2. Avvialo. Non servono permessi di amministratore.
3. Fatto: Levelo parte nell'area di notifica, vicino all'orologio.

Esiste anche una **versione portable** in zip. Requisiti: Windows 10 o 11 a 64 bit.

## Uso

Non devi fare niente. Il click sull'icona apre il pannello, dove trovi:
- l'**interruttore** per attivare o mettere in pausa;
- un **grafico** di cosa sta facendo (▲ alza, ▼ abbassa);
- l'**intensità**;
- l'opzione **Avvia con Windows**.

Il click destro apre il menu: intensità, lingua, avvio automatico, esci.

> **Consiglio:** per alzare le voci basse serve un po' di margine. Se il volume di Windows
> è al 100%, abbassalo un po' e alza le casse, la TV o il monitor. Il pannello te lo segnala.

## Come funziona

1. **Ascolta** l'audio in uscita del PC (loopback WASAPI) a blocchi di 10 ms.
2. **Misura il volume percepito** con la pesatura K (standard ITU-R BS.1770) e ignora le
   pause tra le parole.
3. **Calcola la correzione**:
   - taglio rapido quando il suono sale;
   - ritorno in circa 1 s dopo una scena forte;
   - risalita lenta delle parti deboli;
   - il silenzio non viene mai alzato.
4. **La applica al volume delle singole app** (mixer di Windows), non al volume generale.
5. **Rimette tutto com'era** in pausa, all'uscita, alla disinstallazione, al cambio di
   dispositivo e anche dopo un crash (al riavvio successivo).

## Domande frequenti

**Windows SmartScreen dice "PC protetto da Windows".**
Succede con le app nuove senza un certificato di firma a pagamento. Clicca *Ulteriori
informazioni* → *Esegui comunque*. Il codice sorgente completo è in questo repository.

**Funziona con cuffie, Bluetooth e HDMI?** Sì, segue sempre il dispositivo predefinito.

**Si collega a internet?**
Solo per controllare gli aggiornamenti: due volte al giorno scarica due file pubblici da questo
repository GitHub. Non invia nessun dato su di te o sul tuo PC.

**Rallenta il PC?** No, usa circa l'1% di un core.

## Sostieni il progetto

Levelo è gratuito e open source, senza pubblicità né tracciamento. Se ti ha salvato le
orecchie, **[offrimi un caffè su Ko-fi](https://ko-fi.com/xillo23)** ☕❤️

## Licenza

[GPL-3.0](LICENSE).
