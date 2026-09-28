# ccswap v2: Stand und weiterer Umbau

Stand: 2026-09-28. Diese Datei beschreibt die Architekturabsicht und den
tatsächlich implementierten Umfang des Forks. `0.36.0+gl.1` ist der erste
Ausbauschritt, keine abgeschlossene v2.

## Zielbild

ccswap entscheidet **vor dem Start** einer offiziellen CLI, welcher lokale
Account eine neue Session erhält. Danach führt Claude Code beziehungsweise
Codex CLI die AI-Anfragen selbst direkt aus. Eine Session behält ihren Account.
Daneben bleibt `ccswap auto` für den überwachten Wechsel des Default-Logins
eine eigenständige Kernfunktion:

```text
ccswap auto             -> Entscheidung über den Default-Login
ccswap run --smart      -> einmalige Entscheidung für eine neue native Session
```

Die gemeinsame Grundlage darf Quota-, Gesundheits- und Prozessdaten sein. Die
beiden Entscheidungen haben unterschiedliche Seiteneffekte und dürfen nicht
ungeprüft dieselbe Umschaltlogik ausführen. Bestehende Auto-Switch-Defaults,
Threshold, Hysterese, Cooldowns und Strategien bleiben erhalten.

Kein Provider-Proxy, Gateway, Request-Routing, CLI-Imitat und keine künstlichen
Prompts zum Öffnen von Quota-Fenstern. Diese Grenzen gelten auch für spätere
Ausbauschritte.

## Was bereits vorhanden war

Der Fork hatte vor diesem Umbau einen adaptiven UsageStore mit
last-known-good-Daten, Backoff und 429-Behandlung, Auto-Switch-Strategien,
Credential Locks und synchronisierten Token Refresh, Claude-Session-Profile,
Prozess-Erkennung, Projekt-Mappings, getrennte Codex-Logik, TUI, JSON-Ausgaben
und Export/Import. Die v2-Arbeit baut darauf auf; die bestehende Auto-Engine
wurde in diesem Schritt nicht ersetzt.

## Umgesetzt in `0.36.0+gl.1`

| Bereich | Tatsächlicher Stand |
| --- | --- |
| Smart Start | `ccswap run --smart` wählt einen Account für ein isoliertes Claude-Code-Profil; `--explain` und `--dry-run` zeigen die Entscheidung. |
| Policy | Ein Snapshot, begrenzte 5h/7d-Headroom-Bewertung, lokale aktive Sessions und harte Projekt-Mappings. Unbekannte oder nicht mehr entscheidungsverlässliche Daten führen zu keiner automatischen Wahl. |
| Planung | `ccswap plan [--json] [--refresh]` zeigt Kandidaten und gegebenenfalls einen passiven Hinweis zur größten beobachteten Lücke im 5h-Zyklus. Kein Forecast. |
| Diagnose | `doctor` und `health` prüfen lokale Metadaten, CLI-Verfügbarkeit und Endpoint-/Auth-Overrides. Sie prüfen weder live die Token-Gültigkeit noch Windows-ACLs. |
| Sicherheit | Lesende Vorschauen vermeiden Credential-Migration und Token Refresh. Unlesbare Prozessdaten verhindern Profiländerungen. Smart Start prüft den Default-Login vor und nach dem Profilaufbau. |
| Fork-Installation | Paketversion `0.36.0+gl.1`; PyPI-Updatehinweise und Self-Upgrade werden für diesen Fork-Build unterdrückt. |

Technische Details und Grenzen stehen in
[native-session-allocation.md](native-session-allocation.md). Die Befehle stehen
in der [README](../README.md).

## Ergänzt in `0.36.0+gl.2`

- Pro Anbieter darf nur eine dauerhafte `auto`-Schleife laufen. Ein zweiter
  Aufruf meldet den Konflikt und beendet sich; `--once` bleibt verfügbar.
- Codex-Poll-Ereignisse geben 5h- und 7d-Prozent getrennt aus. Scheitert eine
  Abfrage, wird ein alter letzter Messwert nicht als frischer Schaltwert genutzt.
  Ein übergeordneter Wächter kann diese Ereignisse für Benachrichtigungen und
  Übergaben verwenden, ohne selbst eine zweite Quotenabfrage zu starten.

## Ergänzt in `0.36.0+gl.3`

- Smart Start und `plan` beachten die in `autoswitch.model` konfigurierten
  Modell-Wochenlimits wie der Auto-Wechsel.
