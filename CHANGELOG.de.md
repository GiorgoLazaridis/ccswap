# Änderungsverlauf des Forks (Deutsch)

[English](CHANGELOG.md) · [README](README.md) · [Upstream](https://github.com/errhythm/ccswap)

Hier stehen die Änderungen in Giorgos Fork **gegenüber dem letzten gemeinsamen
Upstream-Commit [`14df53c` (v0.35.1)](https://github.com/errhythm/ccswap/commit/14df53cf3eba7c86a10d03698b958f7ba809b1c4)**.
Bereits bei Upstream vorhandene Funktionen sind nicht erneut aufgelistet. Die
Daten sind Commit-Daten. Der [Git-Tag `v0.35.1-gl.1`](https://github.com/GiorgoLazaridis/ccswap/tree/v0.35.1-gl.1)
trägt noch die Paketversion `0.35.1`; die Paketversionen mit `+gl.*` beginnen
bei `0.36.0+gl.1`.

## 0.36.0+gl.2 — 2026-09-28

- **Dokumentation des Forks.** Die README kennzeichnet diesen Git-Fork,
  verlinkt den zweisprachigen Verlauf und unterscheidet das Upstream-PyPI-Paket.
  Paketmetadaten verweisen auf Repository, Issues und Changelog des Forks.
  Repo-Regeln und CI verlangen künftig beide Sprachen bei Codeänderungen. Der
  CI-Workflow lässt sich auch ohne vorherigen Vergleichs-Commit manuell prüfen.
- **Nur eine dauerhafte Auto-Schleife je Anbieter.** Ein zweiter Aufruf von
  `ccswap auto` oder `ccswap codex auto` endet mit Fehler statt parallel zu
  pollen und zu wechseln. Einmalige Aufrufe mit `--once` bleiben möglich.
  [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/23ea59dd39e3151b62c814f6e132eb8d5de699d4)
- **Codex-Quotenereignisse.** JSON-Poll-Ereignisse enthalten nun je Konto die
  Verbrauchsprozente für das 5-Stunden- und 7-Tage-Fenster. Ein Wächter kann
  beide Schwellen aus dem vorhandenen Poll erkennen, ohne selbst nochmals die
  Quote abzufragen. [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/23ea59dd39e3151b62c814f6e132eb8d5de699d4)
- **Sicherere Codex-Entscheidungen.** Schlägt die aktuelle Abfrage fehl, wird
  ein zwischengespeicherter letzter guter Wert nicht als frischer Schaltwert
  behandelt. [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/23ea59dd39e3151b62c814f6e132eb8d5de699d4)

## 0.36.0+gl.1 — 2026-09-26

- **Intelligenter nativer Claude-Start.** `ccswap run --smart` wählt ein
  geeignetes isoliertes Claude-Profil anhand von Quotenreserve,
  Vertrauenswürdigkeit der Messung, laufenden Sessions und festen
  Verzeichniszuordnungen. `--explain` und `--dry-run` erklären die Wahl;
  `ccswap plan [--json] [--refresh]` zeigt eine lesende Vorschau und bei
  ausreichenden Reset-Daten einen unverbindlichen Hinweis zum 5-Stunden-Fenster.
  Für Codex gibt es damit noch keinen isolierten Starter; die Regeln von
  `ccswap auto` ändern sich dadurch nicht.
  [Funktion](https://github.com/GiorgoLazaridis/ccswap/commit/4b01430fe662364f0f20bf620e9cb484067ce4c1) ·
  [Korrektur der Datenbewertung](https://github.com/GiorgoLazaridis/ccswap/commit/715a8f5ca107dc7b5370c5806f42ef14bc2f4154)
- **Lesende Diagnose.** `ccswap doctor` und `ccswap health` prüfen lokale CLI,
  Kontoliste, Einstellungen, Quotencache und Endpoint-Overrides, auch als
  JSON. Sie bestätigen weder live die Gültigkeit von Tokens noch Windows-ACLs.
  [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/55dd58dc58186dc0646d1a7b3dc3ad6591b3e443)
- **Sicherer Session-Start.** Unlesbare Prozessdaten verhindern
  Profiländerungen; Smart Start lehnt verschachtelte `CLAUDE_CONFIG_DIR` ab und
  prüft das Default-Konto vor dem Start erneut. Ein kurzes Zeitfenster für
  einen gleichzeitigen Default-Wechsel bleibt dokumentiert.
  [Prozesskorrektur](https://github.com/GiorgoLazaridis/ccswap/commit/2a01d53) ·
  [Profilkorrektur](https://github.com/GiorgoLazaridis/ccswap/commit/4d19c6f) ·
  [erneute Kontoprüfung](https://github.com/GiorgoLazaridis/ccswap/commit/3c66a56)
- **Erkennbar installierter Fork.** Das Paket bekam das Suffix `+gl.1`;
  irreführende PyPI-Updatehinweise und das Self-Upgrade des Fork-Builds wurden
  deaktiviert. Installation und Update erfolgen per `uv tool install` aus Git.
  [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/9864849)
- **Architekturdokumentation.** [Native Session Allocation](docs/native-session-allocation.md)
  und die [deutsche v2-Roadmap](docs/ccswap-v2-roadmap.de.md) beschreiben
  den umgesetzten Stand und offene Risiken.
  [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/c237155)

## v0.35.1-gl.1 — 2026-09-26 (Git-Tag)

- **Atomare Veröffentlichung der Kontoliste unter Windows.** `sequence.json`
  wird atomar ersetzt, damit parallele Leser keine unvollständige Datei sehen.
  [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/68ffc97)
- **Dekodierung der Windows-Prozessausgabe.** `tasklist` nutzt die OEM-Codepage
  und toleriert nicht dekodierbare Bytes; `claude auth status --json` wird als
  UTF-8 gelesen. Das behebt fehlgeschlagene Codex-Auto-Ticks bei lokalisierten
  Windows-Ausgaben. [Commit](https://github.com/GiorgoLazaridis/ccswap/commit/aa023fc)

Weitere Fork-Änderungen werden hier und in [CHANGELOG.md](CHANGELOG.md)
dokumentiert. Die Upstream-Versionen stehen in deren
[Release-Übersicht](https://github.com/errhythm/ccswap/releases).
