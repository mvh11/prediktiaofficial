# M5.9B — Infraestructura local de predicción prospectiva

Solo local y simulada: ningún componente se conecta a producción ni a un scheduler, y no hay
predicciones reales. Diseño en M5.9A.

| Módulo | Qué hace |
| --- | --- |
| `canonical.py` | JSON canónico (claves ordenadas, UTC ISO-8601, sin NaN) y sha256 |
| `manifest.py` | Manifiesto sellado de B0/B1/B2 e inferencia en Python puro (solo `json.loads` + validación; sin pickle) |
| `freeze.py` | Crea manifiestos desde modelos ajustados de M5.8 (entorno de investigación) |
| `registry.py` | Registro JSONL append-only: `seq`, `prev_hash`, `record_hash`; detecta modificación, borrado, duplicado y reordenación; idempotente por `prediction_id` |
| `anchors.py` | Anclaje externo de la cabeza del registro y cobertura "anterior al kickoff" |
| `emitter.py` | Emisor con reloj inyectable: T = K − 1 h, H = emisión <= T, sin emisión tardía ni fallback |
| `evaluation.py` | Universo elegible, clases, denominadores, preregistro y evaluación confirmatoria única |

## Contrato temporal
- K y la identidad del partido: conocimiento estricto en H (instante de emisión). UNKNOWN/AMBIGUOUS
  → `no_emission`. T = K − 1 h; si la emisión (o su escritura) es posterior a T → `no_emission LATE`.
- Features: `team_recent_form_v1` con corte = horizonte = H (OPERATIONAL_STRICT).
- Identidad del objetivo no válida → `no_emission IDENTITY_INVALID`. Ventanas sin muestras o
  historia ambigua → `DEGRADED`: B0/B1 sí, B2 = null.
- El registro nunca contiene resultados; las etiquetas se unen solo en la evaluación.

## Anclaje externo (prueba de anterioridad)
Un commit local no prueba nada (su fecha la pone quien escribe). Antes de cada tanda de kickoffs se
publica `anchors.make_statement(registro)` = `{registry_seq, head_hash, statement_sha256}` ante una
autoridad cuyo instante no controla Prediktia:
- `rfc3161`: sello de tiempo de una TSA sobre `statement_sha256` (el token se guarda y se verifica
  con el certificado de la TSA);
- `github_server`: commit con la declaración empujado al remoto; vale la hora del servidor (evento
  de push o API), no la del commit;
- `project_thread`: la declaración publicada en el hilo del proyecto; vale la hora del mensaje.
Una predicción solo es evaluable si un anclaje verificado con `registry_seq >=` el suyo tiene
instante externo anterior al kickoff final. La verificación de cada autoridad se inyecta
(`external_check`); su implementación concreta es parte de M5.9C/M5.9D.
