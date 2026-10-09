## upgrade-100k-rehearsal.json: upgrade 0007 → 0008
- bootstrap 7.40 s; counts {'fixtures': 100000, 'observations': 100000, 'evidence_ids': 1, 'bootstrap_observations': 100000, 'observations_total_bytes': 29679616, 'fixtures_total_bytes': 60022784}
- probe {'probe_reads': 59, 'interval_s': 0.05, 'max_read_ms': 7172.223199999962, 'reads_over_100ms': 1, 'max_blocked_read_ms': 7172.223199999962, 'errors': []}
- fixtures: heap +22.1 MiB, index +8.6 MiB, rows +0
- fixture_observations: heap +17.2 MiB, index +11.1 MiB, rows +100000
- fixture_provider_mappings: heap -0.2 MiB, index +0.0 MiB, rows +0
- team_provider_mappings: heap +0.0 MiB, index +0.0 MiB, rows +0
- teams: heap +0.0 MiB, index +0.0 MiB, rows +0
## upgrade-250k-rehearsal.json: upgrade 0007 → 0008
- bootstrap 17.92 s; counts {'fixtures': 250000, 'observations': 250000, 'evidence_ids': 1, 'bootstrap_observations': 250000, 'observations_total_bytes': 72892416, 'fixtures_total_bytes': 150323200}
- probe {'probe_reads': 79, 'interval_s': 0.05, 'max_read_ms': 17717.852499999935, 'reads_over_100ms': 1, 'max_blocked_read_ms': 17717.852499999935, 'errors': []}
- fixtures: heap +55.9 MiB, index +21.8 MiB, rows +0
- fixture_observations: heap +43.0 MiB, index +26.5 MiB, rows +250000
- fixture_provider_mappings: heap -0.4 MiB, index +0.0 MiB, rows +0
- team_provider_mappings: heap +0.0 MiB, index +0.0 MiB, rows +0
- teams: heap +0.0 MiB, index +0.0 MiB, rows +0
## upgrade-100k-writes.json: upgrade 0007 → 0008
- bootstrap 6.58 s; counts {'fixtures': 100000, 'observations': 100000, 'evidence_ids': 1, 'bootstrap_observations': 100000, 'observations_total_bytes': 29679616, 'fixtures_total_bytes': 60022784}
- probe {'probe_reads': 48, 'interval_s': 0.05, 'max_read_ms': 6389.659500000107, 'reads_over_100ms': 1, 'max_blocked_read_ms': 6389.659500000107, 'errors': []}
- fixtures: heap +22.1 MiB, index +8.6 MiB, rows +0
- fixture_observations: heap +17.2 MiB, index +11.1 MiB, rows +100000
- fixture_provider_mappings: heap -0.2 MiB, index +0.0 MiB, rows +0
- team_provider_mappings: heap +0.0 MiB, index +0.0 MiB, rows +0
- teams: heap +0.0 MiB, index +0.0 MiB, rows +0
## writes-passA.json: pase pass-A (203 s)
- contadores del pase: {'xact_commit': 8277, 'xact_rollback': 5, 'deadlocks': 0, 'conflicts': 0, 'temp_files': 0}
| caso | n | p50 ms | p95 ms | max ms | commit p50 | commit p95 | WAL p50 B | HOT fixtures | obs ins/muestra | created/updated/unchanged p50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| write_insert_380 | 30 | 76.2 | 97.7 | 105.4 | 1.99 | 3.17 | 677496 | — | 380 | 380/0/0 |
| write_update_380 | 30 | 72.4 | 93.1 | 98.3 | 1.78 | 5.26 | 559148 | 32.7% | 380 | 0/380/0 |
| write_unchanged_380 | 30 | 75.0 | 102.8 | 104.8 | 1.30 | 2.05 | 356516 | 85.1% | 380 | 0/0/380 |
| write_older_380 | 30 | 67.5 | 81.0 | 116.5 | 1.04 | 1.25 | 269488 | — | 380 | 0/0/380 |
| write_mixed_380 | 30 | 76.9 | 90.9 | 94.1 | 1.45 | 1.86 | 441392 | 100.0% | 380 | 127/127/126 |
| write_tie_380 | 30 | 65.0 | 74.3 | 79.3 | 1.06 | 1.36 | 269816 | — | 380 | 0/0/380 |
| write_replay_380 | 30 | 63.3 | 74.3 | 82.2 | 0.66 | 0.85 | 84468 | — | 0 | 0/0/380 |
| write_insert_1000 | 30 | 236.4 | 274.4 | 512.7 | 4.27 | 25.22 | 1773488 | — | 1000 | 1000/0/0 |
| write_update_1000 | 30 | 233.0 | 271.9 | 468.9 | 28.46 | 73.61 | 1069184 | 76.4% | 1000 | 0/1000/0 |
| write_unchanged_1000 | 30 | 195.7 | 227.9 | 250.9 | 2.22 | 3.66 | 825700 | 99.0% | 1000 | 0/0/1000 |
| write_older_1000 | 30 | 185.5 | 215.6 | 241.0 | 1.57 | 2.90 | 721092 | — | 1000 | 0/0/1000 |
| write_mixed_1000 | 30 | 211.3 | 247.7 | 257.5 | 2.93 | 24.55 | 1163644 | 99.9% | 1000 | 334/333/333 |
| write_tie_1000 | 30 | 188.4 | 222.3 | 248.6 | 2.18 | 31.49 | 720840 | — | 1000 | 0/0/1000 |
| write_replay_1000 | 30 | 187.5 | 212.6 | 229.8 | 0.94 | 1.54 | 227040 | — | 0 | 0/0/1000 |
| write_insert_2000 | 30 | 394.5 | 437.8 | 464.8 | 6.01 | 32.61 | 3542384 | — | 2000 | 2000/0/0 |
| write_update_2000 | 30 | 407.2 | 448.3 | 469.8 | 3.43 | 6.76 | 2314452 | 75.3% | 2000 | 0/2000/0 |
| write_unchanged_2000 | 30 | 411.1 | 448.2 | 472.7 | 2.87 | 15.58 | 1786524 | 94.8% | 2000 | 0/0/2000 |
| write_older_2000 | 30 | 386.1 | 409.1 | 422.7 | 3.18 | 29.44 | 1567900 | — | 2000 | 0/0/2000 |
| write_mixed_2000 | 30 | 403.2 | 465.5 | 470.3 | 2.86 | 9.44 | 2326932 | 99.9% | 2000 | 667/667/666 |
| write_tie_2000 | 30 | 390.0 | 430.4 | 442.4 | 4.27 | 7.88 | 1476892 | — | 2000 | 0/0/2000 |
| write_replay_2000 | 30 | 364.9 | 419.5 | 427.2 | 2.54 | 4.93 | 451700 | — | 0 | 0/0/2000 |
## writes-passB.json: pase pass-B (213 s)
- contadores del pase: {'xact_commit': 8274, 'xact_rollback': 5, 'deadlocks': 0, 'conflicts': 0, 'temp_files': 0}
| caso | n | p50 ms | p95 ms | max ms | commit p50 | commit p95 | WAL p50 B | HOT fixtures | obs ins/muestra | created/updated/unchanged p50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| write_insert_380 | 30 | 75.2 | 97.6 | 103.6 | 4.67 | 7.58 | 663600 | — | 380 | 380/0/0 |
| write_update_380 | 30 | 71.5 | 83.1 | 115.5 | 3.01 | 5.97 | 336380 | 100.0% | 380 | 0/380/0 |
| write_unchanged_380 | 30 | 71.5 | 87.0 | 106.8 | 2.85 | 5.69 | 307124 | 100.0% | 380 | 0/0/380 |
| write_older_380 | 30 | 69.0 | 83.6 | 97.8 | 2.93 | 12.22 | 269444 | — | 380 | 0/0/380 |
| write_mixed_380 | 30 | 78.7 | 94.4 | 111.8 | 4.79 | 17.13 | 435820 | 100.0% | 380 | 127/127/126 |
| write_tie_380 | 30 | 77.3 | 86.5 | 89.1 | 2.66 | 7.34 | 270404 | — | 380 | 0/0/380 |
| write_replay_380 | 30 | 72.7 | 90.2 | 94.4 | 2.47 | 18.92 | 84648 | — | 0 | 0/0/380 |
| write_insert_1000 | 30 | 220.1 | 313.9 | 996.8 | 7.76 | 64.05 | 1749960 | — | 1000 | 1000/0/0 |
| write_update_1000 | 30 | 202.9 | 237.0 | 277.8 | 2.58 | 21.47 | 896540 | 100.0% | 1000 | 0/1000/0 |
| write_unchanged_1000 | 30 | 187.9 | 212.5 | 239.1 | 2.21 | 4.19 | 829612 | 100.0% | 1000 | 0/0/1000 |
| write_older_1000 | 30 | 202.7 | 250.2 | 275.7 | 2.08 | 4.37 | 717644 | — | 1000 | 0/0/1000 |
| write_mixed_1000 | 30 | 232.3 | 271.0 | 288.6 | 4.16 | 13.21 | 1177356 | 99.9% | 1000 | 334/333/333 |
| write_tie_1000 | 30 | 204.4 | 220.1 | 245.7 | 4.88 | 7.12 | 758396 | — | 1000 | 0/0/1000 |
| write_replay_1000 | 30 | 210.3 | 244.0 | 301.1 | 2.58 | 9.60 | 226340 | — | 0 | 0/0/1000 |
| write_insert_2000 | 30 | 414.6 | 459.2 | 490.0 | 7.64 | 15.71 | 3522524 | — | 2000 | 2000/0/0 |
| write_update_2000 | 30 | 420.3 | 464.7 | 483.6 | 2.71 | 27.10 | 2462480 | 61.7% | 2000 | 0/2000/0 |
| write_unchanged_2000 | 30 | 409.8 | 448.4 | 468.2 | 2.62 | 3.71 | 1873320 | 85.5% | 2000 | 0/0/2000 |
| write_older_2000 | 30 | 388.2 | 438.1 | 457.9 | 3.94 | 8.10 | 1506168 | — | 2000 | 0/0/2000 |
| write_mixed_2000 | 30 | 436.7 | 483.3 | 485.7 | 4.76 | 13.77 | 2334944 | 99.5% | 2000 | 667/667/666 |
| write_tie_2000 | 30 | 389.5 | 427.0 | 456.0 | 4.76 | 9.90 | 1500476 | — | 2000 | 0/0/2000 |
| write_replay_2000 | 30 | 380.7 | 433.1 | 436.6 | 2.43 | 2.83 | 451164 | — | 0 | 0/0/2000 |
## writes-passC.json: pase pass-C (212 s)
- contadores del pase: {'xact_commit': 8281, 'xact_rollback': 5, 'deadlocks': 0, 'conflicts': 0, 'temp_files': 0}
| caso | n | p50 ms | p95 ms | max ms | commit p50 | commit p95 | WAL p50 B | HOT fixtures | obs ins/muestra | created/updated/unchanged p50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| write_insert_380 | 30 | 78.0 | 108.5 | 126.9 | 2.08 | 19.58 | 662844 | — | 380 | 380/0/0 |
| write_update_380 | 30 | 73.4 | 84.0 | 103.9 | 1.22 | 1.62 | 336740 | 100.0% | 380 | 0/380/0 |
| write_unchanged_380 | 30 | 74.8 | 88.3 | 109.1 | 1.25 | 2.10 | 319600 | 100.0% | 380 | 0/0/380 |
| write_older_380 | 30 | 70.7 | 80.2 | 102.8 | 1.12 | 1.74 | 272092 | — | 380 | 0/0/380 |
| write_mixed_380 | 30 | 78.7 | 84.4 | 86.7 | 1.37 | 1.68 | 436648 | 100.0% | 380 | 127/127/126 |
| write_tie_380 | 30 | 70.3 | 77.9 | 79.4 | 1.10 | 1.38 | 270984 | — | 380 | 0/0/380 |
| write_replay_380 | 30 | 70.2 | 78.6 | 90.2 | 2.26 | 3.27 | 84740 | — | 0 | 0/0/380 |
| write_insert_1000 | 30 | 204.7 | 219.2 | 257.1 | 5.65 | 9.26 | 1746360 | — | 1000 | 1000/0/0 |
| write_update_1000 | 30 | 205.4 | 232.5 | 248.9 | 5.55 | 12.71 | 909228 | 99.0% | 1000 | 0/1000/0 |
| write_unchanged_1000 | 30 | 206.5 | 232.1 | 249.4 | 4.76 | 11.78 | 821128 | 100.0% | 1000 | 0/0/1000 |
| write_older_1000 | 30 | 199.8 | 214.4 | 236.4 | 4.83 | 7.39 | 723964 | — | 1000 | 0/0/1000 |
| write_mixed_1000 | 30 | 217.2 | 246.6 | 259.0 | 4.69 | 11.69 | 1163336 | 100.0% | 1000 | 334/333/333 |
| write_tie_1000 | 30 | 194.7 | 208.5 | 256.4 | 4.63 | 8.94 | 719092 | — | 1000 | 0/0/1000 |
| write_replay_1000 | 30 | 190.2 | 202.9 | 234.0 | 2.40 | 6.10 | 225352 | — | 0 | 0/0/1000 |
| write_insert_2000 | 30 | 414.5 | 448.0 | 471.1 | 8.83 | 35.99 | 3525720 | — | 2000 | 2000/0/0 |
| write_update_2000 | 30 | 401.6 | 578.6 | 1210.9 | 2.33 | 59.41 | 2146196 | 97.4% | 2000 | 0/2000/0 |
| write_unchanged_2000 | 30 | 411.2 | 448.7 | 459.5 | 4.08 | 8.66 | 1874176 | 88.7% | 2000 | 0/0/2000 |
| write_older_2000 | 30 | 387.7 | 433.1 | 441.0 | 4.33 | 9.97 | 1464328 | — | 2000 | 0/0/2000 |
| write_mixed_2000 | 30 | 430.6 | 484.4 | 495.8 | 4.90 | 27.66 | 2360904 | 99.9% | 2000 | 667/667/666 |
| write_tie_2000 | 30 | 386.7 | 433.3 | 443.8 | 1.63 | 3.43 | 1479084 | — | 2000 | 0/0/2000 |
| write_replay_2000 | 30 | 378.4 | 427.7 | 430.9 | 1.38 | 2.86 | 450304 | — | 0 | 0/0/2000 |
