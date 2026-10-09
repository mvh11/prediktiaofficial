## upgrade-100k-rehearsal.json: upgrade 0007 → 0008
- bootstrap 7.31 s; counts {'fixtures': 100000, 'observations': 100000, 'evidence_ids': 1, 'bootstrap_observations': 100000, 'observations_total_bytes': 29679616, 'fixtures_total_bytes': 60022784}
- probe {'probe_reads': 59, 'interval_s': 0.05, 'max_read_ms': 7088.327499979641, 'reads_over_100ms': 1, 'max_blocked_read_ms': 7088.327499979641, 'errors': []}
- fixtures: heap +22.1 MiB, index +8.6 MiB, rows +0
- fixture_observations: heap +17.2 MiB, index +11.1 MiB, rows +100000
- fixture_provider_mappings: heap -0.2 MiB, index +0.0 MiB, rows +0
- team_provider_mappings: heap +0.0 MiB, index +0.0 MiB, rows +0
- teams: heap +0.0 MiB, index +0.0 MiB, rows +0
## upgrade-250k-rehearsal.json: upgrade 0007 → 0008
- bootstrap 18.37 s; counts {'fixtures': 250000, 'observations': 250000, 'evidence_ids': 1, 'bootstrap_observations': 250000, 'observations_total_bytes': 72892416, 'fixtures_total_bytes': 150323200}
- probe {'probe_reads': 84, 'interval_s': 0.05, 'max_read_ms': 18176.260699983686, 'reads_over_100ms': 1, 'max_blocked_read_ms': 18176.260699983686, 'errors': []}
- fixtures: heap +55.9 MiB, index +21.8 MiB, rows +0
- fixture_observations: heap +43.0 MiB, index +26.5 MiB, rows +250000
- fixture_provider_mappings: heap -0.4 MiB, index +0.0 MiB, rows +0
- team_provider_mappings: heap +0.0 MiB, index +0.0 MiB, rows +0
- teams: heap +0.0 MiB, index +0.0 MiB, rows +0
## upgrade-100k-writes.json: upgrade 0007 → 0008
- bootstrap 8.02 s; counts {'fixtures': 100000, 'observations': 100000, 'evidence_ids': 1, 'bootstrap_observations': 100000, 'observations_total_bytes': 29679616, 'fixtures_total_bytes': 60022784}
- probe {'probe_reads': 51, 'interval_s': 0.05, 'max_read_ms': 7868.648500007112, 'reads_over_100ms': 1, 'max_blocked_read_ms': 7868.648500007112, 'errors': []}
- fixtures: heap +22.1 MiB, index +8.6 MiB, rows +0
- fixture_observations: heap +17.2 MiB, index +11.1 MiB, rows +100000
- fixture_provider_mappings: heap -0.2 MiB, index +0.0 MiB, rows +0
- team_provider_mappings: heap +0.0 MiB, index +0.0 MiB, rows +0
- teams: heap +0.0 MiB, index +0.0 MiB, rows +0
## writes-passA.json: pase pass-A (243 s)
- contadores del pase: {'xact_commit': 8302, 'xact_rollback': 5, 'deadlocks': 0, 'conflicts': 0, 'temp_files': 0}
| caso | n | p50 ms | p95 ms | max ms | commit p50 | commit p95 | WAL p50 B | HOT fixtures | obs ins/muestra | created/updated/unchanged p50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| write_insert_380 | 30 | 90.7 | 131.9 | 133.3 | 2.25 | 16.04 | 677472 | — | 380 | 380/0/0 |
| write_update_380 | 30 | 102.7 | 125.8 | 135.7 | 10.43 | 18.26 | 559144 | 32.7% | 380 | 0/380/0 |
| write_unchanged_380 | 30 | 96.3 | 126.2 | 160.2 | 3.83 | 17.07 | 356528 | 85.1% | 380 | 0/0/380 |
| write_older_380 | 30 | 79.3 | 109.7 | 117.6 | 1.13 | 2.76 | 269476 | — | 380 | 0/0/380 |
| write_mixed_380 | 30 | 100.1 | 120.3 | 211.7 | 4.26 | 18.64 | 439500 | 100.0% | 380 | 127/127/126 |
| write_tie_380 | 30 | 85.8 | 98.5 | 115.7 | 1.13 | 1.67 | 269556 | — | 380 | 0/0/380 |
| write_replay_380 | 30 | 83.6 | 105.7 | 113.7 | 0.73 | 0.92 | 84480 | — | 0 | 0/0/380 |
| write_insert_1000 | 30 | 261.1 | 410.4 | 584.6 | 11.54 | 162.27 | 1779248 | — | 1000 | 1000/0/0 |
| write_update_1000 | 30 | 229.3 | 258.2 | 263.3 | 3.30 | 8.62 | 1109196 | 71.1% | 1000 | 0/1000/0 |
| write_unchanged_1000 | 30 | 219.6 | 251.7 | 269.3 | 2.30 | 7.73 | 842628 | 97.0% | 1000 | 0/0/1000 |
| write_older_1000 | 30 | 211.2 | 240.0 | 260.2 | 1.46 | 2.55 | 737304 | — | 1000 | 0/0/1000 |
| write_mixed_1000 | 30 | 241.8 | 280.7 | 322.6 | 2.54 | 18.82 | 1173592 | 99.9% | 1000 | 334/333/333 |
| write_tie_1000 | 30 | 240.0 | 342.5 | 476.4 | 1.41 | 4.28 | 716864 | — | 1000 | 0/0/1000 |
| write_replay_1000 | 30 | 218.2 | 252.9 | 262.1 | 0.78 | 1.13 | 226716 | — | 0 | 0/0/1000 |
| write_insert_2000 | 30 | 480.8 | 537.4 | 559.9 | 8.11 | 39.45 | 3544624 | — | 2000 | 2000/0/0 |
| write_update_2000 | 30 | 493.3 | 548.9 | 594.8 | 3.07 | 44.77 | 2242952 | 73.0% | 2000 | 0/2000/0 |
| write_unchanged_2000 | 30 | 484.2 | 574.7 | 635.8 | 2.32 | 10.80 | 1801792 | 90.3% | 2000 | 0/0/2000 |
| write_older_2000 | 30 | 471.6 | 584.3 | 605.9 | 4.70 | 10.44 | 1805764 | — | 2000 | 0/0/2000 |
| write_mixed_2000 | 30 | 496.0 | 588.1 | 706.0 | 4.79 | 90.90 | 2358016 | 94.3% | 2000 | 667/667/666 |
| write_tie_2000 | 30 | 455.3 | 540.5 | 545.6 | 1.89 | 5.19 | 1465780 | — | 2000 | 0/0/2000 |
| write_replay_2000 | 30 | 447.0 | 506.2 | 514.5 | 2.29 | 6.01 | 641820 | — | 0 | 0/0/2000 |
## writes-passB.json: pase pass-B (245 s)
- contadores del pase: {'xact_commit': 8290, 'xact_rollback': 5, 'deadlocks': 0, 'conflicts': 0, 'temp_files': 0}
| caso | n | p50 ms | p95 ms | max ms | commit p50 | commit p95 | WAL p50 B | HOT fixtures | obs ins/muestra | created/updated/unchanged p50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| write_insert_380 | 30 | 86.1 | 108.3 | 116.9 | 4.08 | 10.02 | 663272 | — | 380 | 380/0/0 |
| write_update_380 | 30 | 90.5 | 106.0 | 118.0 | 3.03 | 12.26 | 337716 | 100.0% | 380 | 0/380/0 |
| write_unchanged_380 | 30 | 83.6 | 100.6 | 127.9 | 2.99 | 6.39 | 308016 | 100.0% | 380 | 0/0/380 |
| write_older_380 | 30 | 79.8 | 107.0 | 108.9 | 2.82 | 5.98 | 269508 | — | 380 | 0/0/380 |
| write_mixed_380 | 30 | 91.9 | 112.0 | 119.0 | 3.87 | 7.69 | 435576 | 99.9% | 380 | 127/127/126 |
| write_tie_380 | 30 | 81.0 | 95.4 | 97.6 | 2.81 | 6.28 | 269448 | — | 380 | 0/0/380 |
| write_replay_380 | 30 | 80.7 | 91.8 | 93.5 | 2.35 | 5.07 | 84544 | — | 0 | 0/0/380 |
| write_insert_1000 | 30 | 255.5 | 285.6 | 324.5 | 19.84 | 42.95 | 1746504 | — | 1000 | 1000/0/0 |
| write_update_1000 | 30 | 238.4 | 262.7 | 269.4 | 1.96 | 3.21 | 897248 | 100.0% | 1000 | 0/1000/0 |
| write_unchanged_1000 | 30 | 233.8 | 302.7 | 366.5 | 1.78 | 4.69 | 828532 | 100.0% | 1000 | 0/0/1000 |
| write_older_1000 | 30 | 237.3 | 275.0 | 298.4 | 4.99 | 8.89 | 733872 | — | 1000 | 0/0/1000 |
| write_mixed_1000 | 30 | 258.4 | 290.0 | 307.2 | 3.80 | 14.64 | 1187424 | 99.9% | 1000 | 334/333/333 |
| write_tie_1000 | 30 | 228.2 | 250.6 | 262.0 | 3.19 | 10.71 | 734336 | — | 1000 | 0/0/1000 |
| write_replay_1000 | 30 | 229.5 | 255.0 | 274.4 | 2.65 | 8.95 | 225188 | — | 0 | 0/0/1000 |
| write_insert_2000 | 30 | 473.5 | 521.3 | 535.7 | 6.90 | 34.67 | 3516648 | — | 2000 | 2000/0/0 |
| write_update_2000 | 30 | 476.2 | 514.4 | 530.8 | 2.14 | 4.64 | 1850236 | 99.2% | 2000 | 0/2000/0 |
| write_unchanged_2000 | 30 | 497.4 | 558.2 | 582.7 | 3.51 | 11.28 | 2060964 | 85.3% | 2000 | 0/0/2000 |
| write_older_2000 | 30 | 462.8 | 542.8 | 576.2 | 4.80 | 9.45 | 1497044 | — | 2000 | 0/0/2000 |
| write_mixed_2000 | 30 | 519.6 | 799.2 | 969.6 | 4.50 | 307.58 | 2416792 | 99.8% | 2000 | 667/667/666 |
| write_tie_2000 | 30 | 459.7 | 532.9 | 581.2 | 1.94 | 13.95 | 1512468 | — | 2000 | 0/0/2000 |
| write_replay_2000 | 30 | 469.8 | 565.6 | 583.4 | 0.78 | 2.56 | 449972 | — | 0 | 0/0/2000 |
## writes-passC.json: pase pass-C (253 s)
- contadores del pase: {'xact_commit': 8284, 'xact_rollback': 7, 'deadlocks': 0, 'conflicts': 0, 'temp_files': 0}
| caso | n | p50 ms | p95 ms | max ms | commit p50 | commit p95 | WAL p50 B | HOT fixtures | obs ins/muestra | created/updated/unchanged p50 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| write_insert_380 | 30 | 89.2 | 108.6 | 128.0 | 4.70 | 13.69 | 662888 | — | 380 | 380/0/0 |
| write_update_380 | 30 | 90.5 | 122.6 | 137.2 | 3.05 | 6.90 | 336576 | 100.0% | 380 | 0/380/0 |
| write_unchanged_380 | 30 | 92.6 | 113.8 | 131.0 | 2.95 | 10.67 | 308404 | 100.0% | 380 | 0/0/380 |
| write_older_380 | 30 | 84.8 | 101.6 | 156.0 | 2.96 | 10.04 | 269980 | — | 380 | 0/0/380 |
| write_mixed_380 | 30 | 98.0 | 115.9 | 129.9 | 3.97 | 7.07 | 437236 | 100.0% | 380 | 127/127/126 |
| write_tie_380 | 30 | 92.1 | 118.7 | 169.4 | 2.92 | 7.81 | 269252 | — | 380 | 0/0/380 |
| write_replay_380 | 30 | 88.7 | 116.4 | 126.6 | 2.48 | 12.60 | 84340 | — | 0 | 0/0/380 |
| write_insert_1000 | 30 | 243.1 | 274.0 | 385.8 | 5.57 | 15.06 | 1744348 | — | 1000 | 1000/0/0 |
| write_update_1000 | 30 | 245.6 | 303.2 | 324.9 | 2.92 | 7.11 | 893872 | 100.0% | 1000 | 0/1000/0 |
| write_unchanged_1000 | 30 | 232.4 | 301.9 | 350.6 | 4.06 | 7.40 | 821812 | 100.0% | 1000 | 0/0/1000 |
| write_older_1000 | 30 | 224.9 | 253.8 | 264.7 | 7.00 | 18.63 | 721792 | — | 1000 | 0/0/1000 |
| write_mixed_1000 | 30 | 256.0 | 317.8 | 319.8 | 3.42 | 27.95 | 1170912 | 99.8% | 1000 | 334/333/333 |
| write_tie_1000 | 30 | 227.2 | 255.3 | 299.5 | 1.49 | 3.04 | 718064 | — | 1000 | 0/0/1000 |
| write_replay_1000 | 30 | 215.0 | 259.3 | 263.6 | 0.90 | 1.32 | 224800 | — | 0 | 0/0/1000 |
| write_insert_2000 | 30 | 469.8 | 514.4 | 750.7 | 4.32 | 33.46 | 3522592 | — | 2000 | 2000/0/0 |
| write_update_2000 | 30 | 516.7 | 618.6 | 670.2 | 4.44 | 13.76 | 2200116 | 100.0% | 2000 | 0/2000/0 |
| write_unchanged_2000 | 30 | 478.3 | 544.3 | 569.7 | 4.46 | 22.43 | 1680012 | 100.0% | 2000 | 0/0/2000 |
| write_older_2000 | 30 | 456.5 | 535.3 | 593.6 | 1.29 | 3.56 | 1469816 | — | 2000 | 0/0/2000 |
| write_mixed_2000 | 30 | 518.6 | 612.6 | 872.4 | 2.43 | 12.31 | 2366280 | 99.9% | 2000 | 667/667/666 |
| write_tie_2000 | 30 | 473.8 | 654.7 | 698.9 | 3.34 | 7.88 | 1482048 | — | 2000 | 0/0/2000 |
| write_replay_2000 | 30 | 466.2 | 549.4 | 558.6 | 2.55 | 12.64 | 449268 | — | 0 | 0/0/2000 |