- `ccswap statusline` zeigt Konto- und Poolzustand in Claude Codes
  Statuszeile, rein lesend aus dem Cache. Token- und Kostenverläufe bleiben
  bei [ccusage](https://github.com/ccusage/ccusage); ccswap rechnet sie nicht
  nach.
- `doctor` meldet konkurrierende `claude`-/`codex`-Installationen im PATH.

## Ergänzt in `0.36.0+gl.4`

- `ccswap codex auto` schreibt je Durchlauf eine Quoten-Momentaufnahme ohne
  Identitätsdaten. Statuszeile und `list` zeigen daraus Codex; alte oder
  fehlgeschlagene Messungen sind gekennzeichnet, Kontoänderungen verwerfen
  die Aufnahme.

## Nächste Schritte in sinnvoller Reihenfolge

1. **Start-Race schließen.** Eine kleine, lokale Launch-Reservierung zwischen
   Smart Start und `auto` prüfen. Sie muss beim Crash verfallen und darf weder
   laufende Sessions noch die Refresh-Locks blockieren. Vorher den genauen
   Lock-Ablauf mit Fake-Prozessen und konkurrierenden Starts testen.
2. **Session-Status verlässlich machen.** Die vorhandene Claude-Prozesssuche
   zuerst gegen Windows-PID-Reuse, unlesbare Records und Prozessende härten.
   Eine separate Registry nur ergänzen, falls sie gegenüber den nativen
   Prozessdaten eine konkrete Lücke schließt. Keine Prompts speichern.
3. **Doctor vertiefen.** Sichere Checks für Konfigurationsschema, verwaiste
   Profile, Backup-Konsistenz und Windows-ACLs ergänzen. Live-Token- oder
   Quota-Checks nur als ausdrücklich angeforderte, begrenzte Diagnose.
4. **Kleine Quota-Zeitreihe.** Ausschließlich Zeit, Account, Window-Prozent
   und Reset speichern; Retention begrenzen. Erst mit echten Messpunkten
   Burn Rate und Confidence definieren und mit Fake Clock testen.
5. **Capacity Plan.** 5h-/7d-Projektion und Pool-Abdeckung nur anzeigen, wenn
   Messdichte und Reset-Daten reichen. Sonst `unknown` statt präziser ETA.
   Die Rolling-Window-Empfehlung bleibt passiv und darf einen gewünschten
   Session-Start nicht verzögern.
6. **Reserve und Affinität.** Zuerst den Alltagsnutzen einfacher Reserve- und
   Soft-Affinity-Regeln prüfen. Bestehende harte Mappings haben Vorrang.
   Rollen brauchen klare Migrations- und Override-Semantik, bevor sie in die
   Konfiguration kommen.
7. **Auto gezielt verbessern.** Nur belegbar bessere Zielwahl aus
   verlässlichen Daten übernehmen. Live-Modus, Default und bestehende
   Strategien unverändert lassen, bis ein konkreter Fehler oder ein vom
   Nutzer gewünschter optionaler Modus sauber getestet ist.
8. **Provider und Oberfläche.** Codex-Smart-Launch erst nach Prüfung seiner
   Start-/Auth-/Resume-Semantik. Danach TUI-Ansichten für Plan und Health;
   weitere Provider nur mit offizieller nativer CLI und geklärtem Login.

Verschlüsselter Export, DPAPI-/Keychain-Migration, automatische
Conversation-Affinity und weitere Konfigurationsrollen bleiben Vorschläge.
Sie kommen nur hinzu, wenn sie den täglichen Ablauf messbar verbessern und
Bestandsinstallationen nicht gefährden.

## Prüf- und Freigabekriterien

- Windows 11 und PowerShell 7 sind die primäre Laufzeit. Jeder Eingriff in
  Credentials oder Prozesse braucht Tests für gleichzeitige Sessions, Crash,
  Lock-Timeout und Token-Refresh-Synchronisation.
- `ccswap auto` und explizites `ccswap run N` müssen nach jedem Schritt
  regressionsfrei bleiben. Der Smart-Pfad darf keine zweite Kopie des gerade
  aktiven Default-Credentials starten.
- Native Claude-/Codex-Funktionen wie Resume, MCP, Hooks und Tools brauchen
  einen manuellen Smoke-Test mit echten CLIs. Solche Tests wurden für den
  aktuellen Stand **nicht** durchgeführt.
- Die vorhandenen direkten OAuth-Quota-Abfragen sind technisch Bestand.
  Eine ausdrückliche offizielle Erlaubnis dafür ist **nicht verifiziert**;
  neue Automatisierung darf daraus keine allgemeine Provider-API machen.
- Vor größeren Folgeschritten erneut unabhängiges Architektur-Review und
  nach dem Diff ein Security-/Windows-/Race-Review. Der erste Ausbauschritt
  wurde durch Opus und Codex unabhängig geprüft; wichtige Review-Funde
  wurden vor dem Push behoben.

## Installation des Forks unter Windows

Die PyPI-Anweisung `uv tool install ccswap` installiert Upstream. Für diesen
Fork gilt der Git-Befehl in der [README](../README.md#using-uv-recommended).
Vor einer Neuinstallation laufende `ccswap auto`-Supervisoren anhalten und
prüfen, dass keine `ccswap.exe`- oder zugehörigen Tool-Python-Prozesse mehr
die uv-Umgebung sperren. Danach installieren, `ccswap --version` prüfen und
die Supervisoren wieder starten. Ein gesperrtes `uv tool install --force`
kann die bisherige Tool-Umgebung unvollständig zurücklassen.
